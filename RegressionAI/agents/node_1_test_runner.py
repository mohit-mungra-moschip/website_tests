"""
RegressionAI/agents/test_runner.py — Run tests and capture output.
Adapted from QAOps for the RegressionAI pipeline.
"""
import os
import subprocess
from rich.console import Console
from common_utils.logger import get_logger
from RegressionAI.state import AgentState

console = Console()
log = get_logger("test_runner")


def run_tests(state: AgentState) -> dict:
    """Run the test command and capture output."""
    project_path = state.get("project_path", ".")
    test_command = state.get("test_command", "pytest")

    console.print(f"\n[bold blue]Test Runner[/bold blue]")
    console.print(f"   Command: [cyan]{test_command}[/cyan]")
    console.print(f"   Path: {project_path}")

    # Use venv python if available
    venv_path = os.path.join(project_path, ".venv", "bin", "activate")
    if os.path.exists(venv_path):
        full_cmd = f"source {venv_path} && {test_command}"
        shell_executable = "/bin/bash"
    else:
        full_cmd = test_command
        shell_executable = None

    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=300,
            executable=shell_executable,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0

        if passed:
            console.print("   [green]All tests PASSED[/green]")
        else:
            lines = output.splitlines()
            # Find summary line
            for line in reversed(lines):
                if "passed" in line or "failed" in line or "error" in line:
                    console.print(f"   [red]{line.strip()}[/red]")
                    break

        return {
            "test_output": output,
            "test_passed": passed,
            "failures": [],
            "status": "analyzing" if not passed else "done",
        }

    except subprocess.TimeoutExpired:
        return {
            "test_output": "Test run timed out after 300 seconds.",
            "test_passed": False,
            "failures": [],
            "status": "error",
            "error": "Test timeout",
        }
    except Exception as exc:
        return {
            "test_output": str(exc),
            "test_passed": False,
            "failures": [],
            "status": "error",
            "error": str(exc),
        }
