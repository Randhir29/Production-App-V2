"""PHP sort semantics (agent.md §8.3, D-15). Pure functions."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal
from functools import cmp_to_key


def php_usort[T](items: Iterable[T], key: Callable[[T], float], factor: float) -> list[T]:
    """Sort ascending like ``usort`` with ``fn($a, $b) => (int)(($a->k - $b->k) * factor)``.

    The float difference is truncated toward zero, so values closer than ``1 / factor`` compare
    equal and keep their original order (Python's sort is stable, like PHP 8).
    """

    def compare(a: T, b: T) -> int:
        return int((key(a) - key(b)) * factor)

    return sorted(items, key=cmp_to_key(compare))


def php_round(value: float | Decimal, precision: int = 0) -> float:
    """Round like PHP ``round()``: half away from zero (§8.3, D-16).

    Python's built-in ``round()`` uses banker's rounding on the binary float, which differs.
    The shortest repr of the float is rounded as a decimal, so ``2.675`` gives ``2.68``.
    Non-finite values are returned unchanged.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return value
    number = value if isinstance(value, Decimal) else Decimal(repr(value))
    quantum = Decimal(1).scaleb(-precision)
    rounded = abs(number).quantize(quantum, rounding=ROUND_HALF_UP)
    return float(-rounded if number < 0 else rounded)
