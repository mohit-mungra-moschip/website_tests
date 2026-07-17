"""
RegressionAI/agents/failure_analysis.py — AI Failure Analysis Agent

Node 4 in the pipeline. Classifies each test failure as:
  - TEST_BUG:  The test script itself has wrong code/assertions
  - APP_BUG:   The application code is broken
  - ENV_ISSUE: Missing dependency, config, or environment problem
  - FLAKY:     Non-deterministic / timing-dependent failure

Uses: FAILURE_ANALYSIS_MODEL (default: google/gemini-2.5-flash)
Returns: failure_classifications + overall_confidence
"""
import json
import os
import sys
import time
import threading
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console

from common_utils import AIWrapper, LLMConfig
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, FailureClassification
from RegressionAI.skills import load_prompt

console = Console()
log = get_logger("failure_analysis")

from common_utils.llm_config import get_model_from_env
FAILURE_ANALYSIS_MODEL = get_model_from_env("FAILURE_ANALYSIS_MODEL")
SYSTEM_PROMPT = load_prompt("node_4_failure_analysis_prompt.md")


def _classify_single_failure(failure: dict) -> FailureClassification:
    """Call LLM to classify a single test failure (used as fallback)."""
    ai = AIWrapper(LLMConfig(model=FAILURE_ANALYSIS_MODEL, temperature=0.1), mode="llm")

    context = (
        f"Test: {failure.get('test_name', 'unknown')}\n"
        f"File: {failure.get('file_path', 'unknown')}\n"
        f"Error type: {failure.get('error_type', 'unknown')}\n"
        f"Error message: {failure.get('error_message', '')}\n"
        f"Traceback:\n{(failure.get('traceback') or '')[:2000]}"
    )

    try:
        raw = ai.run(
            prompt=f"Classify this test failure:\n\n{context}\n\nRespond ONLY with valid JSON (keys: bug_type, confidence, reasoning). No markdown formatting.",
            system_prompt=SYSTEM_PROMPT
        )
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        return FailureClassification(
            test_name=failure.get("test_name", "unknown"),
            bug_type=parsed.get("bug_type", "APP_BUG"),
            confidence=int(parsed.get("confidence", 50)),
            reasoning=parsed.get("reasoning", ""),
        )
    except Exception as exc:
        log.warning(f"Classification failed for {failure.get('test_name')}: {exc}")
        return FailureClassification(
            test_name=failure.get("test_name", "unknown"),
            bug_type="APP_BUG",
            confidence=30,
            reasoning=f"Classification failed: {str(exc)[:100]}",
        )


def call_llm_with_progress(func, *args, **kwargs):
    """Run LLM invocation directly."""
    return func(*args, **kwargs)


def _classify_failures_single_chunk(failures: list) -> list:
    """Call LLM to classify a chunk of test failures."""
    ai = AIWrapper(LLMConfig(model=FAILURE_ANALYSIS_MODEL, temperature=0.1), mode="llm")

    failures_list = []
    for i, f in enumerate(failures):
        failures_list.append({
            "index": i,
            "test_name": f.get("test_name", "unknown"),
            "file_path": f.get("file_path", "unknown"),
            "error_type": f.get("error_type", "unknown"),
            "error_message": f.get("error_message", ""),
            "traceback": (f.get("traceback") or "")[:1200]
        })

    prompt_content = f"""Analyze the following list of test failures.

For EACH failure, classify it as exactly one of:
- TEST_BUG (test code itself is wrong/outdated)
- APP_BUG (application/production code is broken)
- ENV_ISSUE (environment/infra setup problem)
- FLAKY (non-deterministic timing/race)

Respond ONLY with a JSON array of classifications matching the test failures provided:
[
  {{
    "test_name": "name of the test",
    "bug_type": "TEST_BUG|APP_BUG|ENV_ISSUE|FLAKY",
    "confidence": 90,
    "reasoning": "One clear sentence explaining why."
  }}
]

Do not use markdown code block wrappers (like ```json). Return ONLY the raw JSON array.

Test Failures to Classify:
{json.dumps(failures_list, indent=2)}"""

    raw = ai.run(
        prompt=prompt_content,
        system_prompt=SYSTEM_PROMPT
    )
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    
    from json_repair import repair_json
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = repair_json(raw, return_objects=True)
        
    if not isinstance(parsed, list):
        raise ValueError("LLM did not return a JSON array of classifications")
        
    classifications = []
    for idx, f in enumerate(failures):
        t_name = f.get("test_name", "unknown")
        match_item = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("test_name") == t_name:
                    match_item = item
                    break
            # Fallback to index if name not matched
            if not match_item and idx < len(parsed):
                item = parsed[idx]
                if isinstance(item, dict):
                    match_item = item
        
        if match_item:
            classifications.append(FailureClassification(
                test_name=t_name,
                bug_type=match_item.get("bug_type", "APP_BUG"),
                confidence=int(match_item.get("confidence", 50)),
                reasoning=match_item.get("reasoning", "Parsed from batch"),
            ))
        else:
            classifications.append(FailureClassification(
                test_name=t_name,
                bug_type="APP_BUG",
                confidence=40,
                reasoning="Failed to match in batch response",
            ))
            
    return classifications


