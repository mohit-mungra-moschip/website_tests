"""
RegressionAI/agents/self_healing.py — Self-Healing Agent (Node 6)

Fully agentic in CI mode — no human approval.
Uses the existing QAOps code_fixer + file_writer logic but:
  1. Skips human_approval when ci_mode=True
  2. Routes ENV_ISSUE failures through self-healing for configuration/URL fixes
  3. Only re-runs failed tests (not the whole suite) by default
  4. For APP_HEAL: re-runs the FULL suite after healing to verify no regressions
  5. Reports healing_type (APP_HEAL | TEST_HEAL | MIXED | NONE) back to graph
  6. Reports healing success/failure back to graph

Uses: SELF_HEALING_MODEL (default: openrouter/openai/gpt-oss-120b:free)
"""
import os
import sys
import re
import json
import time
import threading
import subprocess
from pathlib import Path
from typing import List
from rich.console import Console
from rich.syntax import Syntax
from json_repair import repair_json

from common_utils import AIWrapper, LLMConfig
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, FileFix, EnvIssue
from RegressionAI.skills import load_prompt
from RegressionAI.agents.node_3_fetch_files import get_file_snippet

console = Console()
log = get_logger("self_healing")

from common_utils.llm_config import get_model_from_env
SELF_HEALING_MODEL = get_model_from_env("SELF_HEALING_MODEL")
SYSTEM_PROMPT = load_prompt("node_6_self_healing_prompt.md")


def sanitize_raw_json(raw_json: str) -> str:
    valid_keys = {"file_path", "step_by_step_trace", "target_content", "replacement_content", "fixed_content", "explanation"}
    end_pattern = re.compile(r'^"\s*(?:,\s*"(?:file_path|step_by_step_trace|target_content|replacement_content|fixed_content|explanation)"|\}|\],?)')
    
    result = []
    i = 0
    n = len(raw_json)
    
    while i < n:
        found_key = False
        for key in valid_keys:
            key_pattern = f'"{key}"'
            if raw_json[i:i+len(key_pattern)] == key_pattern:
                col_idx = raw_json.find(':', i + len(key_pattern))
                if col_idx != -1:
                    val_start_quote = raw_json.find('"', col_idx + 1)
                    if val_start_quote != -1 and val_start_quote < col_idx + 10:
                        result.append(raw_json[i:val_start_quote + 1])
                        val_idx = val_start_quote + 1
                        val_chars = []
                        while val_idx < n:
                            if raw_json[val_idx] == '"' and end_pattern.match(raw_json[val_idx:]):
                                break
                            val_chars.append(raw_json[val_idx])
                            val_idx += 1
                        
                        val_str = "".join(val_chars)
                        escaped_val = []
                        for idx, char in enumerate(val_str):
                            if char == '"':
                                if idx > 0 and val_str[idx - 1] == '\\':
                                    escaped_val.append(char)
                                else:
                                    escaped_val.append('\\"')
                            else:
                                escaped_val.append(char)
                        
                        result.append("".join(escaped_val))
                        i = val_idx
                        found_key = True
                        break
        
        if not found_key:
            result.append(raw_json[i])
            i += 1
            
    return "".join(result)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_test_file(path: str) -> bool:
    parts = Path(path).parts
    return (
        any(p in ("tests", "test_framework") for p in parts)
        or Path(path).name.startswith("test_")
    )


