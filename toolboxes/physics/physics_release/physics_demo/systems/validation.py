"""Shared finite numeric input validation for scene factories."""
import math


def _number(value, name: str, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number in [{lower}, {upper}]")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number in [{lower}, {upper}]") from None
    if not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError(f"{name} must be a finite number in [{lower}, {upper}]")
    return number
