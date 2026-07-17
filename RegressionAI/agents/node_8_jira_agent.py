"""
RegressionAI/agents/jira_agent.py — Jira Ticketing Agent (Node 8)

Creates Jira tickets for ALL classified failures (healed and unhealed) when create_jira=True:

  APP_BUG  (healed)   → "Bug Fixed by AI" ticket in Done/Closed state  → tracked in jira_results_healed
  TEST_BUG (healed)   → "Test Updated by AI" ticket                     → tracked in jira_results_healed
  APP_BUG  (unhealed) → "Bug" ticket, open                              → tracked in jira_results
  TEST_BUG (unhealed) → "Bug" ticket, open                              → tracked in jira_results
  ENV_ISSUE           → "Task" ticket for env remediation               → tracked in jira_results

Uses: JIRA_AGENT_MODEL (default: groq/llama-3.3-70b-versatile)
"""
import os
from typing import List
from rich.console import Console
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, JiraResult, EnvIssue

console = Console()
log = get_logger("jira_agent")

# Jira config (from environment / .env)
JIRA_SERVER    = (os.getenv("JIRA_SERVER") or "https://moschip-team-doibg33r.atlassian.net").strip().strip("'\"")
JIRA_USERNAME  = (os.getenv("JIRA_USERNAME") or "mohit.mungra@moschip.com").strip().strip("'\"")
JIRA_PASSWORD  = (os.getenv("JIRA_PASSWORD") or "").strip().strip("'\"")
JIRA_PROJECT   = (os.getenv("JIRA_PROJECT_KEY") or "SCRUM").strip().strip("'\"")
ASSIGNEE_EMAIL = (os.getenv("ASSIGNEE_EMAIL") or "mohit.mungra@moschip.com").strip().strip("'\"")

from common_utils.llm_config import get_model_from_env
JIRA_AGENT_MODEL = get_model_from_env("JIRA_AGENT_MODEL")


# ── Low-level JIRA helpers ────────────────────────────────────────────────────

def _get_jira_client():
    """Return an authenticated JIRA client or raise ValueError."""
    from jira import JIRA
    if not JIRA_PASSWORD:
        raise ValueError("JIRA_PASSWORD not configured in .env or environment")
    
    # Safe debugging info for user verification in CI logs
    console.print(f"   [dim]Jira: Connecting to {JIRA_SERVER} as {JIRA_USERNAME} (API Token length: {len(JIRA_PASSWORD)})[/dim]")
    return JIRA(options={"server": JIRA_SERVER}, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))


def _add_to_sprint(jira, issue_key: str):
    """Attempt to add an issue to the active sprint (best-effort)."""
    try:
        import config
        default_board = config.JIRA_BOARD_NAME
        default_sprint = config.JIRA_SPRINT_NAME
    except ImportError:
        default_board = "SCRUM board"
        default_sprint = "SCRUM Sprint 0"

    board_name  = os.getenv("JIRA_BOARD_NAME",  default_board).strip().strip("'\"")
    sprint_name = os.getenv("JIRA_SPRINT_NAME", default_sprint).strip().strip("'\"")
    try:
        boards = jira.boards(name=board_name)
        if boards:
            sprints = jira.sprints(boards[0].id)
            active  = next((s for s in sprints if s.state == "active" and s.name == sprint_name), None)
            if not active:
                # Robust fallback: find any active sprint on this board
                active = next((s for s in sprints if s.state == "active"), None)
            
            if active:
                jira.add_issues_to_sprint(active.id, [issue_key])
                console.print(f"   Sprint: Added to '{active.name}'")
            else:
                log.warning(f"No active sprint found on board '{board_name}' (configured name: '{sprint_name}')")
    except Exception as exc:
        log.warning(f"Failed to add {issue_key} to sprint: {exc}")


