"""
RegressionAI/agents/fetch_files.py — Fetch relevant source files for failed tests.
Adapted from QAOps for the RegressionAI pipeline.
"""
import os
import re
from pathlib import Path
from rich.console import Console
from common_utils.logger import get_logger
from RegressionAI.state import AgentState

console = Console()
log = get_logger("fetch_files")

MAX_FILE_SIZE = 12000  # chars per file
MAX_FILES = 10


def _read_file(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return content[:MAX_FILE_SIZE]
    except Exception:
        return ""


def _resolve_associated_source_files(test_file: str, project_path: Path) -> list:
    associated = []
    # Clean the test name to find keywords (e.g., test_api_users.py -> users)
    base = Path(test_file).stem.replace("test_api_", "").replace("test_", "")
    keywords = [k for k in base.split("_") if k and len(k) > 2]
    
    # Walk directory to find python files in app/routers matching the keywords
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (".venv", "tests", ".git", "__pycache__", "build", "dist")]
        for file in files:
            if file.endswith(".py"):
                file_stem = Path(file).stem
                # Limit keyword matching to router/controller files to avoid fetching database/model internals
                if "router" in root or "controllers" in root or "app" in root:
                    if any(kw in file_stem for kw in keywords):
                        rel = os.path.relpath(os.path.join(root, file), project_path)
                        associated.append(rel)
                        
    return associated


def _resolve_full_path(rel_path: str, project_path: Path) -> Path:
    p = Path(rel_path)
    if p.is_absolute() and p.exists():
        return p
    p = project_path / rel_path
    if p.exists():
        return p
    p = project_path.parent / rel_path
    if p.exists():
        return p
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        for child in project_path.parent.iterdir():
            if child.is_dir() and child.name != project_path.name:
                candidate = child / Path(*parts[1:])
                if candidate.exists():
                    return candidate
                candidate2 = child / rel_path
                if candidate2.exists():
                    return candidate2
    return project_path / rel_path


def _extract_files_from_traceback(traceback: str, project_path: Path) -> list:
    """Extract any valid python files mentioned in the traceback text."""
    found = []
    # Match patterns like: "tests/e2e/test_full_workflow.py:72" or "app/routers/users.py"
    matches = re.findall(r"([\w/\\.-]+\.py)", traceback)
    for m in matches:
        if _resolve_full_path(m, project_path).exists() and m not in found:
            # Avoid external/venv paths
            if not any(part in m for part in (".venv", "site-packages", "Python.framework")):
                found.append(m)
    return found


def get_file_snippet(project_path: Path, rel_path: str, failures: list) -> str:
    import re
    full_path = _resolve_full_path(rel_path, project_path)
    if not full_path.exists():
        return ""

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    lines = content.splitlines()
    num_lines = len(lines)
    if not lines:
        return ""

    # Parse tracebacks to find lines for this specific file
    line_numbers = set()
    
    pattern_file_line = re.compile(r'File\s+"([^"]+\.py)",\s+line\s+(\d+)')
    pattern_colon_line = re.compile(r'([\w/\\.-]+\.py):(\d+)')
    
    target_norm = rel_path.replace("\\", "/").lower()
    
    for f in failures:
        if f.get("file_path") and f.get("file_path").replace("\\", "/").lower() == target_norm:
            if f.get("line_number"):
                line_numbers.add(int(f["line_number"]))
                
        tb = f.get("traceback") or ""
        for line in tb.splitlines():
            for fp, lnum in pattern_file_line.findall(line):
                fp_norm = fp.replace("\\", "/").lower()
                if fp_norm.endswith(target_norm) or target_norm.endswith(fp_norm):
                    line_numbers.add(int(lnum))
            for fp, lnum in pattern_colon_line.findall(line):
                if any(p in fp for p in (".venv", "site-packages", "Python.framework")):
                    continue
                fp_norm = fp.replace("\\", "/").lower()
                if fp_norm.endswith(target_norm) or target_norm.endswith(fp_norm):
                    line_numbers.add(int(lnum))

    # If it's a test file and we didn't find specific line numbers, search for test function names
    if not line_numbers and ("test_" in rel_path or "tests/" in rel_path):
        for f in failures:
            tname = f.get("test_name", "")
            clean_tname = tname.split("::")[-1] if "::" in tname else tname
            if clean_tname:
                for idx, line in enumerate(lines):
                    if f"def {clean_tname}" in line:
                        line_numbers.add(idx + 1)
                        line_numbers.add(idx + 10)
                        line_numbers.add(idx + 20)

    # If we still have no line numbers, return the first part of the file up to MAX_FILE_SIZE
    if not line_numbers:
        if len(content) <= MAX_FILE_SIZE:
            return content
        else:
            return content[:MAX_FILE_SIZE] + "\n\n... [TRUNCATED to MAX_FILE_SIZE] ..."

    # Build windows/intervals around matching lines
    intervals = []
    for lnum in line_numbers:
        # Context window: 20 lines before and after
        start = max(1, lnum - 20)
        end = min(num_lines, lnum + 20)
        intervals.append((start, end))

    # Merge intervals
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = []
    if intervals:
        merged.append(intervals[0])
        for current in intervals[1:]:
            prev_start, prev_end = merged[-1]
            curr_start, curr_end = current
            if curr_start <= prev_end + 10:
                merged[-1] = (prev_start, max(prev_end, curr_end))
            else:
                merged.append(current)

    # Format the snippets
    snippet_parts = []
    for idx, (start, end) in enumerate(merged):
        if idx > 0:
            snippet_parts.append("\n... [SKIPPED LINES] ...\n")
        snippet_parts.append("\n".join(lines[start - 1:end]))
        
    return "\n".join(snippet_parts)


