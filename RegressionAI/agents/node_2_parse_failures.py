"""
RegressionAI/agents/parse_failures.py — Parse pytest output into structured TestFailure objects.
Adapted from QAOps/agents/failure_parser.py for the RegressionAI pipeline.

Uses: FAILURE_PARSER_MODEL env var (existing, unchanged).
"""
import json
import os
import re
from typing import List
from json_repair import repair_json
from rich.console import Console
from common_utils import AIWrapper, LLMConfig
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, TestFailure

from RegressionAI.skills import load_prompt

console = Console()
log = get_logger("parse_failures")

from common_utils.llm_config import get_model_from_env
FAILURE_PARSER_MODEL = get_model_from_env("FAILURE_PARSER_MODEL")

SYSTEM_PROMPT = load_prompt("node_2_parse_failures_prompt.md")


def _regex_fallback(output: str) -> List[TestFailure]:
    failures = []
    # Match "FAILED tests/foo.py::test_bar - AssertionError"
    for m in re.finditer(r"(?:FAILED|FAIL)\s+([\w/\\.:\-]+)(?:\s+-\s+(.+))?", output):
        full_id, msg = m.groups()
        fp = full_id.split("::")[0]
        failures.append(TestFailure(
            test_name=full_id, file_path=fp, source_files=[],
            line_number=None, error_type="AssertionError",
            error_message=msg or "Assertion failed", traceback="",
        ))
    # Match "tests/foo.py::test_bar FAILED"
    for m in re.finditer(r"([\w/\\.:\-]+)\s+(?:FAILED|FAIL)", output):
        full_id = m.group(1)
        # Avoid duplicate matches
        if not any(f["test_name"] == full_id for f in failures):
            fp = full_id.split("::")[0]
            failures.append(TestFailure(
                test_name=full_id, file_path=fp, source_files=[],
                line_number=None, error_type="AssertionError",
                error_message="Assertion failed", traceback="",
            ))
    return failures


def parse_failures(state: AgentState) -> dict:
    if state.get("test_passed"):
        return {"failures": [], "status": "done"}

    project_path = state.get("project_path", ".")
    from pathlib import Path
    import xml.etree.ElementTree as ET

    console.print(f"\n[bold blue]Parse Failures[/bold blue] — JUnit XML & {FAILURE_PARSER_MODEL}")

    # 1. Try to parse from JUnit XML for 100% accuracy and 0 token cost
    xml_path = Path(project_path) / "logs/test-results.xml"
    if xml_path.exists():
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            xml_failures = []
            for tc in root.findall(".//testcase"):
                failure_node = tc.find("failure")
                if failure_node is not None:
                    traceback_text = failure_node.text or ""
                    message_text = failure_node.get("message", "")
                    classname = tc.get("classname", "")
                    name = tc.get("name", "")
                    
                    file_path = ""
                    line_number = None
                    
                    first_line = traceback_text.strip().splitlines()[0] if traceback_text.strip() else ""
                    match = re.match(r"^([^:]+):(\d+):", first_line)
                    if match:
                        file_path = match.group(1).strip()
                        line_number = int(match.group(2))
                    else:
                        parts = classname.split(".")
                        candidate = "/".join(parts) + ".py"
                        if not os.path.exists(candidate) and parts and parts[-1] and parts[-1][0].isupper():
                            file_path = "/".join(parts[:-1]) + ".py"
                        else:
                            file_path = candidate
                        
                    # Format test_name cleanly like pytest does
                    parts = classname.split(".")
                    if parts[-1].startswith("Test"):
                        class_only = parts[-1]
                        test_name = f"{file_path}::{class_only}::{name}"
                    else:
                        test_name = f"{file_path}::{name}"
                        
                    error_type = "AssertionError"
                    if "ValidationError" in message_text or "ValidationError" in traceback_text:
                        error_type = "ValidationError"
                    elif "KeyError" in message_text or "KeyError" in traceback_text:
                        error_type = "KeyError"
                    elif "TypeError" in message_text or "TypeError" in traceback_text:
                        error_type = "TypeError"
                        
                    xml_failures.append({
                        "test_name": test_name,
                        "file_path": file_path,
                        "source_files": [],
                        "line_number": line_number,
                        "error_type": error_type,
                        "error_message": message_text,
                        "traceback": traceback_text,
                    })
            if xml_failures:
                console.print(f"   Parsed {len(xml_failures)} failure(s) from JUnit XML")
                return {"failures": xml_failures, "status": "analyzing"}
        except Exception as exc:
            log.warning(f"Failed to parse JUnit XML: {exc}")

    # 2. LLM Fallback if JUnit XML parser failed/missing
    output = state.get("test_output", "")
    try:
        # Pass the tail of the output (where pytest prints failure tracebacks)
        tail_output = output[-15000:] if len(output) > 15000 else output
        ai = AIWrapper(LLMConfig(model=FAILURE_PARSER_MODEL, temperature=0.0), mode="llm")
        raw = ai.run(prompt=f"Pytest output:\n\n{tail_output}", system_prompt=SYSTEM_PROMPT)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```")
        try:
            failures = json.loads(raw)
        except Exception:
            failures = repair_json(raw, return_objects=True)
        
        # Normalize to flat list of dicts
        normalized_failures = []
        if isinstance(failures, list):
            for item in failures:
                if isinstance(item, list):
                    for subitem in item:
                        if isinstance(subitem, dict):
                            normalized_failures.append(subitem)
                elif isinstance(item, dict):
                    normalized_failures.append(item)
        elif isinstance(failures, dict):
            normalized_failures.append(failures)

        if normalized_failures:
            console.print(f"   Parsed {len(normalized_failures)} failure(s) via LLM")
            return {"failures": normalized_failures, "status": "analyzing"}
    except Exception as exc:
        log.warning(f"LLM parse failed: {exc}")

    # 3. Regex Fallback
    failures = _regex_fallback(output)
    console.print(f"   Regex fallback: {len(failures)} failure(s)")
    return {"failures": failures, "status": "analyzing"}
