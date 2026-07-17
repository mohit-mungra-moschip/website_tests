"""
RegressionAI Pipeline — Email Notifier
Sends a clean, minimal HTML report email after each pipeline run.
"""

import smtplib
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

# =============================================================================
# EMAIL CONFIG
# =============================================================================

SMTP_SERVER    = "smtp.office365.com"
SMTP_PORT      = 587
EMAIL_USERNAME = "mohit.mungra@moschip.com"
EMAIL_PASSWORD = "nvkdczbtwdlcbdqh"   # Use env var / app password in production
SENDER_EMAIL   = "mohit.mungra@moschip.com"

RECEIVER_EMAILS = [
    "mohit.mungra@moschip.com",
]

JIRA_BASE_URL    = "https://moschip-team-doibg33r.atlassian.net/browse"
GITHUB_REPO_URL  = "https://github.com/mohit-mungra-moschip/agentic_pipeline"


# =============================================================================
# HELPERS
# =============================================================================

def _pill(text: str, bg: str, color: str = "#fff") -> str:
    return (
        f'<span style="background:{bg};color:{color};border-radius:12px;'
        f'padding:2px 10px;font-size:11px;font-weight:600;'
        f'white-space:nowrap;">{text}</span>'
    )


def _link(url: str, label: str, color: str = "#4f46e5") -> str:
    return (
        f'<a href="{url}" style="color:{color};text-decoration:none;'
        f'font-weight:600;">{label}</a>'
    )


def _row(label: str, value: str) -> str:
    return (
        f'<tr>'
        f'<td style="padding:5px 0;color:#6b7280;font-size:12px;'
        f'white-space:nowrap;width:140px;vertical-align:top;">{label}</td>'
        f'<td style="padding:5px 0;color:#111827;font-size:12px;'
        f'vertical-align:top;">{value}</td>'
        f'</tr>'
    )


# =============================================================================
# =============================================================================
# HELPERS FOR REPORT PARSING
# =============================================================================

def _load_latest_json(run_id: str) -> dict:
    import json
    from pathlib import Path
    
    json_dir = Path("reports/json")
    if run_id:
        # Check for rerun report first
        path_rerun = json_dir / f"test_results_{run_id}_full_rerun.json"
        if path_rerun.exists():
            try:
                return json.loads(path_rerun.read_text(encoding="utf-8"))
            except Exception:
                pass
        path = json_dir / f"test_results_{run_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    if json_dir.exists():
        # Get all rerun and standard result json files
        paths = list(json_dir.glob("test_results_*_full_rerun.json")) + list(json_dir.glob("test_results_*.json"))
        if paths:
            paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in paths:
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
    return {}


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "—"
    secs = int(seconds)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


# =============================================================================
# HTML BUILDER
# =============================================================================

