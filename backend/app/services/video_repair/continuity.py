from __future__ import annotations

import re
from typing import Any


WEIGHTS = {
    "target_intent_match": .22, "pre_context_match": .16, "post_context_match": .16,
    "entity_action_match": .12, "visual_entry_match": .10, "visual_exit_match": .08,
    "duration_fit": .06, "source_reliability": .05, "quality_score": .05,
}


def _terms(value: str) -> set[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower()).strip()
    words = {item for item in normalized.split() if len(item) > 1}
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _similarity(left: str, right: str, *, neutral: float = .5) -> float:
    left_terms, right_terms = _terms(left), _terms(right)
    if not left_terms or not right_terms:
        return neutral
    return min(1.0, len(left_terms & right_terms) / max(1, min(len(left_terms), len(right_terms))))


def assess_continuity(
    candidate: dict[str, Any], context: dict[str, Any], *, target_duration: float,
    recommend_score: float = .75, selectable_score: float = .60,
    semantic_mode: str = "RULE_FALLBACK",
) -> dict[str, Any]:
    narrative = context.get("narrative") or {}
    candidate_text = " ".join(str(candidate.get(key) or "") for key in (
        "description", "reason", "speech_text", "content_role"))
    target_text = " ".join(str(narrative.get(key) or "") for key in (
        "target_intent", "target_summary", "required_bridge"))
    entities_actions = " ".join(map(str, (narrative.get("entities") or []) + (narrative.get("actions") or [])))
    duration = max(0.0, float(candidate.get("duration_seconds") or 0))
    same_orientation = bool(candidate.get("same_orientation", True))
    breakdown = {
        "target_intent_match": _similarity(target_text, candidate_text),
        "pre_context_match": _similarity(str(narrative.get("pre_summary") or ""), candidate_text),
        "post_context_match": _similarity(str(narrative.get("post_summary") or ""), candidate_text),
        "entity_action_match": _similarity(entities_actions, candidate_text),
        "visual_entry_match": 1.0 if same_orientation else .25,
        "visual_exit_match": 1.0 if same_orientation else .25,
        "duration_fit": max(0.0, 1.0 - abs(duration - target_duration) / max(target_duration, 1.0)),
        "source_reliability": 1.0 if candidate.get("asset_kind") == "material" else .65,
        "quality_score": min(1.0, max(0.0, float(candidate.get("quality_score") or .5))),
    }
    risk_penalty = 0.0
    warnings: list[str] = []
    risky_terms = ("百分百", "根治", "永久", "唯一", "第一", "竞品", "水印")
    if any(term in candidate_text for term in risky_terms):
        risk_penalty += .12
        warnings.append("候选文本包含高风险或绝对化表达")
    if not same_orientation:
        risk_penalty += .08
        warnings.append("候选画幅方向与原视频不一致")
    if semantic_mode == "RULE_FALLBACK":
        warnings.append("未执行语义模型评估，当前为规则降级评分")
    score = max(0.0, min(1.0, sum(WEIGHTS[key] * value for key, value in breakdown.items()) - risk_penalty))
    if semantic_mode == "RULE_FALLBACK":
        # Rule-only scores are useful for ordering, but not calibrated enough to
        # block a basic manual repair workflow.
        level = "SELECTABLE_WITH_WARNING"
    elif score >= recommend_score:
        level = "RECOMMENDED"
    elif score >= selectable_score:
        level = "SELECTABLE_WITH_WARNING"
    else:
        level = "BELOW_THRESHOLD"
    reasons = []
    if breakdown["target_intent_match"] >= .65:
        reasons.append("候选内容与目标修复意图相关")
    if breakdown["pre_context_match"] >= .65:
        reasons.append("候选能够承接前文")
    if breakdown["post_context_match"] >= .65:
        reasons.append("候选能够衔接后文")
    if breakdown["duration_fit"] >= .8:
        reasons.append("候选时长与目标区间匹配")
    if not reasons:
        reasons.append("候选通过素材硬约束，但语义连续性证据有限")
    breakdown["risk_penalty"] = round(risk_penalty, 4)
    return {
        "score": round(score, 4), "recommendation_level": level,
        "score_breakdown": {key: round(value, 4) for key, value in breakdown.items()},
        "reasons": reasons, "warnings": warnings, "semantic_mode": semantic_mode,
    }
