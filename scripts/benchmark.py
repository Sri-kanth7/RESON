"""Measure RESON performance characteristics.

Every number reported in the readiness report and the implementation plan
comes from this script. It measures what actually exists; it does not invent
benchmarks.

Run with:  python scripts/benchmark.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running the script from anywhere: put the repository root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.collector import (
    SamplingPolicy,
    apply_sampling_policy,
    collection_to_metrics,
)
from src.data.collector_factory import create_collector
from src.data.memory_storage import InMemoryStorage
from src.data.monitoring import (
    latest_metric_values,
    metric_series,
    process_inventory,
    source_inventory,
)
from src.data.sqlite_storage import SQLiteStorage


def _time(fn, repeats: int = 5) -> tuple[float, float]:
    """Return (mean, min) seconds for ``repeats`` executions of ``fn``."""
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return statistics.mean(samples), min(samples)


def _seed(storage, count: int, host: str = "reson-bench") -> None:
    """Insert ``count`` metrics spread over one hour."""
    from uuid import uuid4

    from src.data.schemas import Environment, Metric

    base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    step = 3600.0 / max(1, count)
    records = [
        Metric(
            id=uuid4(),
            timestamp=base + timedelta(seconds=index * step),
            service=host,
            environment=Environment.PRODUCTION,
            metric_name=["cpu_percent", "memory_percent"][index % 2],
            value=20.0 + (index % 50),
            unit="percent",
            source="linux_local",
            host_id=host,
        )
        for index in range(count)
    ]
    # Bulk insert through the storage abstraction keeps the measurement about
    # the real write path.
    storage.save_metrics(records)


def _measure_demonstration() -> dict:
    """Measure the default demonstration timeline end to end.

    Reports how many signal pairs the correlation engine evaluates and how
    long the whole demonstration takes, for the in-memory backend and for the
    persistent SQLite backend. The pair count is a property of the data (it
    grows with the square of the signal count), the wall clock is a property
    of this machine.
    """
    import os

    from app.services.backend import build_application, run_demonstration

    result: dict = {}

    memory_app = build_application("memory")
    start = time.perf_counter()
    demo = run_demonstration(memory_app)
    result["memory_seconds"] = time.perf_counter() - start

    # The SQLite run uses a throwaway database file: the benchmark never
    # touches the user's real data directory.
    tmp = Path(tempfile.mkdtemp())
    previous = os.environ.get("RESON_SQLITE_PATH")
    os.environ["RESON_SQLITE_PATH"] = str(tmp / "demonstration.db")
    try:
        sqlite_app = build_application("sqlite")
        start = time.perf_counter()
        sqlite_demo = run_demonstration(sqlite_app)
        result["sqlite_seconds"] = time.perf_counter() - start
    finally:
        if previous is None:
            os.environ.pop("RESON_SQLITE_PATH", None)
        else:
            os.environ["RESON_SQLITE_PATH"] = previous

    incident_result = demo.incident_result
    result["batches"] = demo.batches
    result["records"] = demo.records
    result["pairs"] = len(incident_result.correlation_candidates)
    result["evidence"] = len(incident_result.correlation_evidence)
    result["incidents"] = len(incident_result.researches)
    result["signals"] = _signal_count(incident_result)

    # Both runs must produce the same number of pairs, evidence links and
    # incidents; a difference would mean the backend changed the outcome.
    sqlite_result = sqlite_demo.incident_result
    assert len(sqlite_result.correlation_candidates) == result["pairs"]
    assert len(sqlite_result.correlation_evidence) == result["evidence"]
    assert len(sqlite_result.researches) == result["incidents"]

    return result


def _signal_count(incident_result) -> int:
    """Return the number of distinct signals the engine evaluated."""
    identifiers = set()
    for candidate in incident_result.correlation_candidates:
        identifiers.add(candidate.source.source_id)
        identifiers.add(candidate.target.source_id)
    return len(identifiers)


def main() -> int:
    print("RESON performance measurements")
    print("=" * 60)
    print(f"Python {__import__('sys').version.split()[0]}")
    print()

    # ------------------------------------------------------------------
    # 1. Collector overhead
    # ------------------------------------------------------------------
    print("1. Collector overhead (one full collection cycle)")
    collector = create_collector()
    collector.collect_once()  # prime
    mean, best = _time(lambda: collector.collect_once(), repeats=10)
    print(f"   collect_once(): mean {mean * 1000:.1f} ms, best {best * 1000:.1f} ms")
    print(f"   host: {collector.host_id}  source: {collector.source}")
    print()

    # ------------------------------------------------------------------
    # 2. Database ingestion
    # ------------------------------------------------------------------
    print("2. SQLite ingestion throughput")
    tmp = Path(tempfile.mkdtemp())
    storage = SQLiteStorage(tmp / "bench.db")
    for total in (1_000, 10_000):
        _seed(storage, total)
        start = time.perf_counter()
        _seed(storage, total)
        elapsed = time.perf_counter() - start
        print(
            f"   {total:>6,} rows written in {elapsed:.3f}s "
            f"({total / elapsed:,.0f} rows/s)"
        )
    print()

    # ------------------------------------------------------------------
    # 3. Query latency
    # ------------------------------------------------------------------
    print("3. Query latency (SQLite, ~11k metric rows)")
    count = storage._counts()["metrics"]
    print(f"   rows in table: {count:,}")
    mean, best = _time(lambda: storage.get_metrics(service="reson-bench"))
    print(f"   get_metrics(service):      mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: storage.get_metrics(source="linux_local"))
    print(f"   get_metrics(source):       mean {mean * 1000:.1f} ms")
    mean, best = _time(
        lambda: storage.get_metrics(
            source="linux_local",
            start_time=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
            end_time=datetime(2026, 6, 1, 12, 45, tzinfo=timezone.utc),
        )
    )
    print(f"   get_metrics(bounded window): mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: source_inventory(storage))
    print(f"   source_inventory():        mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: metric_series(storage, source="linux_local"))
    print(f"   metric_series():           mean {mean * 1000:.1f} ms")
    mean, best = _time(
        lambda: latest_metric_values(storage, source="linux_local")
    )
    print(f"   latest_metric_values():    mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: process_inventory(storage))
    print(f"   process_inventory():       mean {mean * 1000:.1f} ms")
    print()

    print("   Dashboard-optimized reads (indexed, no full scan):")
    mean, best = _time(
        lambda: storage.get_latest_metrics(source="linux_local")
    )
    print(f"   get_latest_metrics():      mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: storage.count_records(source="linux_local"))
    print(f"   count_records():           mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: storage.list_sources())
    print(f"   list_sources():            mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: storage.list_hosts(source="linux_local"))
    print(f"   list_hosts():              mean {mean * 1000:.1f} ms")
    mean, best = _time(lambda: storage.latest_observation(source="linux_local"))
    print(f"   latest_observation():      mean {mean * 1000:.1f} ms")
    print()

    # ------------------------------------------------------------------
    # 4. In-memory backend (dashboard without a database)
    # ------------------------------------------------------------------
    print("4. In-memory backend (no disk)")
    memory = InMemoryStorage()
    _seed(memory, 10_000)
    mean, best = _time(lambda: memory.get_metrics(source="linux_local"))
    print(f"   get_metrics(source):       mean {mean * 1000:.1f} ms")
    print()

    # ------------------------------------------------------------------
    # 5. Metrics produced per cycle
    # ------------------------------------------------------------------
    print("5. Volume of telemetry produced per collection cycle")
    result = collector.collect_once()
    raw_records = collection_to_metrics(result)
    print(f"   unfiltered records/cycle:  {len(raw_records)}")
    print(f"   host samples per cycle:    {1}")
    print(f"   process samples per cycle: {len(result.processes)}")

    policy = SamplingPolicy()
    print(f"   policy: process_sample_every={policy.process_sample_every}, "
          f"slow_sample_every={policy.slow_sample_every}, "
          f"max_processes={policy.max_processes}")
    published_total = 0
    for cycle in range(1, 121):
        published = apply_sampling_policy(result, cycle, policy)
        published_total += len(collection_to_metrics(published))
    per_cycle = published_total / 120
    print(f"   published records/cycle:   {per_cycle:.1f} (averaged over 120 cycles)")
    for interval in (5, 10, 30):
        cycles_per_day = 86400 / interval
        per_day = per_cycle * cycles_per_day
        print(
            f"   at a {interval:>2}s interval: {per_day:>10,.0f} records/day "
            f"({per_day * 30 / 1_000_000:.1f}M over 30 days)"
        )
    print()

    # ------------------------------------------------------------------
    # 6. Retention pruning
    # ------------------------------------------------------------------
    print("6. Retention pruning (SQLite)")
    old = datetime.now(timezone.utc) - timedelta(days=400)
    from src.data.schemas import Environment, Metric

    stale = [
        Metric(
            timestamp=old + timedelta(seconds=i),
            service="reson-stale",
            environment=Environment.PRODUCTION,
            metric_name="cpu_percent",
            value=1.0,
            unit="percent",
            source="linux_local",
            host_id="reson-stale",
        )
        for i in range(5_000)
    ]
    storage.save_metrics(stale)
    start = time.perf_counter()
    removed = storage.purge_before(datetime.now(timezone.utc) - timedelta(days=30))
    elapsed = time.perf_counter() - start
    print(f"   purged {removed['metrics']:,} rows in {elapsed:.3f}s")
    print()

    # ------------------------------------------------------------------
    # 7. Default demonstration timeline (correlation pair sweep)
    # ------------------------------------------------------------------
    print("7. Default demonstration timeline (correlation engine)")
    demonstration = _measure_demonstration()
    print(f"   scenario batches:          {demonstration['batches']}")
    print(f"   telemetry records:          {demonstration['records']:,}")
    print(f"   signals evaluated:          {demonstration['signals']}")
    print(f"   candidate signal pairs:     {demonstration['pairs']:,}")
    print(f"   accepted evidence links:    {demonstration['evidence']:,}")
    print(f"   incidents produced:         {demonstration['incidents']}")
    print(
        f"   wall clock (in-memory):     "
        f"{demonstration['memory_seconds']:.2f} s"
    )
    print(
        f"   wall clock (sqlite):        "
        f"{demonstration['sqlite_seconds']:.2f} s"
    )
    print()

    print("=" * 60)
    print("Note: these figures describe THIS machine and dataset size.")
    print("Re-run scripts/benchmark.py on the target hardware before")
    print("quoting them as expected performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