def _build_html(state: dict, run_id: str) -> str:
    payload = _load_latest_json(run_id)
    results = payload.get("results", []) or []
    
    total_tests = len(results)
    passed_tests = sum(1 for r in results if r.get("status") in ("PASS", "PASSED"))
    failed_tests = sum(1 for r in results if r.get("status") in ("FAIL", "FAILED", "ERROR"))
    skipped_tests = sum(1 for r in results if r.get("status") in ("SKIP", "SKIPPED"))
    
    healed_tests = sum(1 for r in results if r.get("status") in ("PASS", "PASSED") and (r.get("pr_url") or r.get("jira_id")))
    direct_pass = max(0, passed_tests - healed_tests)
    
    pass_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0.0
    pass_rate_str = f"{pass_rate:.1f}%"
    
    exec_seconds = payload.get("execution_seconds", 0.0)
    exec_time_str = _format_duration(exec_seconds)
    
    run_at_str = payload.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Release Status
    if failed_tests > 0:
        release_status = '<span style="color:#dc2626; font-weight:bold;">BLOCKER</span>'
    else:
        release_status = '<span style="color:#16a34a; font-weight:bold;">PASSED</span>'

    # Styles
    fail_style = 'color: #dc2626; font-weight: bold; background-color: #fee2e2;' if failed_tests > 0 else 'color: #475569;'
    pass_style = 'color: #16a34a; font-weight: bold; background-color: #dcfce7;' if pass_rate == 100.0 else 'color: #dc2626; font-weight: bold; background-color: #fee2e2;'

    # Group by Module (parent folder under tests/)
    modules = {}
    for r in results:
        node_id = r.get("test_id", "")
        file_path_part = node_id.split("::")[0] if node_id else ""
        parts = file_path_part.split("/") if file_path_part else []
        
        module_name = "unknown"
        if "tests" in parts:
            idx = parts.index("tests")
            if idx + 1 < len(parts):
                module_name = parts[idx + 1]
        elif len(parts) > 1:
            module_name = parts[-2]
        elif parts:
            module_name = parts[-1].replace(".py", "")
            
        if not module_name or module_name == "unknown":
            module_name = "unknown"
            
        if module_name not in modules:
            modules[module_name] = {
                "total": 0,
                "passed": 0,
                "healed": 0,
                "failed": 0,
                "skipped": 0,
            }
            
        stats = modules[module_name]
        stats["total"] += 1
        status = r.get("status", "").upper()
        if status in ("PASS", "PASSED"):
            stats["passed"] += 1
            if r.get("pr_url") or r.get("jira_id"):
                stats["healed"] += 1
        elif status in ("FAIL", "FAILED", "ERROR"):
            stats["failed"] += 1
        elif status in ("SKIP", "SKIPPED"):
            stats["skipped"] += 1

    # Table 2 Rows
    module_rows_html = ""
    for idx, (mod_name, m) in enumerate(sorted(modules.items())):
        bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
        m_pass_rate = (m["passed"] / m["total"] * 100) if m["total"] > 0 else 0.0
        
        m_fail_style = 'color: #dc2626; font-weight: bold; background-color: #fee2e2;' if m["failed"] > 0 else 'color: #1e293b;'
        m_heal_style = 'color: #7c3aed; font-weight: bold; background-color: #ede9fe;' if m["healed"] > 0 else 'color: #1e293b;'
        
        module_rows_html += f"""
        <tr style="background-color: {bg}; border-bottom: 1px solid #cbd5e1;">
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: left; font-weight: 500;">{mod_name}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{m["total"]}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #16a34a; font-weight: bold;">{m["passed"] - m["healed"]}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {m_heal_style}">{m["healed"]}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {m_fail_style}">{m["failed"]}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #64748b;">{m["skipped"]}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">{m_pass_rate:.1f}%</td>
        </tr>
        """
        
    total_fail_style = 'color: #dc2626; font-weight: bold; background-color: #fee2e2;' if failed_tests > 0 else 'color: #1e293b;'
    total_heal_style = 'color: #7c3aed; font-weight: bold; background-color: #ede9fe;' if healed_tests > 0 else 'color: #1e293b;'
    module_rows_html += f"""
    <tr style="background-color: #f1f5f9; font-weight: bold; border-top: 2px solid #94a3b8;">
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: left; font-weight: 700;">TOTAL</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{total_tests}</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #16a34a;">{direct_pass}</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {total_heal_style}">{healed_tests}</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {total_fail_style}">{failed_tests}</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #64748b;">{skipped_tests}</td>
      <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{pass_rate_str}</td>
    </tr>
    """

    # ── Table 3: Traceability & Self-Healing Links (Jira & PR links) ─────────────
    trace_rows_html = ""
    has_trace_rows = False
    for idx, r in enumerate(results):
        status = r.get("status", "").upper()
        is_failed = status in ("FAIL", "FAILED", "ERROR")
        
        pr_url = r.get("pr_url", "")
        jira_id = r.get("jira_id", "")
        jira_url = r.get("jira_url", "")
        if not jira_url and jira_id:
            jira_url = f"{JIRA_BASE_URL}/{jira_id}"
            
        is_healed = status in ("PASS", "PASSED") and (pr_url or jira_id)
        
        if is_failed or is_healed or jira_id or pr_url:
            has_trace_rows = True
            bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
            
            # Status badge
            if is_healed:
                status_lbl = '<span style="background:#ede9fe; color:#7c3aed; border-radius:12px; padding:2px 10px; font-size:11px; font-weight:600; white-space:nowrap;">Healed</span>'
            elif is_failed:
                status_lbl = '<span style="background:#fee2e2; color:#dc2626; border-radius:12px; padding:2px 10px; font-size:11px; font-weight:600; white-space:nowrap;">Failed</span>'
            else:
                status_lbl = '<span style="background:#dcfce7; color:#16a34a; border-radius:12px; padding:2px 10px; font-size:11px; font-weight:600; white-space:nowrap;">Passed</span>'
                
            # Jira link
            if jira_id:
                jira_lbl = f'<a href="{jira_url}" style="color:#2563eb; text-decoration:none; font-weight:600;">{jira_id}</a>'
            else:
                jira_lbl = '<span style="color:#94a3b8;">—</span>'
                
            # PR link
            if pr_url:
                pr_num = ""
                if "/pull/" in pr_url:
                    pr_num = pr_url.rstrip('/').split('/')[-1]
                pr_text = f"PR #{pr_num}" if (pr_num and pr_num.isdigit()) else "PR Link"
                pr_lbl = f'<a href="{pr_url}" style="color:#7c3aed; text-decoration:none; font-weight:600;">{pr_text}</a>'
            else:
                pr_lbl = '<span style="color:#94a3b8;">—</span>'
                
            tc_id = r.get("doc_test_case_id") or r.get("test_case_id") or "—"
            tc_name = r.get("test_name", "—")
            
            trace_rows_html += f"""
            <tr style="background-color: {bg}; border-bottom: 1px solid #cbd5e1;">
              <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-family: monospace;">{tc_id}</td>
              <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: left; font-weight: 500;">{tc_name}</td>
              <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{status_lbl}</td>
              <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{jira_lbl}</td>
              <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{pr_lbl}</td>
            </tr>
            """
            
    traceability_table_html = ""
    if has_trace_rows:
        traceability_table_html = f"""
    <!-- Table 3: Traceability & Self-Healing Links -->
    <h3 style="background-color: #1b365d; color: #ffffff; padding: 8px 12px; margin: 25px 0 0 0; font-size: 14px; font-weight: bold; border-top-left-radius: 4px; border-top-right-radius: 4px;">Traceability & Self-Healing Links</h3>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px; font-size: 13px;">
      <thead>
        <tr style="background-color: #1e3a8a; color: #ffffff;">
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold; width: 90px;">Test Case ID</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: left; font-weight: bold;">Test Case Name</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold; width: 80px;">Status</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold; width: 100px;">Jira Ticket</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold; width: 110px;">PR Link</th>
        </tr>
      </thead>
      <tbody>
        {trace_rows_html}
      </tbody>
    </table>
    """

    healing_type       = state.get("healing_type", "NONE")
    approved_fixes     = state.get("approved_fixes") or []
    root_cause         = state.get("root_cause") or {}
    pr_links           = state.get("pr_links") or []
    overall_confidence = state.get("overall_confidence") or state.get("summary", {}).get("overall_confidence", 0)

    # Root Cause
    root_html = ""
    if root_cause:
        commit  = root_cause.get("commit_sha", root_cause.get("likely_commit", root_cause.get("commit_hash", "—")))
        if commit and commit != "—" and len(commit) > 8:
            commit = commit[:8]
        author  = root_cause.get("author", "—")
        date    = root_cause.get("date", "—")
        message = root_cause.get("commit_message", root_cause.get("message", "—"))
        ai_note = root_cause.get("analysis", root_cause.get("ai_analysis", ""))

        root_html = f"""
        <div style="margin:24px 0 10px; border-top:1px solid #cbd5e1; padding-top:15px;">
            <h4 style="color: #1e3a8a; margin: 0 0 8px 0; font-size: 13px; font-weight: bold; text-transform: uppercase;">Likely Root Cause Analysis</h4>
            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #cbd5e1; border-collapse:collapse; font-size: 12px;">
              <tr style="background:#ffffff;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; width: 120px; color:#475569;">Commit</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-family: monospace;">{commit}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; color:#475569;">Author</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{author}</td>
              </tr>
              <tr style="background:#ffffff;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; color:#475569;">Date</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{date}</td>
              </tr>
              {f'<tr><td colspan="2" style="border: 1px solid #cbd5e1; padding:8px 10px; font-size:11px; color:#64748b; line-height:1.5;">{ai_note}</td></tr>' if ai_note else ""}
            </table>
        </div>"""

    # Healed Files
    healed_files = [f.get("file_path", "") for f in approved_fixes if f.get("file_path")]
    files_html = ""
    if healed_files:
        tags = " ".join(
            f'<code style="background:#ede9fe; color:#5b21b6; border-radius:4px; padding:1px 6px; font-size:11px; margin:1px;">{f}</code>'
            for f in healed_files
        )
        files_html = f"""
        <tr style="background:#ffffff;">
          <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; width: 120px; color:#475569;">Healed Files</td>
          <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{tags}</td>
        </tr>"""

    # Extra Details table
    pipeline_details_table = ""
    run_url = f"{GITHUB_REPO_URL}/actions/runs/{run_id}"
    if healing_type != "NONE" or pr_links:
        pipeline_details_table = f"""
        <div style="margin:24px 0 10px; border-top:1px solid #cbd5e1; padding-top:15px;">
            <h4 style="color: #1e3a8a; margin: 0 0 8px 0; font-size: 13px; font-weight: bold; text-transform: uppercase;">Pipeline details</h4>
            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #cbd5e1; border-collapse:collapse; font-size: 12px;">
              <tr style="background:#ffffff;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; width: 120px; color:#475569;">Pipeline Run</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{_link(run_url, f"Run #{run_id}")}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; color:#475569;">Healing Type</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{healing_type}</td>
              </tr>
              <tr style="background:#ffffff;">
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px; font-weight: bold; color:#475569;">AI Confidence</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px 10px;">{overall_confidence}%</td>
              </tr>
              {files_html}
            </table>
        </div>"""

    # Assemble HTML
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0; padding:0; background-color:#ffffff; font-family:Arial, Helvetica, sans-serif; color:#1e293b;">
  <div style="max-width:800px; margin: 0 auto; padding: 20px;">
    
    <h2 style="color: #1e3a8a; margin-top: 0; margin-bottom: 20px; font-size: 20px; font-weight: bold; border-bottom: 2px solid #1e3a8a; padding-bottom: 10px;">Agentic Solution - Test Execution Report</h2>
    
    <p style="font-size: 14px; margin-bottom: 15px; color:#1e293b;">Hi Team,</p>
    <p style="font-size: 14px; margin-bottom: 20px; color:#1e293b;">Please find below the <strong>Agentic Solution</strong> Test Execution Report with module-wise results.</p>
    
    <!-- Table 1: Overall Execution Summary -->
    <h3 style="background-color: #1b365d; color: #ffffff; padding: 8px 12px; margin: 20px 0 0 0; font-size: 14px; font-weight: bold; border-top-left-radius: 4px; border-top-right-radius: 4px;">Overall Execution Summary</h3>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 13px;">
      <thead>
        <tr style="background-color: #1e3a8a; color: #ffffff;">
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Total Tests</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Passed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Healed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Failed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Pass %</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Exec Time</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Run At</th>
        </tr>
      </thead>
      <tbody>
        <tr style="background-color: #f8fafc;">
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold; color: #1e293b;">{total_tests}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #16a34a; font-weight: bold;">{direct_pass}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #7c3aed; font-weight: bold; background-color: #ede9fe;">{healed_tests}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {fail_style}">{failed_tests}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; {pass_style}">{pass_rate_str}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; color: #1e293b;">{exec_time_str}</td>
          <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-family: monospace; color: #1e293b;">{run_at_str}</td>
        </tr>
      </tbody>
    </table>
    
    <p style="margin: 10px 0 25px 0; font-size: 14px; font-weight: bold; color: #1e293b;">
      Release Status: {release_status}
    </p>

    <!-- Table 2: Modulewise Results -->
    <h3 style="background-color: #1b365d; color: #ffffff; padding: 8px 12px; margin: 20px 0 0 0; font-size: 14px; font-weight: bold; border-top-left-radius: 4px; border-top-right-radius: 4px;">Tests</h3>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px; font-size: 13px;">
      <thead>
        <tr style="background-color: #1e3a8a; color: #ffffff;">
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: left; font-weight: bold;">Module</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Total</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Passed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Healed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Failed</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Skipped</th>
          <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: center; font-weight: bold;">Pass %</th>
        </tr>
      </thead>
      <tbody>
        {module_rows_html}
      </tbody>
    </table>

    <!-- Traceability details (rest of features) -->
    {traceability_table_html}
    {root_html}
    {pipeline_details_table}

  </div>
