"""Deterministic frame budgets and the bounded v2 millisecond adapter."""
from __future__ import annotations

from fractions import Fraction


TIMING_VERSION = "v2-frame-budget-1"
PRECISE_TIMING_VERSION = "v3-frame-sample-1"
AUDIO_RATE = 48000


def frame_rate(fps) -> Fraction:
    rate = Fraction(str(fps))
    if rate <= 0:
        raise ValueError("Output frame rate must be positive")
    return rate


def ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def required_frames(duration_ms: int, fps) -> int:
    rate = frame_rate(fps)
    return ceil_div(duration_ms * rate.numerator, 1000 * rate.denominator)


def frame_at_ms(point: int, fps) -> int:
    return round(Fraction(point, 1000) * frame_rate(fps))


def ms_at_frame(frame: int, fps) -> int:
    return round(Fraction(frame * 1000, 1) / frame_rate(fps))


def candidate_frame_bounds(start_ms: int, end_ms: int, fps) -> tuple[int, int]:
    rate = frame_rate(fps)
    # V2 serializes frame endpoints to the nearest millisecond. Restore only
    # that half-millisecond uncertainty, never a whole extra candidate frame.
    left = Fraction(2 * start_ms - 1, 2000) * rate
    right = Fraction(2 * end_ms + 1, 2000) * rate
    return max(0, ceil_div(left.numerator, left.denominator)), right.numerator // right.denominator


def technical_tail_allowed(missing: int, rate: int, fps) -> bool:
    """Accept at most one frame plus v2 endpoint quantization, never shortage."""
    return 0 <= missing and Fraction(missing, rate) <= 1 / frame_rate(fps) + Fraction(1, 1000)


def sample_at_frame(frame: int, fps, rate: int = AUDIO_RATE) -> int:
    value = Fraction(frame * rate, 1) / frame_rate(fps)
    return ceil_div(value.numerator, value.denominator)


def frames_for_samples(samples: int, fps, *, start_frame: int = 0, rate: int = AUDIO_RATE) -> int:
    if type(samples) is not int or samples <= 0 or rate <= 0:
        raise ValueError("配音样本元数据无效")
    target = sample_at_frame(start_frame, fps, rate) + samples
    value = Fraction((target - 1), rate) * frame_rate(fps)
    return value.numerator // value.denominator + 1 - start_frame


def precise_tail_allowed(missing: int, fps, *, display_ms=0, rate: int = AUDIO_RATE) -> bool:
    if missing < 0:
        return False
    return Fraction(missing, rate) < 1 / frame_rate(fps) or (0 < display_ms <= 100 and missing * 1000 <= display_ms * rate)