def _assign_issue(jira, issue_key: str, assignee_email: str):
    """Assign the issue to a user (best-effort)."""
    # Filter out local mock emails that won't exist in Jira
    if assignee_email and (assignee_email.endswith(".local") or "local" in assignee_email or "regressionai" in assignee_email):
        target = ASSIGNEE_EMAIL
    else:
        target = assignee_email or ASSIGNEE_EMAIL

    if target:
        try:
            jira.assign_issue(issue_key, target)
            console.print(f"   Assignee: {target}")
            return
        except Exception as exc:
            log.warning(f"Failed to assign {issue_key} to {target}: {exc}")
            if target != ASSIGNEE_EMAIL and ASSIGNEE_EMAIL:
                try:
                    console.print(f"   Fallback assignee: {ASSIGNEE_EMAIL}")
                    jira.assign_issue(issue_key, ASSIGNEE_EMAIL)
                except Exception as exc2:
                    log.warning(f"Failed fallback assignment to {ASSIGNEE_EMAIL}: {exc2}")


def _format_applied_fixes(approved_fixes: list) -> str:
    if not approved_fixes:
        return ""
    
    lines = ["h3. Applied Code Fixes\n"]
    for fix in approved_fixes:
        fp          = fix.get("file_path", "unknown")
        diff_str    = (fix.get("diff") or "").strip()
        explanation = (fix.get("explanation") or "").strip()
        
        lines.append(f"*File:* `{fp}`")

        if diff_str:
            # Trim the unified diff to only the changed hunks (skip the header lines)
            hunk_lines = []
            for line in diff_str.splitlines():
                if line.startswith("---") or line.startswith("+++"):
                    continue        # skip file header lines
                hunk_lines.append(line)
            if hunk_lines:
                lines.append("{code:diff}")
                lines.extend(hunk_lines)
                lines.append("{code}")

        if explanation:
            lines.append(f"*Explanation:* {explanation}")
        lines.append("")
    return "\n".join(lines)


# ── Ticket builders ───────────────────────────────────────────────────────────

def _create_failure_ticket(
    test_name: str,
    bug_type: str,
    error_message: str,
    ai_summary: str,
    suggested_fix: str,
    priority: str,
    confidence: int,
    heal_status: str,          # "healed" | "unhealed"
    commit_sha: str = "unknown",
    run_id: str = "",
    assignee_email: str = "",
    approved_fixes: list = None,
) -> JiraResult:
    """Create a Jira Bug ticket for a healed or unhealed APP_BUG / TEST_BUG failure."""
    try:
        jira = _get_jira_client()

        jira_priority = {"Critical": "Highest", "High": "High", "Medium": "Medium", "Low": "Low"}.get(priority, "Medium")

        # Differentiate healed vs unhealed in the title and description
        healed_badge  = "AI-Healed" if heal_status == "healed" else "Unhealed"
        issue_summary = f"[RegressionAI] {bug_type} {healed_badge} | {test_name[:80]}"

        repo = os.getenv("GITHUB_REPOSITORY", "mohit-mungra-moschip/agentic_pipeline")
        server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")

        if run_id and run_id.isdigit():
            run_link = f"[{run_id}|{server_url}/{repo}/actions/runs/{run_id}]"
        else:
            run_link = run_id

        if commit_sha and len(commit_sha) >= 7 and commit_sha != "unknown":
            commit_link = f"[{commit_sha[:8]}|{server_url}/{repo}/commit/{commit_sha}]"
        else:
            commit_link = f"`{commit_sha[:8]}`" if commit_sha else "`unknown`"

        description = (
            f"*GitHub Pipeline Run:* {run_link}\n\n"
            f"*Failed Test:* `{test_name}`\n"
            f"*Classification:* {bug_type} (AI Confidence: {confidence}%)\n"
            f"*Heal Status:* {heal_status.upper()}\n"
            f"*Likely Root Cause Commit:* {commit_link}\n\n"
            f"h3. AI Analysis\n{ai_summary}\n\n"
            f"h3. Suggested Fix\n{suggested_fix}\n\n"
            f"_Ticket auto-created by RegressionAI pipeline._"
        )

        issue = jira.create_issue(
            project=JIRA_PROJECT,
            summary=issue_summary,
            description=description,
            issuetype={"name": "Bug"},
            priority={"name": jira_priority},
        )

        # Transition to In Review if already healed (since a PR is opened)
        if heal_status == "healed":
            try:
                transitions = jira.transitions(issue)
                review_t = next((t for t in transitions if "review" in t["name"].lower()), None)
                if review_t:
                    jira.transition_issue(issue, review_t["id"])
                    console.print(f"   → Transitioned {issue.key} to {review_t['name']} (healed)")
                else:
                    # Fallback to Done if no Review transition exists in workflow
                    done_t = next((t for t in transitions if t["name"].lower() in ("done", "closed", "resolved")), None)
                    if done_t:
                        jira.transition_issue(issue, done_t["id"])
                        console.print(f"   → Transitioned {issue.key} to Done (healed - fallback)")
            except Exception as te:
                log.warning(f"Could not transition {issue.key} to In Review: {te}")

        _assign_issue(jira, issue.key, assignee_email)
        _add_to_sprint(jira, issue.key)

        jira_url = f"{JIRA_SERVER}/browse/{issue.key}"
        console.print(f"   [{heal_status}] Jira ticket: [link={jira_url}]{issue.key}[/link]")
        return JiraResult(
            test_name=test_name, jira_id=issue.key, jira_url=jira_url,
            status="created", bug_type=bug_type, heal_status=heal_status,
        )
    except Exception as exc:
        log.warning(f"Jira ticket creation failed for {test_name}: {exc}")
        return JiraResult(
            test_name=test_name, jira_id="", jira_url="",
            status=f"failed: {str(exc)[:80]}", bug_type=bug_type, heal_status=heal_status,
        )