</body>
</html>"""


# =============================================================================
# SEND
# =============================================================================

def send_pipeline_report(state: dict, run_id: str) -> None:
    """
    Build and send the HTML pipeline report email.
    Called at the end of regression_runner.py — non-blocking.
    """
    # Load test results from latest JSON payload to get final counts
    payload = _load_latest_json(run_id)
    results = payload.get("results", []) or []
    
    total_tests = len(results)
    failed_tests = sum(1 for r in results if r.get("status") in ("FAIL", "FAILED", "ERROR"))
    healed_tests = sum(1 for r in results if r.get("status") in ("PASS", "PASSED") and (r.get("pr_url") or r.get("jira_id")))

    if failed_tests > 0:
        tag = f"{failed_tests} FAILURE(S)"
    elif healed_tests > 0:
        tag = "ALL HEALED"
    else:
        tag = "ALL PASSED"

    subject   = f"Agentic Solution - Test Execution Report ({tag})"
    html_body = _build_html(state, run_id)

    msg = MIMEMultipart("alternative")
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECEIVER_EMAILS)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAILS, msg.as_string())
        print(f"Pipeline report email sent -> {', '.join(RECEIVER_EMAILS)}")
    except Exception as exc:
        print(f"Email send failed (non-critical): {exc}")
