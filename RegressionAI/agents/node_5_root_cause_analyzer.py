"""
RegressionAI/agents/root_cause_analyzer.py — Root Cause Commit Analysis Agent

Node 6: Triggered when self-healing FAILS.
Uses git blame + git log + LLM to identify:
  - Which commit introduced the breaking change
  - Who authored it
  - What changed
  - Likely root cause

Uses: ROOT_CAUSE_MODEL (default: groq/llama-3.3-70b-versatile)
"""
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

from common_utils import AIWrapper, LLMConfig
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, RootCauseResult

from RegressionAI.skills import load_prompt

console = Console()
log = get_logger("root_cause_analyzer")

from common_utils.llm_config import get_model_from_env
ROOT_CAUSE_MODEL = get_model_from_env("ROOT_CAUSE_MODEL")

SYSTEM_PROMPT = load_prompt("node_5_root_cause_prompt.md")


def _run_git(cmd: list, cwd: str) -> str:
    """Run a git command and return stdout, empty string on error."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except Exception as exc:
        log.warning(f"Git command failed: {cmd} — {exc}")
        return ""


def _get_recent_commits(project_path: str, n: int = 10) -> str:
    """Get the last N commits with author and message."""
    return _run_git(
        ["git", "log", f"-{n}", "--pretty=format:%H|%an|%ae|%s|%ad", "--date=short"],
        project_path,
    )


def _get_changed_files_per_commit(project_path: str, n: int = 5) -> str:
    """Get files changed in the last N commits."""
    return _run_git(
        ["git", "log", f"-{n}", "--name-only", "--pretty=format:%H %s"],
        project_path,
    )


def _git_blame_file(project_path: str, file_path: str, line_number: Optional[int]) -> str:
    """Get git blame for a specific file (and line if known)."""
    if not file_path:
        return ""
    full_path = Path(project_path) / file_path
    if not full_path.exists():
        return ""
    cmd = ["git", "blame", "--line-porcelain"]
    if line_number:
        cmd += [f"-L{line_number},{line_number}"]
    cmd.append(str(full_path))
    return _run_git(cmd, project_path)[:1000]  # cap at 1000 chars


def root_cause_commit_analysis(state: AgentState) -> dict:
    """
    Root Cause Commit Analysis Node.
    Uses git history + LLM to identify the breaking commit.
    """
    project_path = state.get("project_path", ".")
    failures = state.get("failures", [])
    classifications = state.get("failure_classifications", [])

    console.print("\n[bold magenta]Root Cause Commit Analysis Agent[/bold magenta]")
    console.print(f"   Model: {ROOT_CAUSE_MODEL}")

    if not failures:
        return {"root_cause": None, "status": "self_healing"}

    # Pick the most critical failure (APP_BUG preferred)
    primary_failure = failures[0]
    for i, cls in enumerate(classifications):
        if cls.get("bug_type") == "APP_BUG" and i < len(failures):
            primary_failure = failures[i]
            break

    # Determine git directories
    app_git_dir = project_path
    test_git_dir = os.path.join(project_path, "test_framework")
    has_test_framework = os.path.exists(test_git_dir) and os.path.exists(os.path.join(test_git_dir, ".git"))

    rel_file_path = primary_failure.get("file_path", "")

    # Gather git context
    console.print("   Collecting git history...")
    app_recent_commits = _get_recent_commits(app_git_dir, n=10)
    app_changed_files = _get_changed_files_per_commit(app_git_dir, n=5)

    test_recent_commits = ""
    test_changed_files = ""
    if has_test_framework:
        test_recent_commits = _get_recent_commits(test_git_dir, n=10)
        test_changed_files = _get_changed_files_per_commit(test_git_dir, n=5)

    blame_output = ""
    if rel_file_path:
        if rel_file_path.startswith("test_framework/"):
            blame_git_dir = test_git_dir
            blame_file = rel_file_path[len("test_framework/"):]
        else:
            blame_git_dir = app_git_dir
            blame_file = rel_file_path

        if blame_file:
            blame_output = _git_blame_file(
                blame_git_dir,
                blame_file,
                primary_failure.get("line_number"),
            )

    # Build prompt
    context = f"""FAILURE DETAILS:
Test: {primary_failure.get('test_name', 'unknown')}
File: {primary_failure.get('file_path', 'unknown')}
Error: {primary_failure.get('error_type') or ''}: {(primary_failure.get('error_message') or '')[:300]}
Traceback: {(primary_failure.get('traceback') or '')[:1000]}

RECENT GIT COMMITS (APP REPOSITORY):
{app_recent_commits or '(no git history available)'}

FILES CHANGED IN RECENT COMMITS (APP REPOSITORY):
{app_changed_files or '(not available)'}
"""
    if has_test_framework:
        context += f"""
RECENT GIT COMMITS (TEST REPOSITORY):
{test_recent_commits or '(no git history available)'}

FILES CHANGED IN RECENT COMMITS (TEST REPOSITORY):
{test_changed_files or '(not available)'}
"""

    context += f"""
GIT BLAME OUTPUT (failing file):
{blame_output or '(not available)'}
"""

    ai = AIWrapper(LLMConfig(model=ROOT_CAUSE_MODEL, temperature=0.1), mode="llm")

    try:
        raw = ai.run(prompt=f"Identify the root cause commit:\n\n{context}", system_prompt=SYSTEM_PROMPT)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        sha = parsed.get("commit_sha", "unknown")
        author = parsed.get("author", "unknown")
        author_email = parsed.get("author_email", "")
        date_str = "unknown"
        commit_msg = parsed.get("commit_message", "unknown")
        
        if sha != "unknown" and len(sha) >= 7:
            # Try to search in App repository first
            git_info = _run_git(["git", "log", "-1", "--pretty=format:%an|%ae|%ad|%B", "--date=short", sha], app_git_dir)
            
            # If not found in App repo, try Test repo
            if (not git_info or "|" not in git_info) and has_test_framework:
                git_info = _run_git(["git", "log", "-1", "--pretty=format:%an|%ae|%ad|%B", "--date=short", sha], test_git_dir)
                
            if git_info and "|" in git_info:
                parts = git_info.split("|", 3)
                if len(parts) >= 4:
                    author = parts[0].strip()
                    author_email = parts[1].strip()
                    date_str = parts[2].strip()
                    commit_msg = parts[3].strip()

        result = RootCauseResult(
            commit_sha=sha,
            commit_message=commit_msg,
            author=author,
            author_email=author_email,
            date=date_str,
            changed_files=parsed.get("changed_files", []),
            analysis=parsed.get("analysis", ""),
            confidence=int(parsed.get("confidence", 20)),
        )
        console.print(
            f"   Root cause identified: [bold]{result['commit_sha'][:8]}[/bold] "
            f"by {result['author']} (confidence: {result['confidence']}%)"
        )
        console.print(f"      Analysis: {result['analysis'][:100]}...")

    except Exception as exc:
        log.warning(f"Root cause analysis failed: {exc}")
        result = RootCauseResult(
            commit_sha="unknown",
            commit_message="Could not determine",
            author="unknown",
            author_email="",
            date="unknown",
            changed_files=[],
            analysis=f"Analysis failed: {str(exc)[:200]}",
            confidence=10,
        )

    return {"root_cause": result, "status": "self_healing"}