def _read_file_safe(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _fuzzy_replace(original: str, target_content: str, replacement_content: str) -> str:
    original_lines = original.splitlines()
    orig_non_empty = [(idx, line.strip()) for idx, line in enumerate(original_lines) if line.strip()]
    target_non_empty = [line.strip() for line in target_content.splitlines() if line.strip()]
    
    if not target_non_empty:
        return original
        
    n_orig = len(orig_non_empty)
    n_tar = len(target_non_empty)
    
    matched_idx = -1
    for i in range(n_orig - n_tar + 1):
        match = True
        for j in range(n_tar):
            if orig_non_empty[i + j][1] != target_non_empty[j]:
                match = False
                break
        if match:
            if matched_idx != -1:
                # Ambiguous match (found multiple times), return original to be safe
                return original
            matched_idx = i
            
    if matched_idx != -1:
        # We found a unique match!
        start_orig_idx = orig_non_empty[matched_idx][0]
        end_orig_idx = orig_non_empty[matched_idx + n_tar - 1][0]
        
        # Determine the indentation of the first matched line
        first_orig_line = original_lines[start_orig_idx]
        indentation = len(first_orig_line) - len(first_orig_line.lstrip())
        
        # Format replacement content with the same base indentation
        rep_lines = replacement_content.splitlines()
        
        # Adjust first line indentation if it's under-indented relative to the rest of the block
        if len(rep_lines) > 1:
            first_line = rep_lines[0]
            first_indent = len(first_line) - len(first_line.lstrip())
            if first_line.strip() and first_indent == 0:
                # Find min indentation of the rest of the non-empty lines
                rest_non_empty = [line for line in rep_lines[1:] if line.strip()]
                if rest_non_empty:
                    min_rest_indent = min(len(line) - len(line.lstrip()) for line in rest_non_empty)
                    if min_rest_indent >= 8:
                        starts_block = False
                        stripped_first = first_line.strip()
                        if stripped_first.endswith(":") or any(stripped_first.startswith(kw + " ") for kw in ["if", "for", "while", "def", "class", "with", "try"]):
                            starts_block = True
                        elif stripped_first == "try:":
                            starts_block = True
                        
                        target_first_indent = min_rest_indent - 4 if starts_block else min_rest_indent
                        if target_first_indent > 0:
                            rep_lines[0] = " " * target_first_indent + first_line

        # Find the minimum indentation of non-empty replacement lines to preserve relative indentation
        non_empty_rep = [line for line in rep_lines if line.strip() and not line.strip().startswith("#")]
        if non_empty_rep:
            min_rep_indent = min(len(line) - len(line.lstrip()) for line in non_empty_rep)
        else:
            min_rep_indent = min((len(line) - len(line.lstrip()) for line in rep_lines if line.strip()), default=0)
            
        formatted_rep_lines = []
        for line in rep_lines:
            if not line.strip():
                formatted_rep_lines.append("")
            else:
                line_indent = len(line) - len(line.lstrip())
                relative_indent = max(0, line_indent - min_rep_indent)
                formatted_rep_lines.append(" " * (indentation + relative_indent) + line.strip())
                
        # Reconstruct the file
        new_lines = original_lines[:start_orig_idx] + formatted_rep_lines + original_lines[end_orig_idx + 1:]
        return "\n".join(new_lines)
    return original


def _apply_fix(project_path: str, fix: dict) -> bool:
    """Write the fixed content or apply search-and-replace to disk."""
    file_path = fix.get("file_path")
    if not file_path:
        log.warning(f"No file_path provided in fix: {fix}")
        return False
        
    try:
        target = Path(project_path) / file_path
        target.parent.mkdir(parents=True, exist_ok=True)

        target_content      = fix.get("target_content")
        replacement_content = fix.get("replacement_content")
        fixed_content       = fix.get("fixed_content")

        if target_content is not None and replacement_content is not None:
            if not target.exists():
                log.warning(f"File {file_path} does not exist for search-and-replace.")
                return False
            original = target.read_text(encoding="utf-8")
            if target_content in original:
                target.write_text(original.replace(target_content, replacement_content), encoding="utf-8")
                return True
            # Normalized match
            norm_target   = target_content.replace("\r\n", "\n").strip()
            norm_original = original.replace("\r\n", "\n")
            if norm_target in norm_original:
                norm_replacement = replacement_content.replace("\r\n", "\n")
                target.write_text(norm_original.replace(norm_target, norm_replacement), encoding="utf-8")
                return True
            # Fuzzy match fallback
            fuzzy_result = _fuzzy_replace(original, target_content, replacement_content)
            if fuzzy_result != original:
                target.write_text(fuzzy_result, encoding="utf-8")
                log.info(f"Fuzzy search-and-replace succeeded for {file_path}.")
                return True
                
            log.warning(f"Surgical search-and-replace failed for {file_path}.")
            if fixed_content:
                log.info(f"Falling back to writing full fixed_content for {file_path}")
                target.write_text(fixed_content, encoding="utf-8")
                return True
            return False
        elif fixed_content is not None:
            target.write_text(fixed_content, encoding="utf-8")
            return True
        else:
            log.warning(f"No valid fix content in fix: {fix}")
            return False
    except Exception as exc:
        log.warning(f"Failed to apply fix to {file_path}: {exc}")
        return False


def _run_failed_tests(project_path: str, test_command: str, failures: list, use_test_name: bool = False) -> tuple:
    """Re-run only the specific failed test files or test names."""
    if use_test_name:
        targets = list(set(f.get("test_name", f.get("file_path", "")) for f in failures if f.get("test_name") or f.get("file_path")))
    else:
        targets = list(set(f.get("file_path", "") for f in failures if f.get("file_path")))

    if not targets:
        base_cmd = test_command
    else:
        parts    = test_command.split()
        base_cmd = f"{parts[0]} {' '.join(targets)} -v --tb=short --no-header"

    venv_path = os.path.join(project_path, ".venv", "bin", "activate")
    if os.path.exists(venv_path):
        full_cmd         = f"source {venv_path} && {base_cmd}"
        shell_executable = "/bin/bash"
    else:
        full_cmd         = base_cmd
        shell_executable = None

    run_env = os.environ.copy()
    if "REGRESSION_RUN_ID" in run_env:
        run_env["REGRESSION_RUN_ID"] = f"{run_env['REGRESSION_RUN_ID']}_healed"

    try:
        result = subprocess.run(
            full_cmd, shell=True, cwd=project_path,
            capture_output=True, text=True, timeout=120,
            executable=shell_executable, env=run_env,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except Exception as exc:
        return False, str(exc)
def _parse_failed_test_names_from_xml(project_path: str) -> set:
    """Parse XML test results to extract set of failing test names."""
    import xml.etree.ElementTree as ET
    import re
    xml_path = os.path.join(project_path, "logs/test-results.xml")
    failed_names = set()
    if not os.path.exists(xml_path):
        return failed_names
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for tc in root.findall(".//testcase"):
            failure_node = tc.find("failure")
            if failure_node is not None:
                traceback_text = failure_node.text or ""
                classname = tc.get("classname", "")
                name = tc.get("name", "")
                
                file_path = ""
                first_line = traceback_text.strip().splitlines()[0] if traceback_text.strip() else ""
                match = re.match(r"^([^:]+):(\d+):", first_line)
                if match:
                    file_path = match.group(1).strip()
                else:
                    parts = classname.split(".")
                    candidate = "/".join(parts) + ".py"
                    if parts and parts[-1] and parts[-1][0].isupper():
                        file_path = "/".join(parts[:-1]) + ".py"
                    else:
                        file_path = candidate
                    
                parts = classname.split(".")
                if parts[-1].startswith("Test"):
                    class_only = parts[-1]
                    test_name = f"{file_path}::{class_only}::{name}"
                else:
                    test_name = f"{file_path}::{name}"
                failed_names.add(test_name)
    except Exception as exc:
        log.warning(f"Failed to parse JUnit XML in verification: {exc}")
    return failed_names


def _run_full_suite(project_path: str, test_command: str) -> tuple:
    """Re-run the FULL test suite after an APP_HEAL to verify no regressions."""
    venv_path = os.path.join(project_path, ".venv", "bin", "activate")
    if os.path.exists(venv_path):
        full_cmd         = f"source {venv_path} && {test_command}"
        shell_executable = "/bin/bash"
    else:
        full_cmd         = test_command
        shell_executable = None

    run_env = os.environ.copy()
    if "REGRESSION_RUN_ID" in run_env:
        run_env["REGRESSION_RUN_ID"] = f"{run_env['REGRESSION_RUN_ID']}_full_rerun"

    try:
        result = subprocess.run(
            full_cmd, shell=True, cwd=project_path,
            capture_output=True, text=True, timeout=300,
            executable=shell_executable, env=run_env,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except Exception as exc:
        return False, str(exc)


def _call_llm_with_progress(func, *args, **kwargs):
    """Run LLM call directly."""
    return func(*args, **kwargs)


# ── Main node ─────────────────────────────────────────────────────────────────

def self_healing(state: AgentState) -> dict:
    """
    Self-Healing Agent — attempts to auto-fix failures and re-runs tests.
    In CI mode: fully agentic (no human approval prompt).

    Healing logic:
      - ENV_ISSUE: attempts to auto-fix via configuration/URL updates; if unsuccessful, surfaces them in env_issues.
      - FLAKY:     retries up to 3 times before attempting a code fix.
      - APP_BUG:   LLM fixes app code; after success, re-runs the FULL test suite
                   to verify no regressions introduced by the change.
      - TEST_BUG:  LLM fixes test code; after success, re-runs only the failing tests.
      - MIXED:     LLM fixes both; after success, re-runs only the failing tests.
    """
    failures       = state.get("failures", [])
    classifications = state.get("failure_classifications", [])
    project_path   = state.get("project_path", ".")
    test_command   = state.get("test_command", "pytest")
    ci_mode        = state.get("ci_mode", True)
    iteration      = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)

    console.print(f"\n[bold green]Self-Healing Agent[/bold green] — {SELF_HEALING_MODEL}")
    console.print(f"   CI Mode: {'Fully Agentic' if ci_mode else 'Human approval required'}")
    console.print(f"   Iteration: {iteration + 1}/{max_iterations}")

    if iteration >= max_iterations:
        console.print("   Max iterations reached. Self-healing failed.")
        return {
            "healing_successful": False,
            "status": "root_cause_analysis",
            "healing_type": "NONE",
            "env_issues": list(state.get("env_issues") or []),
        }

    if not failures:
        return {
            "healing_successful": True,
            "status": "done",
            "healing_type": "NONE",
            "env_issues": list(state.get("env_issues") or []),
        }

    # ── Step 1: Classify & separate ENV_ISSUE failures ───────────────────────
    cls_map = {c["test_name"]: c for c in classifications}

    env_failures      = [f for f in failures if cls_map.get(f.get("test_name", ""), {}).get("bug_type") == "ENV_ISSUE"]
    # We now allow ENV_ISSUE failures to be healable as they might be due to broken URLs/credentials in files
    healable_failures = list(failures)

    existing_env_issues = list(state.get("env_issues") or [])
    new_env_issues: list = []
    for ef in env_failures:
        cls = cls_map.get(ef.get("test_name", ""), {})
        new_env_issues.append(EnvIssue(
            test_name=ef.get("test_name", "unknown"),
            error_message=(ef.get("error_message") or "")[:500],
            remediation_hint=cls.get("reasoning", "Check environment configuration, dependencies, or service availability."),
        ))
    all_env_issues = existing_env_issues + new_env_issues

    if env_failures:
        console.print(f"   {len(env_failures)} ENV_ISSUE failure(s) detected — attempting to auto-heal by adjusting code config...")
        for ef in env_failures:
            console.print(f"      [cyan]{ef.get('test_name', 'unknown')[:70]}[/cyan]")

    failures = healable_failures

    # ── Step 2: Retry FLAKY failures first ──────────────────────────────────
    flaky_failures = [f for f in failures if cls_map.get(f.get("test_name", ""), {}).get("bug_type") == "FLAKY"]

    if flaky_failures:
        console.print(f"   Detected {len(flaky_failures)} FLAKY failure(s). Retrying them...")
        still_failing = []
        for f in flaky_failures:
            test_name = f.get("test_name", "unknown")
            passed    = False
            for attempt in range(3):
                console.print(f"      Retrying {test_name[:60]}... (attempt {attempt+1}/3)")
                passed, _ = _run_failed_tests(project_path, test_command, [f], use_test_name=True)
                if passed:
                    console.print(f"      Test passed on attempt {attempt+1}!")
                    break
                time.sleep(1)
            if not passed:
                console.print(f"      Consistently failing after 3 attempts.")
                still_failing.append(f)

        non_flaky = [f for f in failures if cls_map.get(f.get("test_name", ""), {}).get("bug_type") != "FLAKY"]
        failures  = non_flaky + still_failing

        if not failures:
            console.print("   All flaky failures resolved via retry!")
            return {
                "healing_successful": True,
                "healed_test_output": "All flaky failures resolved via retry",
                "proposed_fixes": [],
                "approved_fixes": [],
                "applied_fix_log": ["All flaky failures resolved via retry"],
                "iteration": iteration + 1,
                "status": "done",
                "healing_type": "NONE",
                "env_issues": all_env_issues,
            }

    # ── Step 3: Determine intended healing type & route model ────────────────
    relevant_files = state.get("relevant_files", {})
    cls_map        = {c["test_name"]: c for c in classifications}   # refresh after flaky removal

    bug_types    = {cls_map.get(f.get("test_name", ""), {}).get("bug_type", "APP_BUG") for f in failures}
    has_app_bug  = "APP_BUG" in bug_types

    if has_app_bug:
        intended_healing_type = "APP_HEAL"
        model_to_use = os.getenv("ADVANCED_HEALING_MODEL", "groq/llama-3.3-70b-versatile").strip()
        console.print(f"   [bold magenta]Escalating to advanced model: {model_to_use}[/bold magenta] (APP_BUG detected)")
    else:
        intended_healing_type = "TEST_HEAL"
        model_to_use = SELF_HEALING_MODEL
        console.print(f"   Using model: {model_to_use} (no APP_BUG detected)")

    console.print(f"   Intended healing type: [bold yellow]{intended_healing_type}[/bold yellow]")

    # ── Step 4: Internal Try-Verify-Adjust Sandbox Loop ──────────────────────
    internal_max_attempts = 3
    internal_attempt = 0
    healing_successful = False
    final_proposed_fixes = []
    final_test_output = ""
    final_healing_type = intended_healing_type
    applied_logs = list(state.get("applied_fix_log", []))
    internal_failures_history = []

    # Prepare base contexts for failures and files
    failures_context = ""
    for f in failures[:8]:
        cls = cls_map.get(f.get("test_name", ""), {})
        failures_context += (
            f"\n--- FAILURE: {f.get('test_name')} ---\n"
            f"Type: {cls.get('bug_type', 'unknown')}\n"
            f"Error: {f.get('error_type') or ''}: {(f.get('error_message') or '')[:500]}\n"
            f"Traceback:\n{(f.get('traceback') or '')[:1500]}\n"
        )

    has_env_issue = "ENV_ISSUE" in bug_types
    if intended_healing_type == "APP_HEAL":
        type_instructions = """- You are performing an APPLICATION FIX (APP_HEAL). You must modify ONLY the application code or package dependency files (e.g., requirements.txt, pyproject.toml) to make the original tests pass.
- Do NOT modify any test files. Do NOT propose any changes to files in tests/ or test_framework/ folders.
- Do NOT delete unrelated fields, properties, schemas, or helper methods in the application files."""
    else:
        type_instructions = """- You are performing a TEST/CONFIG/ENV FIX (TEST_HEAL). You must only update test assertions, expected status codes, database connection URLs/strings, environment configuration variables, API endpoints, or project dependencies (e.g., requirements.txt, pyproject.toml) to align with correct behavior.
- Do NOT modify any application logic code (e.g., in app/ folder)."""
        if has_env_issue:
            type_instructions += """
- CRITICAL: An environment/infrastructure issue (ENV_ISSUE) has been detected (e.g., broken connection strings, nonexistent hosts, database timeouts). Prioritize correcting database URLs, API hostnames, port configurations, or credentials in the test files to point back to stable local/in-memory endpoints (like 'sqlite+aiosqlite:///./test_tasks.db' or local mock services)."""

    while internal_attempt < internal_max_attempts:
        console.print(f"\n   [bold blue]Sandbox Loop: Internal Attempt {internal_attempt + 1}/{internal_max_attempts}[/bold blue]")

        # Re-read files to ensure we have current clean workspace content
        files_context = ""
        filtered_files = {}
        for path, content in relevant_files.items():
            if intended_healing_type == "TEST_HEAL" and not _is_test_file(path) and Path(path).name not in ("requirements.txt", "pyproject.toml", "setup.py"):
                continue
            # Read fresh from disk to avoid stale data (using snippet logic to stay within token limits)
            disk_content = get_file_snippet(Path(project_path), path, failures)
            filtered_files[path] = disk_content if disk_content else content

        # Prioritize files needed by the failures we are displaying
        needed_normalized = set()
        import re
        for f in failures[:8]:
            if f.get("file_path"):
                needed_normalized.add(Path(f["file_path"]).as_posix().lower())
            tb = f.get("traceback") or ""
            for m in re.findall(r"([\w/\\.-]+\.py)", tb):
                if not any(part in m for part in (".venv", "site-packages", "Python.framework")):
                    needed_normalized.add(Path(m).as_posix().lower())

        # Sort filtered_files: prioritized first, then others
        sorted_files = []
        for path, content in filtered_files.items():
            path_lower = Path(path).as_posix().lower()
            is_priority = any(needed in path_lower or path_lower in needed for needed in needed_normalized)
            sorted_files.append((is_priority, path, content))
        
        sorted_files.sort(key=lambda x: x[0], reverse=True)

        for _, path, content in sorted_files[:10]:
            if intended_healing_type == "APP_HEAL" and _is_test_file(path):
                files_context += f"\n=== [READ-ONLY REFERENCE] {path} ===\n{content}\n"
            else:
                files_context += f"\n=== {path} ===\n{content}\n"

        failed_attempts_context = ""
        if internal_failures_history:
            failed_attempts_context = "\n### REINFORCEMENT FEEDBACK FROM PREVIOUS FAILED INTERNAL ATTEMPTS:\n"
            for i, attempt_err in enumerate(internal_failures_history, 1):
                failed_attempts_context += f"\n--- Failed Internal Attempt #{i} ---\n{attempt_err}\n"

        # Load project-specific instructions if available
        project_rules_path = Path(project_path) / "project_rules.md"
        project_rules_inst = ""
        if project_rules_path.exists():
            try:
                project_rules_inst = f"\n### PROJECT-SPECIFIC RULES:\n{project_rules_path.read_text(encoding='utf-8').strip()}\n"
            except Exception as e:
                log.warning(f"Could not read project_rules.md: {e}")
        else:
            # Default fallback for this default project if not specified
            project_rules_inst = """
### PROJECT-SPECIFIC RULES:
- Do NOT delete or rename existing pytest fixtures (such as 'client').
- The test functions must accept 'client' as an argument, NOT 'async_client'.
- Keep all existing properties, schemas, imports, and helper functions intact.
"""

        prompt = f"""Fix these test failures.

### IMPORTANT INSTRUCTIONS:
{type_instructions}
- Provide targeted, minimal fixes that restore correct behavior while preserving the original design and completeness of the code.
{project_rules_inst}
Failure details:
{failures_context}

Relevant source files:
{files_context}
{failed_attempts_context}
Return fixes as JSON array. For TEST_BUG: fix the test file. For APP_BUG: fix the source file."""

        # Determine candidate models to try for this attempt
        candidate_models = []
        fallback_model = os.getenv("FALLBACK_MODEL", "").strip()
        if not fallback_model and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            fallback_model = "google/gemini-2.5-flash"

        if intended_healing_type == "APP_HEAL":
            advanced_env = os.getenv("ADVANCED_HEALING_MODEL", "").strip()
            if not advanced_env:
                advanced_env = os.getenv("DEFAULT_MODEL", "groq/llama-3.3-70b-versatile").strip()
            candidate_models.append(advanced_env)
        else:
            healing_env = os.getenv("SELF_HEALING_MODEL", "").strip()
            if not healing_env:
                healing_env = os.getenv("DEFAULT_MODEL", "groq/llama-3.3-70b-versatile").strip()
            candidate_models.append(healing_env)

        if fallback_model and fallback_model not in candidate_models:
            candidate_models.append(fallback_model)

        attempt_succeeded = False
        for model_candidate in candidate_models:
            raw = None
            fixes_raw = None
            try:
                console.print(f"   Calling model: {model_candidate}...")
                ai = AIWrapper(LLMConfig(model=model_candidate, temperature=0.1), mode="llm")
                raw = _call_llm_with_progress(ai.run, prompt, system_prompt=SYSTEM_PROMPT)
                raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```")
                log.info(f"Self-healing raw LLM response ({model_candidate}): {raw}")
                
                try:
                    # Sanitize raw JSON first to escape unescaped double-quotes in Python code strings
                    sanitized_raw = sanitize_raw_json(raw)
                    fixes_raw = json.loads(sanitized_raw)
                except Exception:
                    try:
                        fixes_raw = json.loads(raw)
                    except Exception:
                        fixes_raw = repair_json(raw, return_objects=True)
                
                if not isinstance(fixes_raw, list) or not fixes_raw:
                    console.print(f"   No valid fixes generated by {model_candidate}.")
                    continue
            except Exception as model_exc:
                console.print(f"   Model {model_candidate} failed: {model_exc}")
                continue

            # Auto-correct file paths if LLM specifies wrong path but unique target_content match is found
            for fix in fixes_raw:
                if not isinstance(fix, dict):
                    continue
                target_content = fix.get("target_content")
                file_path = fix.get("file_path", "")
                if target_content and file_path:
                    file_path = file_path.replace("\\", "/")
                    full_p = Path(project_path) / file_path
                    in_current = False
                    if full_p.exists():
                        try:
                            content = full_p.read_text(encoding="utf-8")
                            if target_content in content or target_content.replace("\r\n", "\n").strip() in content.replace("\r\n", "\n"):
                                in_current = True
                        except Exception:
                            pass
                    
                    if not in_current:
                        found_path = None
                        for path in relevant_files.keys():
                            if path == file_path:
                                continue
                            test_p = Path(project_path) / path
                            if test_p.exists():
                                try:
                                    content = test_p.read_text(encoding="utf-8")
                                    if target_content in content or target_content.replace("\r\n", "\n").strip() in content.replace("\r\n", "\n"):
                                        if found_path is not None:
                                            found_path = None
                                            break
                                        found_path = path
                                except Exception:
                                    pass
                        if found_path:
                            console.print(f"   Auto-correcting wrong LLM file path from '{file_path}' to '{found_path}' (target_content matches)")
                            fix["file_path"] = found_path

            # Keep backups of files we are going to modify
            backups = {}
            for fix in fixes_raw:
                if not isinstance(fix, dict):
                    continue
                file_path = fix.get("file_path", "")
                if not file_path:
                    continue
                full_path = Path(project_path) / file_path
                if full_path.exists():
                    backups[file_path] = full_path.read_text(encoding="utf-8")
                else:
                    backups[file_path] = None

            # Apply candidate fixes
            current_attempt_proposed = []
            apply_failed = False
            apply_error_msg = ""

            for fix in fixes_raw:
                if not isinstance(fix, dict):
                    continue
                file_path = fix.get("file_path", "")
                if not file_path:
                    continue

                console.print(f"   Applying candidate fix to: [cyan]{file_path}[/cyan]")
                if _apply_fix(project_path, fix):
                    full_path = Path(project_path) / file_path
                    if full_path.suffix == ".py":
                        try:
                            import py_compile
                            py_compile.compile(str(full_path), doraise=True)
                        except py_compile.PyCompileError as err:
                            console.print(f"   Syntax error detected in healed file {file_path}:\n{err.msg}")
                            apply_failed = True
                            apply_error_msg = f"Syntax error in {file_path} after applying fix:\n{err.msg}"
                            break

                    import difflib
                    updated_content = _read_file_safe(full_path)
                    original_content = backups.get(file_path) or ""
                    diff_str = "".join(difflib.unified_diff(
                        original_content.splitlines(keepends=True),
                        updated_content.splitlines(keepends=True),
                        fromfile=f"a/{file_path}",
                        tofile=f"b/{file_path}"
                    ))
                    current_attempt_proposed.append(FileFix(
                        file_path=file_path,
                        original_content=original_content,
                        fixed_content=updated_content,
                        explanation=fix.get("explanation", ""),
                        diff=diff_str,
                    ))
                else:
                    apply_failed = True
                    apply_error_msg = f"Failed to apply search-and-replace to {file_path} (target_content did not match original code)."
                    break

            if apply_failed or not current_attempt_proposed:
                # Revert backups immediately
                for fp, content in backups.items():
                    full_p = Path(project_path) / fp
                    if content is None:
                        if full_p.exists():
                            full_p.unlink()
                    else:
                        full_p.write_text(content, encoding="utf-8")

                err_msg = apply_error_msg or "No fixes applied."
                console.print(f"   Candidate fix application failed: {err_msg}")
                internal_failures_history.append(f"Model {model_candidate} fix application failed: {err_msg}")
                continue

            # Determine actual healing type for candidate
            fixed_paths   = [p.get("file_path", "") for p in current_attempt_proposed]
            touched_app   = any(not _is_test_file(fp) for fp in fixed_paths)
            touched_tests = any(_is_test_file(fp)     for fp in fixed_paths)

            if touched_app and touched_tests:
                candidate_healing_type = "MIXED"
            elif touched_app:
                candidate_healing_type = "APP_HEAL"
            elif touched_tests:
                candidate_healing_type = "TEST_HEAL"
            else:
                candidate_healing_type = intended_healing_type

            # Auto-install packages if dependency configuration was modified
            dep_files = {"requirements.txt", "pyproject.toml", "setup.py"}
            modified_dep_files = {Path(fix.get("file_path", "")).name for fix in current_attempt_proposed}
            if dep_files.intersection(modified_dep_files):
                console.print("   [cyan]Detected dependency configuration change — installing packages in sandbox...[/cyan]")
                venv_activate = os.path.join(project_path, ".venv", "bin", "activate")
                if os.path.exists(venv_activate):
                    install_cmds = []
                    for fix in current_attempt_proposed:
                        fpath = fix.get("file_path", "")
                        fname = Path(fpath).name
                        if fname == "requirements.txt":
                            install_cmds.append(f"pip install -r {fpath}")
                        elif fname in ("setup.py", "pyproject.toml"):
                            folder = str(Path(fpath).parent)
                            install_cmds.append(f"pip install -e {folder}")
                    install_cmd = f"source {venv_activate} && " + " && ".join(install_cmds)
                    install_exec = "/bin/bash"
                else:
                    install_cmds = []
                    for fix in current_attempt_proposed:
                        fpath = fix.get("file_path", "")
                        fname = Path(fpath).name
                        if fname == "requirements.txt":
                            install_cmds.append(f"pip install -r {fpath}")
                        elif fname in ("setup.py", "pyproject.toml"):
                            folder = str(Path(fpath).parent)
                            install_cmds.append(f"pip install -e {folder}")
                    install_cmd = " && ".join(install_cmds)
                    install_exec = None
                try:
                    res = subprocess.run(
                        install_cmd, shell=True, cwd=project_path,
                        capture_output=True, text=True, timeout=120,
                        executable=install_exec
                    )
                    if res.returncode == 0:
                        console.print("   [green]Dependency installation successful![/green]")
                    else:
                        console.print(f"   [red]Dependency installation failed: {res.stdout + res.stderr}[/red]")
                except Exception as inst_err:
                    console.print(f"   [red]Failed to run package installation: {inst_err}[/red]")

            # Re-run tests in sandbox
            passed = False
            test_output = ""
            if candidate_healing_type == "APP_HEAL":
                console.print("   APP_HEAL candidate — running FULL test suite in sandbox to verify no regressions...")
                xml_path = os.path.join(project_path, "logs/test-results.xml")
                original_xml_content = None
                if os.path.exists(xml_path):
                    try:
                        with open(xml_path, "r", encoding="utf-8") as f:
                            original_xml_content = f.read()
                    except Exception as xml_err:
                        log.warning(f"Could not backup xml file: {xml_err}")

                raw_passed, test_output = _run_full_suite(project_path, test_command)
                
                if raw_passed:
                    passed = True
                else:
                    original_failed_names = {f.get("test_name") for f in failures if f.get("test_name")}
                    targeted_names = {f.get("test_name") for f in failures[:8] if f.get("test_name")}
                    rerun_failed_names = _parse_failed_test_names_from_xml(project_path)
                    
                    log.info(f"Self-healing verification: original={original_failed_names}, targeted={targeted_names}, rerun={rerun_failed_names}")
                    
                    if targeted_names and not targeted_names.intersection(rerun_failed_names) and not (rerun_failed_names - original_failed_names):
                        console.print("   Targeted failures resolved, and no new regressions detected. Verification PASSED.")
                        passed = True
                    else:
                        passed = False
                        if original_xml_content is not None:
                            try:
                                with open(xml_path, "w", encoding="utf-8") as f:
                                    f.write(original_xml_content)
                            except Exception as xml_err:
                                log.warning(f"Could not restore backup xml file: {xml_err}")
            else:
                console.print("   TEST_HEAL candidate — running failed tests in sandbox...")
                passed, test_output = _run_failed_tests(project_path, test_command, failures)

            if passed:
                # Success! Keep changes on disk, print git diff
                healing_successful = True
                final_proposed_fixes = current_attempt_proposed
                final_test_output = test_output
                final_healing_type = candidate_healing_type

                for fix in current_attempt_proposed:
                    fp = fix.get("file_path", "")
                    try:
                        diff_res = subprocess.run(
                            ["git", "diff", "--", fp],
                            cwd=project_path, capture_output=True, text=True,
                        )
                        if diff_res.stdout.strip():
                            console.print("\n[bold yellow]   Git Diff of Applied Fix:[/bold yellow]")
                            console.print(Syntax(diff_res.stdout, "diff", theme="monokai", line_numbers=True))
                            console.print("[bold yellow]   ---------------------------------------[/bold yellow]\n")
                    except Exception as e:
                        log.warning(f"Could not print git diff for {fp}: {e}")

                applied_logs.append(f"Iter {iteration+1} (Internal Attempt {internal_attempt+1}): Successfully healed {len(current_attempt_proposed)} file(s) using {model_candidate}.")
                attempt_succeeded = True
                break
            else:
                # Revert backups immediately
                for fp, content in backups.items():
                    full_p = Path(project_path) / fp
                    if content is None:
                        if full_p.exists():
                            full_p.unlink()
                    else:
                        full_p.write_text(content, encoding="utf-8")

                console.print(f"   Candidate fix failed verification tests.")
                internal_failures_history.append(
                    f"Model {model_candidate} (Attempt #{internal_attempt+1}) code changes failed verification. Output:\n{test_output[:1000]}"
                )

        if attempt_succeeded:
            break
        else:
            applied_logs.append(f"Iter {iteration+1} (Internal Attempt {internal_attempt+1}): Failed all models.")
            internal_attempt += 1

    if healing_successful:
        console.print(f"   [bold green]All tests PASS! Self-healing ({final_healing_type}) successful after {internal_attempt + 1} attempts.[/bold green]")
        
        # Filter out successfully healed env issues from the returned env_issues list
        healed_test_names = {f.get("test_name") for f in failures}
        remaining_env_issues = [ei for ei in all_env_issues if ei.get("test_name") not in healed_test_names]
        
        return {
            "healing_successful": True,
            "healed_test_output": final_test_output,
            "proposed_fixes":     final_proposed_fixes,
            "approved_fixes":     final_proposed_fixes,
            "applied_fix_log":    applied_logs,
            "iteration":          iteration + 1,
            "status":             "done",
            "healing_type":       final_healing_type,
            "env_issues":         remaining_env_issues,
        }
    else:
        console.print(f"   All {internal_max_attempts} internal self-healing attempts failed.")
        return {
            "healing_successful": False,
            "healed_test_output": final_test_output or "All internal healing attempts failed verification",
            "proposed_fixes":     [],
            "applied_fix_log":    applied_logs,
            "iteration":          iteration + 1,
            "status":             "root_cause_analysis",
            "healing_type":       final_healing_type,
            "env_issues":         all_env_issues,
        }