def _create_env_ticket(
    env_issue: EnvIssue,
    run_id: str = "",
    assignee_email: str = "",
) -> JiraResult:
    """Create a Jira Task ticket for an environmental issue that needs manual remediation."""
    test_name       = env_issue.get("test_name", "unknown")
    error_message   = env_issue.get("error_message", "")
    remediation     = env_issue.get("remediation_hint", "Manual investigation required.")

    try:
        jira = _get_jira_client()

        repo = os.getenv("GITHUB_REPOSITORY", "mohit-mungra-moschip/agentic_pipeline")
        server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")

        if run_id and run_id.isdigit():
            run_link = f"[{run_id}|{server_url}/{repo}/actions/runs/{run_id}]"
        else:
            run_link = run_id

        description = (
            f"*GitHub Pipeline Run:* {run_link}\n\n"
            f"*Affected Test:* `{test_name}`\n"
            f"*Classification:* ENV_ISSUE (not auto-fixable)\n\n"
            f"h3. Remediation Hint\n{remediation}\n\n"
            f"_Ticket auto-created by RegressionAI pipeline._"
        )

        issue = jira.create_issue(
            project=JIRA_PROJECT,
            summary=f"[RegressionAI] ENV_ISSUE | {test_name[:90]}",
            description=description,
            issuetype={"name": "Task"},
            priority={"name": "High"},
        )

        _assign_issue(jira, issue.key, assignee_email)
        _add_to_sprint(jira, issue.key)

        jira_url = f"{JIRA_SERVER}/browse/{issue.key}"
        console.print(f"   ENV ticket: [link={jira_url}]{issue.key}[/link]")
        return JiraResult(
            test_name=test_name, jira_id=issue.key, jira_url=jira_url,
            status="created", bug_type="ENV_ISSUE", heal_status="env_tracked",
        )
    except Exception as exc:
        log.warning(f"ENV Jira ticket creation failed for {test_name}: {exc}")
        return JiraResult(
            test_name=test_name, jira_id="", jira_url="",
            status=f"failed: {str(exc)[:80]}", bug_type="ENV_ISSUE", heal_status="env_tracked",
        )


# ── Main node ─────────────────────────────────────────────────────────────────