def _classify_failures_batch(failures: list) -> list:
    """Call LLM to classify all test failures in small batches of max 5 to prevent token limits."""
    chunk_size = 5
    all_classifications = []
    for i in range(0, len(failures), chunk_size):
        chunk = failures[i:i+chunk_size]
        chunk_classifications = _classify_failures_single_chunk(chunk)
        all_classifications.extend(chunk_classifications)
    return all_classifications


def _classify_failures_parallel(failures: list) -> list:
    """Fall back to parallel classification using ThreadPoolExecutor."""
    class_map = {f.get("test_name", "unknown"): None for f in failures}
    
    console.print(f"   Batch analysis failed. Falling back to parallel individual classifications...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_failure = {
            executor.submit(_classify_single_failure, f): f for f in failures
        }
        
        completed_count = 0
        for future in as_completed(future_to_failure):
            f = future_to_failure[future]
            t_name = f.get("test_name", "unknown")
            try:
                classification = future.result()
                class_map[t_name] = classification
                console.print(
                    f"     [{completed_count+1}/{len(failures)}] [bold]{classification['bug_type']}[/bold] "
                    f"for {t_name[:60]}... ({classification['confidence']}%)"
                )
            except Exception as e:
                console.print(f"     Failed to classify {t_name}: {e}")
                class_map[t_name] = FailureClassification(
                    test_name=t_name,
                    bug_type="APP_BUG",
                    confidence=30,
                    reasoning=f"Parallel analysis error: {str(e)[:50]}",
                )
            completed_count += 1
            
    return [class_map[f.get("test_name", "unknown")] for f in failures]


def failure_analysis(state: AgentState) -> dict:
    """
    AI Failure Analysis Node — classifies each failure as TEST_BUG / APP_BUG / ENV_ISSUE / FLAKY.
    Outputs confidence scores per failure and an overall aggregate score.
    """
    failures = state.get("failures", [])
    if not failures:
        return {
            "failure_classifications": [],
            "overall_confidence": 100,
            "status": "analyzing",
        }

    console.print(f"\n[bold blue]Failure Analysis Agent[/bold blue] — {FAILURE_ANALYSIS_MODEL}")
    console.print(f"   Classifying {len(failures)} failure(s)...")

    try:
        classifications = call_llm_with_progress(_classify_failures_batch, failures)
        # Print results nicely
        for classification in classifications:
            console.print(
                f"     [bold]{classification['bug_type']}[/bold] "
                f"for [cyan]{classification['test_name'][:70]}[/cyan] "
                f"(confidence: {classification['confidence']}%) — {classification['reasoning'][:80]}"
            )
    except Exception as exc:
        log.warning(f"Batch classification failed: {exc}")
        classifications = _classify_failures_parallel(failures)

    # Aggregate confidence: average of all individual scores
    overall = int(sum(c["confidence"] for c in classifications) / len(classifications)) if classifications else 0

    console.print(f"\n   Analysis complete. Overall confidence: [bold green]{overall}%[/bold green]")

    return {
        "failure_classifications": classifications,
        "overall_confidence": overall,
        "status": "self_healing",
    }
