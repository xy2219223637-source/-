from __future__ import annotations

import re
from typing import Any


_NUMBER_RANGE = re.compile(
    r"(?:第\s*)?(?P<start>\d+(?:\.\d+)?)\s*(?P<start_unit>ms|毫秒|s|秒)?\s*"
    r"(?:-|–|—|~|～|至|到)\s*(?:第\s*)?(?P<end>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>ms|毫秒|s|秒)",
    re.IGNORECASE,
)
_CLOCK_RANGE = re.compile(
    r"(?P<sm>\d{1,2}):(?P<ss>\d{2}(?:\.\d+)?)\s*"
    r"(?:-|–|—|~|～|至|到)\s*"
    r"(?P<em>\d{1,2}):(?P<es>\d{2}(?:\.\d+)?)"
)


def _to_ms(value: float, unit: str) -> int:
    return round(value if unit.lower() in {"ms", "毫秒"} else value * 1000)


def _normalize(start_ms: int, end_ms: int, duration_ms: int | None,
               *, source: str, confidence: float) -> dict[str, Any] | None:
    if start_ms < 0 or end_ms <= start_ms:
        return None
    if duration_ms is not None:
        if start_ms >= duration_ms:
            return None
        end_ms = min(end_ms, duration_ms)
    if end_ms <= start_ms:
        return None
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "precision": "EXACT",
        "source": source,
        "confidence": confidence,
    }


def normalize_input_ranges(rows: list[dict[str, Any]], duration_ms: int | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            if "start_ms" in row or "end_ms" in row:
                start_ms, end_ms = int(row["start_ms"]), int(row["end_ms"])
            else:
                start_ms = round(float(row["start"]) * 1000)
                end_ms = round(float(row["end"]) * 1000)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        normalized = _normalize(
            start_ms, end_ms, duration_ms, source="INPUT", confidence=1.0)
        if normalized:
            result.append(normalized)
    return result


def extract_time_ranges(text: str, duration_ms: int | None = None) -> list[dict[str, Any]]:
    matches: list[tuple[int, dict[str, Any]]] = []
    for match in _CLOCK_RANGE.finditer(text):
        start_ms = round((int(match["sm"]) * 60 + float(match["ss"])) * 1000)
        end_ms = round((int(match["em"]) * 60 + float(match["es"])) * 1000)
        normalized = _normalize(
            start_ms, end_ms, duration_ms, source="TEXT_PARSE", confidence=0.98)
        if normalized:
            matches.append((match.start(), normalized))
    occupied = [(match.start(), match.end()) for match in _CLOCK_RANGE.finditer(text)]
    for match in _NUMBER_RANGE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        start_unit = match["start_unit"] or match["unit"]
        normalized = _normalize(
            _to_ms(float(match["start"]), start_unit),
            _to_ms(float(match["end"]), match["unit"]),
            duration_ms,
            source="TEXT_PARSE",
            confidence=0.98,
        )
        if normalized:
            matches.append((match.start(), normalized))
    seen: set[tuple[int, int]] = set()
    result: list[dict[str, Any]] = []
    for _, value in sorted(matches, key=lambda row: row[0]):
        key = (value["start_ms"], value["end_ms"])
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def prioritized_time_ranges(input_ranges: list[dict[str, Any]], text: str,
                            duration_ms: int | None) -> list[dict[str, Any]]:
    explicit = normalize_input_ranges(input_ranges, duration_ms)
    return explicit or extract_time_ranges(text, duration_ms)