def jira_ticketing(state: AgentState) -> dict:
    """
    Jira Ticketing Agent — creates tickets for ALL classified failures:
      • Healed APP_BUG / TEST_BUG → Done ticket  (jira_results_healed)
      • Unhealed APP_BUG / TEST_BUG → Open ticket (jira_results)
      • ENV_ISSUE                  → Task ticket   (jira_results)
    """
    create_jira       = state.get("create_jira", False)
    failures          = state.get("failures", [])
    classifications   = state.get("failure_classifications", [])
    recommendations   = state.get("action_recommendations", [])
    root_cause        = state.get("root_cause") or {}
    run_id            = state.get("run_id", "unknown")
    healing_successful = state.get("healing_successful", False)
    healing_type      = state.get("healing_type", "NONE")
    approved_fixes    = state.get("approved_fixes", [])
    env_issues        = state.get("env_issues") or []

    console.print(f"\n[bold cyan]Jira Ticketing Agent[/bold cyan]")
    console.print(f"   Healing: {healing_type} | Healed: {healing_successful}")

    if not create_jira:
        console.print("   Jira creation is disabled for this run (create_jira=False). Skipping.")
        return {"jira_results": [], "jira_results_healed": [], "status": "done"}

    cls_map = {c["test_name"]: c for c in classifications}
    rec_map = {r["test_name"]: r for r in recommendations}

    commit_sha    = root_cause.get("commit_sha", "unknown")
    author_email  = root_cause.get("author_email", "")
    target_email  = author_email if (author_email and author_email != "unknown") else ""

    # Build a set of healed test names from approved_fixes
    healed_files = {fix.get("file_path", "") for fix in approved_fixes}

    results_unhealed: List[JiraResult] = []
    results_healed:   List[JiraResult] = []

    # ── Tickets for APP_BUG / TEST_BUG failures ──
    for failure in failures:
        test_name = failure.get("test_name", "unknown")
        cls       = cls_map.get(test_name, {"bug_type": "APP_BUG", "confidence": 50, "reasoning": ""})
        rec       = rec_map.get(test_name, {"priority": "Medium", "summary": "", "suggested_fix": "", "confidence": 50})
        bug_type  = cls.get("bug_type", "APP_BUG")

        if bug_type == "ENV_ISSUE":
            continue  # handled separately below

        # Determine if this specific test was healed. Since this node loops over
        # the currently failing tests, any test in this list is only healed if
        # the overall healing run was completely successful.
        is_healed = healing_successful
        heal_status = "healed" if is_healed else "unhealed"

        console.print(f"   → [{heal_status}] Creating ticket for: [cyan]{test_name[:60]}[/cyan]")
        result = _create_failure_ticket(
            test_name=test_name,
            bug_type=bug_type,
            error_message=failure.get("error_message", ""),
            ai_summary=rec.get("summary", cls.get("reasoning", "")),
            suggested_fix=rec.get("suggested_fix", "Manual investigation needed."),
            priority=rec.get("priority", "Medium"),
            confidence=cls.get("confidence", 50),
            heal_status=heal_status,
            commit_sha=commit_sha,
            run_id=run_id,
            assignee_email=target_email,
            approved_fixes=approved_fixes,
        )

        if is_healed:
            results_healed.append(result)
        else:
            results_unhealed.append(result)

    # ── Tickets for ENV_ISSUE failures ──
    if env_issues:
        console.print(f"\n   Creating {len(env_issues)} ENV_ISSUE ticket(s)...")
    for env_issue in env_issues:
        result = _create_env_ticket(env_issue, run_id=run_id, assignee_email=target_email)
        results_unhealed.append(result)

    # Summary
    created_unhealed = sum(1 for r in results_unhealed if r["status"] == "created")
    created_healed   = sum(1 for r in results_healed   if r["status"] == "created")
    console.print(
        f"\n   Jira done: [bold green]{created_healed}[/bold green] healed ticket(s), "
        f"[bold red]{created_unhealed}[/bold red] unhealed/env ticket(s)."
    )
    return {
        "jira_results":        results_unhealed,
        "jira_results_healed": results_healed,
        "status": "done",
    }


def heal_type_matches(healing_type: str, bug_type: str) -> bool:
    """Return True if the healing_type implies this bug_type was addressed."""
    if healing_type == "MIXED":
        return True
    if healing_type == "APP_HEAL" and bug_type == "APP_BUG":
        return True
    if healing_type == "TEST_HEAL" and bug_type == "TEST_BUG":
        return True
    return False
