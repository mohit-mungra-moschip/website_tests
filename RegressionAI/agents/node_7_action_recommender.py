"""
RegressionAI/agents/action_recommender.py — Action Recommendation Engine (Node 7)

Uses: ACTION_RECOMMENDER_MODEL (default: google/gemini-2.0-flash)
"""
import json
import os
from typing import List
from rich.console import Console
from common_utils import AIWrapper, LLMConfig
from common_utils.logger import get_logger
from RegressionAI.state import AgentState, ActionRecommendation

from RegressionAI.skills import load_prompt

console = Console()
log = get_logger("action_recommender")
from common_utils.llm_config import get_model_from_env
ACTION_RECOMMENDER_MODEL = get_model_from_env("ACTION_RECOMMENDER_MODEL")

SYSTEM_PROMPT = load_prompt("node_7_action_recommender_prompt.md")


def _get_recommendation(failure: dict, classification: dict, root_cause: dict) -> ActionRecommendation:
    ai = AIWrapper(LLMConfig(model=ACTION_RECOMMENDER_MODEL, temperature=0.2), mode="llm")
    context = (
        f"Test: {failure.get('test_name', 'unknown')}\n"
        f"Error: {failure.get('error_type') or ''}: {(failure.get('error_message') or '')[:200]}\n"
        f"Classification: {classification.get('bug_type', 'APP_BUG')} "
        f"({classification.get('confidence', 50)}%) — {classification.get('reasoning', '')}\n"
        f"Root cause commit: {(root_cause or {}).get('commit_sha', 'N/A')[:8]}\n"
        f"Root cause analysis: {(root_cause or {}).get('analysis', 'N/A')[:300]}"
    )
    try:
        raw = ai.run(prompt=f"Recommend fix:\n{context}", system_prompt=SYSTEM_PROMPT)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```")
        parsed = json.loads(raw)
        return ActionRecommendation(
            test_name=failure.get("test_name", "unknown"),
            priority=parsed.get("priority", "Medium"),
            summary=parsed.get("summary", ""),
            suggested_fix=parsed.get("suggested_fix", ""),
            effort_hours=float(parsed.get("effort_hours", 2.0)),
            confidence=int(parsed.get("confidence", 50)),
        )
    except Exception as exc:
        return ActionRecommendation(
            test_name=failure.get("test_name", "unknown"),
            priority="Medium", summary="Manual investigation required",
            suggested_fix="Review traceback and fix root cause manually.",
            effort_hours=4.0, confidence=20,
        )


def action_recommendation_engine(state: AgentState) -> dict:
    failures = state.get("failures", [])
    classifications = state.get("failure_classifications", [])
    root_cause = state.get("root_cause")
    console.print(f"\n[bold yellow]Action Recommendation Engine[/bold yellow] — {ACTION_RECOMMENDER_MODEL}")
    cls_map = {c["test_name"]: c for c in classifications}
    recommendations: List[ActionRecommendation] = []
    for failure in failures:
        cls = cls_map.get(failure.get("test_name", ""), {"bug_type": "APP_BUG", "confidence": 50, "reasoning": ""})
        rec = _get_recommendation(failure, cls, root_cause)
        recommendations.append(rec)
        colors = {"Critical": "red", "High": "orange1", "Medium": "yellow", "Low": "green"}
        c = colors.get(rec["priority"], "white")
        console.print(f"   [{c}]{rec['priority']}[/{c}] — {rec['summary'][:80]} (~{rec['effort_hours']}h)")
    console.print(f"   {len(recommendations)} recommendation(s) generated.")
    return {"action_recommendations": recommendations, "status": "jira_ticketing"}
