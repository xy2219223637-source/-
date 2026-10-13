from __future__ import annotations

from dataclasses import dataclass


V4_STATES = frozenset({
    "ANALYZING",
    "AWAITING_MANUAL_DECISION",
    "AWAITING_MATERIAL",
    "CONFIGURING",
    "PREVIEW_QUEUED",
    "PREVIEW_RENDERING",
    "PREVIEW_READY",
    "COMPLETED",
    "FAILED",
    "CANCELED",
})

TERMINAL_STATES = frozenset({"COMPLETED", "CANCELED"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "ANALYZING": frozenset({"CONFIGURING", "AWAITING_MANUAL_DECISION", "FAILED", "CANCELED"}),
    "AWAITING_MANUAL_DECISION": frozenset({"ANALYZING", "CONFIGURING", "CANCELED"}),
    "AWAITING_MATERIAL": frozenset({"CONFIGURING", "CANCELED"}),
    "CONFIGURING": frozenset({"ANALYZING", "AWAITING_MATERIAL", "PREVIEW_QUEUED", "CANCELED"}),
    "PREVIEW_QUEUED": frozenset({"PREVIEW_RENDERING", "CONFIGURING", "FAILED", "CANCELED"}),
    "PREVIEW_RENDERING": frozenset({"PREVIEW_READY", "FAILED", "CANCELED"}),
    "PREVIEW_READY": frozenset({"ANALYZING", "CONFIGURING", "COMPLETED", "CANCELED"}),
    "FAILED": frozenset({"ANALYZING", "CONFIGURING", "PREVIEW_QUEUED", "CANCELED"}),
    "COMPLETED": frozenset(),
    "CANCELED": frozenset(),
}


@dataclass(frozen=True)
class InvalidRepairTransition(ValueError):
    current: str
    target: str

    def __str__(self) -> str:
        return f"不允许的视频返修状态流转: {self.current} -> {self.target}"


def validate_transition(current: str, target: str) -> None:
    if current not in V4_STATES:
        raise ValueError(f"未知的视频返修状态: {current}")
    if target not in V4_STATES:
        raise ValueError(f"未知的视频返修状态: {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidRepairTransition(current, target)


def transition(task, target: str):
    validate_transition(task.status, target)
    task.status = target
    return task
