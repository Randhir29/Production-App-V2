"""Load test: the V2 performance report for many plants in parallel (agent.md §10 P7).

The legacy frontend fires V2 for every plant at once (~55). This script reproduces that against
a running API and prints latency percentiles and failures per HTTP status.

    LOAD_TOKEN=<access token> python -m scripts.load_v2 --base-url http://localhost:8000 \\
        --from-date 2026-01-01 --to-date 2026-01-07 --p95-target-s 60

The token comes from the environment (never argv) and is not printed. Exit code is 1 when the
error rate or the p95 target is exceeded, so it can gate a pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Sample:
    plant: str
    status: int  # 0 = transport error / timeout
    seconds: float


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True, slots=True)
class Summary:
    total: int
    ok: int
    by_status: dict[int, int]
    p50: float
    p95: float
    slowest: float
    wall_seconds: float

    @property
    def error_rate(self) -> float:
        return 0.0 if self.total == 0 else (self.total - self.ok) / self.total


def summarise(samples: Sequence[Sample], wall_seconds: float) -> Summary:
    ok = [s for s in samples if s.status == 200]
    latencies = [s.seconds for s in samples]
    return Summary(
        total=len(samples),
        ok=len(ok),
        by_status=dict(Counter(s.status for s in samples)),
        p50=percentile(latencies, 50),
        p95=percentile(latencies, 95),
        slowest=max(latencies, default=0.0),
        wall_seconds=wall_seconds,
    )


async def _one(
    client: httpx.AsyncClient, plant: str, from_date: str, to_date: str, gate: asyncio.Semaphore
) -> Sample:
    async with gate:
        started = time.perf_counter()
        try:
            response = await client.get(
                f"/api/v1/plants/{plant}/reports/performance-v2",
                params={"from_date": from_date, "to_date": to_date},
            )
            status = response.status_code
        except httpx.HTTPError:
            status = 0
        return Sample(plant, status, time.perf_counter() - started)


async def run(
    base_url: str,
    token: str,
    plants: Sequence[str] | None,
    from_date: str,
    to_date: str,
    concurrency: int,
    timeout_s: float,
) -> tuple[list[Sample], float]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_s) as client:
        if plants is None:
            listing = await client.get("/api/v1/plants")
            listing.raise_for_status()
            plants = [p["value"] for p in listing.json()["plants"]]
        gate = asyncio.Semaphore(concurrency)
        started = time.perf_counter()
        samples = await asyncio.gather(*(_one(client, p, from_date, to_date, gate) for p in plants))
        return list(samples), time.perf_counter() - started


def format_summary(summary: Summary, samples: Sequence[Sample]) -> str:
    lines = [
        (
            f"plants={summary.total} ok={summary.ok} error_rate={summary.error_rate:.1%} "
            f"wall={summary.wall_seconds:.1f}s"
        ),
        f"latency p50={summary.p50:.2f}s p95={summary.p95:.2f}s max={summary.slowest:.2f}s",
        "status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.by_status.items())),
    ]
    failures = [s for s in samples if s.status != 200]
    if failures:
        lines.append(
            "failed plants: " + ", ".join(f"{s.plant}({s.status or 'timeout'})" for s in failures[:20])
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V2 report load test")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--plants", help="Comma separated short names (default: GET /plants)")
    parser.add_argument("--concurrency", type=int, default=55)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--p95-target-s", type=float, default=0.0, help="0 = no latency gate")
    parser.add_argument("--max-error-rate", type=float, default=0.0)
    args = parser.parse_args(argv)

    token = os.environ.get("LOAD_TOKEN", "")
    plants = [p.strip() for p in args.plants.split(",")] if args.plants else None
    samples, wall = asyncio.run(
        run(args.base_url, token, plants, args.from_date, args.to_date, args.concurrency, args.timeout_s)
    )
    summary = summarise(samples, wall)
    print(format_summary(summary, samples))
    too_slow = args.p95_target_s > 0 and summary.p95 > args.p95_target_s
    return 1 if too_slow or summary.error_rate > args.max_error_rate else 0


if __name__ == "__main__":
    sys.exit(main())
