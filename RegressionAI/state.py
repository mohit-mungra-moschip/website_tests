"""
RegressionAI/state.py — Extended AgentState for the full regression pipeline.

Extends the original QAOps state with:
  - failure_classifications: AI classification per failure (TEST_BUG / APP_BUG / ENV_ISSUE)
  - confidence_scores: per-failure AI confidence (0-100)
  - root_cause: commit analysis result
  - action_recommendations: structured fix suggestions
  - jira_results: created Jira ticket IDs (for unhealed failures)
  - jira_results_healed: created Jira ticket IDs (for successfully healed failures)
  - healing_type: APP_HEAL | TEST_HEAL | MIXED | NONE — what kind of healing was applied
  - env_issues: list of ENV_ISSUE classified failures with remediation hints
  - pr_links: PR URLs generated during this run (for report embedding)
  - run_id: unique identifier for this regression run (stored in DB)
  - ci_mode: True when running in GitHub Actions (fully agentic, no human input)
"""
from __future__ import annotations
from typing import Annotated, TypedDict, Optional, List, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TestFailure(TypedDict):
    test_name: str
    file_path: str
    source_files: List[str]
    line_number: Optional[int]
    error_type: str
    error_message: str
    traceback: str


class FailureClassification(TypedDict):
    test_name: str
    bug_type: str           # TEST_BUG | APP_BUG | ENV_ISSUE | FLAKY
    confidence: int         # 0-100
    reasoning: str


class FileFix(TypedDict):
    file_path: str
    original_content: str
    fixed_content: str
    explanation: str
    diff: str


class RootCauseResult(TypedDict):
    commit_sha: str
    commit_message: str
    author: str
    author_email: str
    date: str
    changed_files: List[str]
    analysis: str
    confidence: int


class ActionRecommendation(TypedDict):
    test_name: str
    priority: str           # Critical | High | Medium | Low
    summary: str
    suggested_fix: str
    effort_hours: float
    confidence: int         # 0-100


class JiraResult(TypedDict):
    test_name: str
    jira_id: str
    jira_url: str
    status: str             # created | skipped | failed
    bug_type: str           # APP_BUG | TEST_BUG | ENV_ISSUE | FLAKY
    heal_status: str        # healed | unhealed | env_tracked


class EnvIssue(TypedDict):
    test_name: str
    error_message: str
    remediation_hint: str   # AI-suggested remediation step


class AgentState(TypedDict):
    # ── Inputs ──────────────────────────────────────────────
    project_path: str
    test_command: str
    run_id: str
    ci_mode: bool           # True = fully agentic, no human approval

    # ── Test results ────────────────────────────────────────
    test_output: str
    test_passed: bool
    failures: List[TestFailure]

    # ── Failure Analysis (NEW) ───────────────────────────────
    failure_classifications: List[FailureClassification]
    overall_confidence: int  # aggregate confidence score 0-100

    # ── File context ─────────────────────────────────────────
    relevant_files: Dict[str, str]

    # ── Self-Healing ─────────────────────────────────────────
    proposed_fixes: List[FileFix]
    approved_fixes: List[FileFix]
    applied_fix_log: List[str]
    healing_successful: bool
    healed_test_output: str

    # ── Root Cause Analysis (NEW) ────────────────────────────
    root_cause: Optional[RootCauseResult]

    # ── Action Recommendations (NEW) ─────────────────────────
    action_recommendations: List[ActionRecommendation]

    # ── Jira Results ─────────────────────────────────────────
    jira_results: List[JiraResult]          # Jira tickets for unhealed failures
    jira_results_healed: List[JiraResult]   # Jira tickets for healed failures (tracking)
    create_jira: bool        # toggle Jira creation per run

    # ── Healing Metadata ──────────────────────────────────────
    healing_type: str        # APP_HEAL | TEST_HEAL | MIXED | NONE
    env_issues: List[EnvIssue]  # ENV_ISSUE classified failures with hints

    # ── PR Links ──────────────────────────────────────────────
    pr_links: List[str]      # PR URLs created during this run

    # ── Control flow ────────────────────────────────────────
    status: str
    error: Optional[str]
    iteration: int
    max_iterations: int

    # ── Human feedback (only used when ci_mode=False) ────────
    user_feedback: Optional[str]

    # ── Conversation history ─────────────────────────────────
    messages: Annotated[List[BaseMessage], add_messages]
