"""PHP sort semantics (agent.md §8.3, D-15). Pure functions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import cmp_to_key


def php_usort[T](items: Iterable[T], key: Callable[[T], float], factor: float) -> list[T]:
    """Sort ascending like ``usort`` with ``fn($a, $b) => (int)(($a->k - $b->k) * factor)``.

    The float difference is truncated toward zero, so values closer than ``1 / factor`` compare
    equal and keep their original order (Python's sort is stable, like PHP 8).
    """

    def compare(a: T, b: T) -> int:
        return int((key(a) - key(b)) * factor)

    return sorted(items, key=cmp_to_key(compare))
