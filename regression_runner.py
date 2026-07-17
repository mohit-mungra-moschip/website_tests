"""
regression_runner.py — CI/CD entry point for the RegressionAI pipeline.

Called by GitHub Actions after pytest runs. Feeds pytest output
through the AI pipeline (failure analysis → self-healing → root cause → jira).
"""
import json
import os
import sys
import uuid
import click
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Try multiple candidate paths for .env
env_paths = [
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.path.dirname(__file__), "../agentic_pipeline/.env"),
    os.path.join(os.path.dirname(__file__), ".env")
]
for path in env_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path, override=True)
        break

# Ensure parent directory of workspaces is in sys.path for docstring import resolving
parent_dir = str(Path(__file__).resolve().parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
test_dir = str(Path(__file__).resolve().parent)
if test_dir not in sys.path:
    sys.path.insert(0, test_dir)


def _update_active_run_status(stage: str, percentage: int, status: str, run_id: str, message: str = "", running: bool = True):
    """Write active run status to reports/active_run.json for real-time UI monitoring."""
    try:
        Path("reports").mkdir(exist_ok=True)
        status_data = {
            "run_id": run_id,
            "stage": stage,
            "percentage": percentage,
            "status": status,
            "message": message,
            "running": running,
            "updated_at": datetime.now().isoformat()
        }
        Path("reports/active_run.json").write_text(
            json.dumps(status_data, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


@click.command()
@click.option("--project-path", "-p", default=".", show_default=True)
@click.option("--test-command", "-c", default="pytest tests/ -v --tb=short --junitxml=logs/test-results.xml")
@click.option("--run-id", default=None, help="GitHub Actions run ID")
@click.option("--ci-mode", is_flag=True, default=True, help="Fully agentic mode")
@click.option("--max-iter", default=3, type=int, show_default=True)
@click.option("--create-jira", default="true", help="Create Jira tickets (true/false)")
def main(project_path, test_command, run_id, ci_mode, max_iter, create_jira):
    """AI-powered regression pipeline entry point for CI/CD."""
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    run_id = run_id or os.environ.get("REGRESSION_RUN_ID") or str(uuid.uuid4())[:8]
    os.environ["REGRESSION_RUN_ID"] = run_id
    os.environ["REGRESSION_RUN_STAMP"] = os.environ.get("REGRESSION_RUN_STAMP") or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    should_create_jira = str(create_jira).lower() in ("true", "1", "yes")

    import signal
    def handle_abort(signum, frame):
        console.print("\n[bold red]Pipeline aborted by user / process manager.[/bold red]\n")
        _update_active_run_status("Aborted", 100, "aborted", run_id, "Pipeline aborted by signal or user interrupt.", running=False)
        sys.exit(1)

    signal.signal(signal.SIGINT, handle_abort)
    signal.signal(signal.SIGTERM, handle_abort)

    # Ensure test command contains --junitxml=logs/test-results.xml for Excel report parsing
    if "pytest" in test_command and "--junitxml=" not in test_command:
        Path("logs").mkdir(exist_ok=True)
        test_command = f"{test_command} --junitxml=logs/test-results.xml"

    # Initial state write
    _update_active_run_status("Initializing", 5, "running", run_id, "Preparing environment and setting up agent nodes...")

    console.print(Panel(
        f"[bold]Run ID:[/bold]       {run_id}\n"
        f"[bold]Project:[/bold]      {project_path}\n"
        f"[bold]Test Command:[/bold] {test_command}\n"
        f"[bold]CI Mode:[/bold]      {'Fully Agentic' if ci_mode else 'Interactive'}\n"
        f"[bold]Max Iterations:[/bold] {max_iter}\n"
        f"[bold]Create Jira:[/bold]  {should_create_jira}",
        title="[bold blue]RegressionAI Pipeline[/bold blue]",
    ))

    # Read test output from file (CI writes it) or run fresh
    raw_output_path = Path("reports/raw_output.txt")
    if raw_output_path.exists():
        test_output = raw_output_path.read_text(encoding="utf-8")
        test_passed = "failed" not in test_output.lower() and "error" not in test_output.lower()
        if "passed" in test_output and "failed" not in test_output:
            test_passed = True
        console.print(f"[dim]Using existing test output ({len(test_output)} chars)[/dim]")
    else:
        test_output = ""
        test_passed = False

    _update_active_run_status("Running Tests", 15, "running", run_id, "Executing test suite via pytest...")

    from RegressionAI.graph import compile_graph
    graph = compile_graph()

    initial_state = {
        "project_path":           str(Path(project_path).resolve()),
        "test_command":           test_command,
        "run_id":                 run_id,
        "ci_mode":                ci_mode,
        "test_output":            test_output,
        "test_passed":            test_passed,
        "failures":               [],
        "failure_classifications": [],
        "overall_confidence":     0,
        "relevant_files":         {},
        "proposed_fixes":         [],
        "approved_fixes":         [],
        "applied_fix_log":        [],
        "healing_successful":     False,
        "healed_test_output":     "",
        "root_cause":             None,
        "action_recommendations": [],
        "jira_results":           [],
        "jira_results_healed":    [],   # NEW: healed Jira tickets
        "create_jira":            should_create_jira,
        "healing_type":           "NONE",  # NEW: APP_HEAL | TEST_HEAL | MIXED | NONE
        "env_issues":             [],       # NEW: ENV_ISSUE classified failures
        "pr_links":               [],       # NEW: PR URLs created during this run
        "status":                 "running",
        "error":                  None,
        "iteration":              0,
        "max_iterations":         max_iter,
        "user_feedback":          None,
        "messages":               [],
    }

    thread = {"configurable": {"thread_id": run_id}}
    try:
        console.print("\n[bold cyan]Starting RegressionAI Pipeline...[/bold cyan]\n")
        
        # Stream updates in real-time
        for event in graph.stream(initial_state, thread, stream_mode="updates"):
            for node_name, node_update in event.items():
                console.print(f"[bold cyan]Node completed: {node_name}[/bold cyan]")
                
                # Update status based on completed node
                stage_mapping = {
                    "run_tests": ("Running Tests", 20, "Executing test suite via pytest..."),
                    "parse_failures": ("Parsing Failures", 35, "Extracting and parsing test failures..."),
                    "fetch_files": ("Fetching Files", 50, "Reading and mapping source files..."),
                    "failure_analysis": ("Analyzing Failures", 65, "Classifying failures via LLM..."),
                    "self_healing": ("Self Healing", 80, "Attempting autonomous code fixes..."),
                    "root_cause_commit_analysis": ("Root Cause Analysis", 90, "Analyzing git blame and commits..."),
                    "action_recommendation_engine": ("Generating Recommendations", 95, "Creating actionable recommendations..."),
                    "jira_ticketing": ("Jira Sync", 98, "Syncing regression tickets with Jira..."),
                }
                
                if node_name in stage_mapping:
                    stage, pct, msg = stage_mapping[node_name]
                    # Custom logs overlay
                    if "failures" in node_update and node_name == "parse_failures":
                        msg = f"Parsed {len(node_update['failures'])} failures."
                    elif "healing_successful" in node_update and node_name == "self_healing":
                        success       = node_update["healing_successful"]
                        healing_type  = node_update.get("healing_type", "NONE")
                        env_count     = len(node_update.get("env_issues") or [])
                        msg = (
                            f"Self-healing ({healing_type}): {'Success' if success else 'Failure/Escalating'}"
                            + (f" | {env_count} ENV_ISSUE(s) surfaced" if env_count else "")
                        )
                    elif "root_cause" in node_update and node_update["root_cause"] and node_name == "root_cause_commit_analysis":
                        rc  = node_update["root_cause"]
                        msg = f"Root cause commit found: {rc.get('commit_sha', 'unknown')[:8]}"

                    _update_active_run_status(stage, pct, "running", run_id, msg)

                # Dynamic node logs
                if "failures" in node_update and node_name == "parse_failures":
                    console.print(f"  └─ Parsed [bold red]{len(node_update['failures'])}[/bold red] failures.")
                if "failure_classifications" in node_update:
                    console.print(f"  └─ Failure classifications completed.")
                if "healing_successful" in node_update:
                    success = node_update["healing_successful"]
                    console.print(f"  └─ Healing outcome: {'[bold green]Success[/bold green]' if success else '[bold red]Failure / Escalating[/bold red]'}")
                if "root_cause" in node_update and node_update["root_cause"]:
                    rc = node_update["root_cause"]
                    console.print(f"  └─ Root cause identified: commit [bold magenta]{rc.get('commit_sha', 'unknown')[:8]}[/bold magenta] by {rc.get('author')}")
                if "action_recommendations" in node_update:
                    console.print(f"  └─ Generated [bold]{len(node_update['action_recommendations'])}[/bold] fix recommendations.")
                if "jira_results" in node_update:
                    jira_open   = [r for r in node_update.get("jira_results",        []) if r.get("status") == "created"]
                    jira_healed = [r for r in node_update.get("jira_results_healed", []) if r.get("status") == "created"]
                    console.print(
                        f"  └─ Jira: [bold green]{len(jira_healed)}[/bold green] healed + "
                        f"[bold red]{len(jira_open)}[/bold red] unhealed ticket(s) created."
                    )

        snap = graph.get_state(thread)
        final = snap.values

        # Write AI summary for CI job summary
        _write_summary(final, run_id)
        _write_json_report(final, run_id)
        _update_html_and_excel_reports(final, run_id)

        # Send pipeline report email (non-blocking — failure won't abort the run)
        if not os.getenv("GITHUB_ACTIONS"):
            try:
                from utils.mailer import send_pipeline_report
                send_pipeline_report(final, run_id)
            except Exception as mail_exc:
                console.print(f"  [yellow]Mailer skipped: {mail_exc}[/yellow]")

        if final.get("healing_successful") or final.get("test_passed"):
            console.print("\n[bold green]Pipeline complete — all issues resolved.[/bold green]")
            _update_active_run_status("Completed", 100, "success", run_id, "Pipeline complete — all issues resolved.", running=False)
            sys.exit(0)
        else:
            remaining = len([
                f for f in final.get("failures", [])
                if not final.get("healing_successful")
            ])
            console.print(f"\n[bold yellow]Pipeline complete — {remaining} unhealed failure(s).[/bold yellow]")
            _update_active_run_status("Completed", 100, "warning", run_id, f"Pipeline complete — {remaining} unhealed failure(s).", running=False)
            sys.exit(0)  # Exit 0 so CI doesn't double-fail; final check job handles this

    except KeyboardInterrupt:
        console.print("\n[bold red]Pipeline aborted by user (Ctrl+C).[/bold red]\n")
        _update_active_run_status("Aborted", 100, "aborted", run_id, "Pipeline aborted by user (Ctrl+C).", running=False)
        sys.exit(1)
    except Exception as exc:
        import traceback
        console.print(f"[bold red]Pipeline error: {exc}[/bold red]")
        traceback.print_exc()
        _write_summary({}, run_id, error=str(exc))
        _update_active_run_status("Error", 100, "error", run_id, f"Pipeline error: {exc}", running=False)
        sys.exit(1)
    finally:
        try:
            from common_utils.token_tracker import token_tracker
            token_tracker.print_summary()
            token_tracker.save_to_json()
        except Exception as tracker_exc:
            console.print(f"[bold red]Error showing token usage summary: {tracker_exc}[/bold red]")


def _write_summary(state: dict, run_id: str, error: str = None):
    Path("reports").mkdir(exist_ok=True)
    failures          = state.get("failures", [])
    healed            = state.get("healing_successful", False)
    healing_type      = state.get("healing_type", "NONE")
    jira_results      = state.get("jira_results", [])
    jira_healed       = state.get("jira_results_healed", [])
    classifications   = state.get("failure_classifications", [])
    env_issues        = state.get("env_issues") or []
    approved_fixes    = state.get("approved_fixes", [])
    from common_utils.token_tracker import token_tracker

    all_jira = jira_results + jira_healed
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "all_healed": healed,
        "healing_type": healing_type,
        "error": error,
        "summary": {
            "total":              len(failures),
            "passed":             0 if failures else 1,
            "failed":             len(failures) if not healed else 0,
            "healed":             len(failures) if healed else 0,
            "env_issues":         len(env_issues),
            "overall_confidence": state.get("overall_confidence", 0),
            "jira_tickets_total": sum(1 for r in all_jira if r.get("status") == "created"),
            "jira_healed":        sum(1 for r in jira_healed if r.get("status") == "created"),
            "jira_unhealed":      sum(1 for r in jira_results if r.get("status") == "created"),
        },
        "classifications":   classifications,
        "jira_results":      jira_results,
        "jira_results_healed": jira_healed,
        "root_cause":        state.get("root_cause"),
        "approved_fixes":    approved_fixes,
        "healed_files":      [f.get("file_path") for f in approved_fixes],
        "env_issues":        env_issues,
        "recommendations":   state.get("action_recommendations", []),
        "pr_links":          state.get("pr_links") or [],
        "token_usage":       token_tracker.get_summary_dict(),
    }
    Path("reports/ai_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )


def _get_current_commit(project_path: str) -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"

def _write_json_report(state: dict, run_id: str):
    """Write a full JSON report for the web dashboard."""
    Path("reports").mkdir(exist_ok=True)
    from common_utils.token_tracker import token_tracker
    report = {
        "run_id":               run_id,
        "generated_at":         datetime.now().isoformat(),
        "test_passed":          state.get("test_passed", False),
        "healing_successful":   state.get("healing_successful", False),
        "healing_type":         state.get("healing_type", "NONE"),
        "failures":             state.get("failures", []),
        "classifications":      state.get("failure_classifications", []),
        "overall_confidence":   state.get("overall_confidence", 0),
        "root_cause":           state.get("root_cause"),
        "recommendations":      state.get("action_recommendations", []),
        "jira_results":         state.get("jira_results", []),
        "jira_results_healed":  state.get("jira_results_healed", []),
        "env_issues":           state.get("env_issues") or [],
        "applied_fixes":        state.get("applied_fix_log", []),
        "healed_files":         [f.get("file_path") for f in state.get("approved_fixes", [])],
        "pr_links":             state.get("pr_links") or [],
        "current_commit":       _get_current_commit(state.get("project_path", ".")),
        "token_usage":          token_tracker.get_summary_dict(),
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    Path(f"reports/full_report_{stamp}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )


def _update_html_and_excel_reports(state: dict, run_id: str):
    """Update HTML report with Jira link, and generate Excel report."""
    from rich.console import Console
    console = Console()

    # 1. Update HTML/JSON with Jira results
    try:
        import conftest
        import json
        from pathlib import Path

        # Locate the test_results_{run_stamp}.json and HTML files
        run_stamp = os.environ.get("REGRESSION_RUN_STAMP") or run_id
        json_paths = []
        json_dir = Path("reports/json")
        if json_dir.exists():
            for jf in json_dir.glob("test_results_*.json"):
                try:
                    payload = json.loads(jf.read_text(encoding="utf-8"))
                    file_run_id = str(payload.get("run_id") or "")
                    if file_run_id == str(run_id) or file_run_id == f"{run_id}_healed" or file_run_id.startswith(str(run_id)):
                        json_paths.append(jf)
                except Exception:
                    pass
        if not json_paths:
            json_paths = [
                Path(f"reports/json/test_results_{run_stamp}.json"),
                Path(f"reports/json/test_results_{run_stamp}_healed.json"),
                Path(f"reports/json/test_results_{run_stamp}_full_rerun.json")
            ]

        for json_path in json_paths:
            if json_path.exists():
                html_path = json_path.parent.parent / "html" / json_path.name.replace(".json", ".html")
                payload = json.loads(json_path.read_text(encoding="utf-8"))

                # Map jira results and AI insights
                jira_map = {j["test_name"]: j for j in state.get("jira_results", [])}
                jira_healed_map = {j["test_name"]: j for j in state.get("jira_results_healed", [])}
                class_map = {c["test_name"]: c for c in state.get("failure_classifications", [])}
                rec_map = {r["test_name"]: r for r in state.get("action_recommendations", [])}

                updated_results = []
                for result in payload.get("results", []):
                    test_name = result.get("test_name")
                    test_id = result.get("test_id")
                    
                    # Check mapping via full test_id, short test_name, or if the ticket test_name is a substring of test_id (e.g. file path matching)
                    jr = None
                    for k, v in jira_map.items():
                        if k == test_id or k == test_name or (k.endswith('.py') and k in test_id):
                            jr = v
                            break
                    
                    jrh = None
                    for k, v in jira_healed_map.items():
                        if k == test_id or k == test_name or (k.endswith('.py') and k in test_id):
                            jrh = v
                            break
                    
                    if jr:
                        result["jira_id"] = jr.get("jira_id")
                        result["jira_url"] = jr.get("jira_url")
                    elif jrh:
                        result["jira_id"] = jrh.get("jira_id")
                        result["jira_url"] = jrh.get("jira_url")
                        
                    # PR URL mapping
                    if jrh or (state.get("healing_successful") and jr):
                        result["is_healed"] = True
                        if state.get("pr_links"):
                            result["pr_url"] = ",".join(state.get("pr_links"))
                        else:
                            h_type = state.get("healing_type", "APP_HEAL")
                            repo = "agentic_pipeline_tests" if h_type == "TEST_HEAL" else "agentic_pipeline"
                            result["pr_url"] = f"https://github.com/mohit-mungra-moschip/{repo}/tree/ai-fix/app-{run_id}"
                    
                    cls = None
                    for k, v in class_map.items():
                        if k == test_id or k == test_name or (k.endswith('.py') and k in test_id):
                            cls = v
                            break
                    if cls:
                        result["ai_short_summary"] = cls.get("reasoning", "")
                        
                    rec = None
                    for k, v in rec_map.items():
                        if k == test_id or k == test_name or (k.endswith('.py') and k in test_id):
                            rec = v
                            break
                    if rec:
                        result["ai_suggested_fix"] = rec.get("suggested_fix", "")
                        if rec.get("summary") and not result.get("ai_short_summary"):
                            result["ai_short_summary"] = rec.get("summary")
                            
                    updated_results.append(result)

                payload["results"] = updated_results

                # Recalculate summary counts with healed metrics
                _results = updated_results
                _total = len(_results)
                _healed = sum(1 for r in _results if r.get("pr_url"))
                _passed = sum(1 for r in _results if r.get("status") in ("PASS", "PASSED") and not r.get("pr_url"))
                _failed = sum(1 for r in _results if r.get("status") in ("FAIL", "FAILED", "ERROR") and not r.get("pr_url"))
                _skipped = sum(1 for r in _results if r.get("status") == "SKIPPED")
                payload["summary"] = {
                    "total": _total,
                    "passed": _passed,
                    "failed": _failed,
                    "healed": _healed,
                    "skipped": _skipped,
                    "success_rate": round(((_passed + _healed) / _total * 100) if _total > 0 else 0.0, 2)
                }

                # Save updated JSON
                json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

                # Regenerate HTML
                html_content = conftest._build_html(payload, json_path.name)
                html_path.write_text(html_content, encoding="utf-8")
                console.print(f"  🎨 [bold green]Updated Visual HTML report with Jira link[/bold green] -> {html_path}")
    except Exception as e:
        console.print(f"  [yellow]Could not update HTML report: {e}[/yellow]")

    # 2. Generate Excel Report — prefer healed JSON (full results), fall back to XML
    try:
        from utils.report_utils.excel_report import generate_excel_from_json, _generate_excel_report_inline
        from pathlib import Path

        run_stamp = os.environ.get("REGRESSION_RUN_STAMP") or run_id
        dest = Path(f"reports/test_results_{run_stamp}.xlsx")
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Find the best JSON: prefer the one with most results (healed full run)
        best_json = None
        json_dir = Path("reports/json")
        if json_dir.exists():
            import json as _j
            candidates = []
            for jf in json_dir.glob("test_results_*.json"):
                try:
                    p = _j.loads(jf.read_text(encoding="utf-8"))
                    frid = str(p.get("run_id") or "")
                    if (frid == str(run_id) or frid == f"{run_id}_healed"
                            or frid.startswith(str(run_id))):
                        candidates.append((len(p.get("results", [])), jf))
                except Exception:
                    pass
            if candidates:
                best_json = sorted(candidates, key=lambda x: -x[0])[0][1]

        excel_file = None
        if best_json:
            excel_file = generate_excel_from_json(str(best_json), str(dest), ai_state=state)

        # Fall back to XML-based generation if JSON approach failed
        if not excel_file or not Path(excel_file).exists():
            excel_file = _generate_excel_report_inline("logs", output_file=str(dest), ai_state=state)

        if excel_file and Path(excel_file).exists():
            console.print(f"  [bold green]Excel Report Generated Successfully[/bold green] -> {dest}")
        else:
            console.print("  [yellow]Excel report generation returned empty or JSON/XML was not found.[/yellow]")
            
        # 3. Upload results to TestRail (if configured)
        if best_json:
            try:
                from utils.report_utils.test_rail_sync import sync_results_to_testrail
                sync_results_to_testrail(str(best_json))
            except Exception as e:
                console.print(f"  [red]TestRail sync failed: {e}[/red]")
    except Exception as e:
        console.print(f"  [red]Excel report generation failed: {e}[/red]")


if __name__ == "__main__":
    main()
