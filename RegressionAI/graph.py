"""
RegressionAI/graph.py — Full LangGraph pipeline matching the flow diagram.

Flow:
  START
    └─▶ run_tests
          ├─ [passed]  ──────────────────────────────────────────────────▶ END
          └─ [failed]  ──▶ parse_failures
                           └─▶ fetch_files
                                 └─▶ failure_analysis  (AI: TEST_BUG vs APP_BUG vs ENV_ISSUE)
                                       └─▶ root_cause_commit_analysis (AI)
                                             └─▶ self_healing  (AI: auto-fix + re-run)
                                                   ├─ [success / fail] ──▶ action_recommendation_engine  (AI)
                                                   |                          └─▶ jira_ticketing  (AI: healed + unhealed tickets)
                                                   |                                └─▶ END (Reports + PRs)
                                                   └─ [retry]  ─────────▶ run_tests (up to max_iterations)
"""
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from RegressionAI.state import AgentState
from RegressionAI.agents import (
    run_tests,
    parse_failures,
    fetch_files,
    failure_analysis,
    self_healing,
    root_cause_commit_analysis,
    action_recommendation_engine,
    jira_ticketing,
)


# ── Routing functions ─────────────────────────────────────────────────────────

def after_run_tests(state: AgentState) -> str:
    if state.get("test_passed"):
        return "end"
    if state.get("status") == "error":
        return "end"
    return "parse_failures"


def after_self_healing(state: AgentState) -> str:
    """
    After self-healing:
      - success  → action_recommendation_engine (to create healed Jira tickets + PRs)
      - failure  → run_tests (if under iteration limit) or action_recommendation_engine
      - error    → end
    """
    if state.get("status") == "error":
        return "end"
    # Check iteration limit for retry loop
    if not state.get("healing_successful") and state.get("iteration", 0) < state.get("max_iterations", 3):
        return "run_tests"
    # Whether healed or not, always run through recommendations + Jira for tracking
    return "recommend"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    import functools

    def wrap_node(node_name, node_func):
        @functools.wraps(node_func)
        def wrapper(state):
            from common_utils.token_tracker import token_tracker
            token_tracker.set_current_node(node_name)
            try:
                result = node_func(state)
                return result
            finally:
                token_tracker.set_current_node(None)
        return wrapper

    # Register all nodes
    g.add_node("run_tests",                   wrap_node("run_tests", run_tests))
    g.add_node("parse_failures",              wrap_node("parse_failures", parse_failures))
    g.add_node("fetch_files",                 wrap_node("fetch_files", fetch_files))
    g.add_node("failure_analysis",            wrap_node("failure_analysis", failure_analysis))
    g.add_node("root_cause_commit_analysis",  wrap_node("root_cause_commit_analysis", root_cause_commit_analysis))
    g.add_node("self_healing",                wrap_node("self_healing", self_healing))
    g.add_node("action_recommendation_engine", wrap_node("action_recommendation_engine", action_recommendation_engine))
    g.add_node("jira_ticketing",              wrap_node("jira_ticketing", jira_ticketing))

    # Edges
    g.add_edge(START, "run_tests")

    g.add_conditional_edges(
        "run_tests",
        after_run_tests,
        {"parse_failures": "parse_failures", "end": END},
    )

    # Linear: parse → fetch → classify → root_cause → heal
    g.add_edge("parse_failures",  "fetch_files")
    g.add_edge("fetch_files",     "failure_analysis")
    g.add_edge("failure_analysis", "root_cause_commit_analysis")
    g.add_edge("root_cause_commit_analysis", "self_healing")

    # Self-healing branches — always flow to recommend/jira for Jira ticket creation
    g.add_conditional_edges(
        "self_healing",
        after_self_healing,
        {"end": END, "run_tests": "run_tests", "recommend": "action_recommendation_engine"},
    )

    # Escalation + healed path: recommend → jira → end
    g.add_edge("action_recommendation_engine", "jira_ticketing")
    g.add_edge("jira_ticketing",               END)

    return g


def compile_graph():
    """Return a compiled graph with in-memory checkpointer."""
    memory = MemorySaver()
    return build_graph().compile(checkpointer=memory)