def _get_failure_key(f: dict) -> tuple:
    import re
    file_path = f.get("file_path") or ""
    
    line_number = 0
    raw_lnum = f.get("line_number")
    if raw_lnum is not None:
        try:
            line_number = int(raw_lnum)
        except (ValueError, TypeError):
            line_number = 0
    
    tb = f.get("traceback") or ""
    if tb:
        matches = re.findall(r'File\s+"([^"]+\.py)",\s+line\s+(\d+)', tb)
        if matches:
            for fp, lnum in reversed(matches):
                if not any(part in fp for part in (".venv", "site-packages", "Python.framework")):
                    file_path = fp or ""
                    try:
                        line_number = int(lnum)
                    except (ValueError, TypeError):
                        line_number = 0
                    break
    return (str(file_path).lower(), line_number)


def fetch_files(state: AgentState) -> dict:
    """Collect all source files referenced in failures."""
    failures = state.get("failures", [])
    project_path = Path(state.get("project_path", "."))

    console.print(f"\n[bold blue]Fetch Files[/bold blue]")

    # De-duplicate failures based on failure location to keep context size small
    unique_failures = []
    seen_keys = set()
    for f in failures:
        key = _get_failure_key(f)
        t_name = f.get("test_name", "unknown")
        if key not in seen_keys and t_name not in seen_keys:
            seen_keys.add(key)
            seen_keys.add(t_name)
            unique_failures.append(f)
    if not unique_failures:
        unique_failures = failures

    paths_to_fetch = set()

    for f in unique_failures:
        if f.get("file_path"):
            paths_to_fetch.add(f["file_path"])
            # Resolve associated source files dynamically (e.g., routers)
            for src in _resolve_associated_source_files(f["file_path"], project_path):
                paths_to_fetch.add(src)
        
        # Extract files directly from the execution traceback
        if f.get("traceback"):
            for tb_file in _extract_files_from_traceback(f["traceback"], project_path):
                paths_to_fetch.add(tb_file)

        for src in f.get("source_files", []):
            if src:
                paths_to_fetch.add(src)

    # If any traceback indicates a missing module, fetch requirements.txt files
    is_missing_module = False
    for f in unique_failures:
        tb = f.get("traceback") or ""
        msg = f.get("error_message") or ""
        err_type = f.get("error_type") or ""
        if "ModuleNotFoundError" in tb or "ModuleNotFoundError" in msg or "ModuleNotFoundError" in err_type or "No module named" in tb or "No module named" in msg:
            is_missing_module = True
            break
            
    if is_missing_module:
        for req_file in ["requirements.txt", "test_framework/requirements.txt"]:
            if (project_path / req_file).exists() or _resolve_full_path(req_file, project_path).exists():
                paths_to_fetch.add(req_file)

    # Automatically resolve core application dependencies from imports (e.g. schemas, crud, models)
    dependencies = set()
    for rel_path in paths_to_fetch:
        full = _resolve_full_path(rel_path, project_path)
        if full.exists():
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if "from app" in line or "import app" in line:
                        for mod in ["crud", "schemas", "models", "database"]:
                            if mod in line:
                                rel_mod = f"app/{mod}.py"
                                if (project_path / rel_mod).exists():
                                    dependencies.add(rel_mod)
            except Exception:
                pass
    paths_to_fetch.update(dependencies)

    # Prioritize paths so we don't drop critical routers/tests during slicing
    def get_path_priority(path_str: str) -> int:
        path_lower = path_str.replace("\\", "/").lower()
        # Direct failing file
        for f in unique_failures:
            if f.get("file_path") and f["file_path"].replace("\\", "/").lower() == path_lower:
                return 100
        # Requirements / Dependencies
        if "requirements" in path_lower or "pyproject" in path_lower or "setup.py" in path_lower:
            return 95
        # Traceback files
        for f in unique_failures:
            if f.get("traceback") and path_lower in f["traceback"].replace("\\", "/").lower():
                return 80
        # Routers/controllers
        if "router" in path_lower or "controller" in path_lower:
            return 60
        # CRUD
        if "crud" in path_lower:
            return 50
        # Schema
        if "schema" in path_lower:
            return 30
        # Model
        if "model" in path_lower:
            return 20
        # Database
        if "database" in path_lower or "db" in path_lower:
            return 10
        return 0

    sorted_paths = sorted(list(paths_to_fetch), key=get_path_priority, reverse=True)

    relevant: dict = {}
    for rel_path in sorted_paths[:MAX_FILES]:
        content = get_file_snippet(project_path, rel_path, failures)
        if content:
            relevant[rel_path] = content
            console.print(f"   {rel_path} ({len(content)} chars snippet)")
        else:
            log.warning(f"File not found or empty: {rel_path}")

    console.print(f"   Fetched {len(relevant)} file(s)")
    return {"relevant_files": relevant, "status": "self_healing"}
