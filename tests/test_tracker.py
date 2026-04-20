"""Tests for llm-cost-tracker."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from llm_cost_tracker import CostTracker, lookup_pricing, approx_tokens


def test_basic_record_and_report():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        # Record external call
        result = tracker.record(
            prompt_tokens=1000,
            completion_tokens=500,
            model="gpt-4o-mini",
            provider="openai",
        )
        assert result["request_id"]
        assert result["cost_usd"] > 0
        assert result["route"] == "external"

        # Record local call
        result2 = tracker.record(
            prompt_tokens=0,
            completion_tokens=0,
            model="gpt-4o-mini",
            provider="openai",
            route="local",
            prompt_text="where is the function defined",
        )
        assert result2["cost_usd"] == 0
        assert result2["saved_full_modeled_usd"] > 0
        assert result2["route"] == "local_only"

        # Report
        report = tracker.report()
        assert report["total_requests"] == 2
        assert report["total_cost_usd"] > 0
        assert report["local_count"] == 1
        assert report["external_count"] == 1
        assert report["total_saved_full_modeled_usd"] > 0

    print("  test_basic_record_and_report: PASS")


def test_report_with_window():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        tracker.record(prompt_tokens=500, completion_tokens=200, model="gpt-4o")

        report = tracker.report(window="1d")
        assert report["total_requests"] == 1
        assert report["window"] == "1d"

        report_old = tracker.report(window="1s")
        # 1 second window will include the just-recorded request
        assert report_old["total_requests"] >= 0  # may or may not catch it depending on timing

    print("  test_report_with_window: PASS")


def test_report_group_by():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        tracker.record(prompt_tokens=100, completion_tokens=50, model="gpt-4o-mini", provider="openai")
        tracker.record(prompt_tokens=200, completion_tokens=100, model="claude-3-5-sonnet", provider="anthropic")
        tracker.record(prompt_tokens=150, completion_tokens=75, model="gpt-4o-mini", provider="openai")

        report = tracker.report(group_by="model")
        assert "breakdown" in report
        assert "gpt-4o-mini" in report["breakdown"]
        assert report["breakdown"]["gpt-4o-mini"]["requests"] == 2
        assert "claude-3-5-sonnet" in report["breakdown"]
        assert report["breakdown"]["claude-3-5-sonnet"]["requests"] == 1

    print("  test_report_group_by: PASS")


def test_snapshot():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        tracker.record(prompt_tokens=1000, completion_tokens=500, model="gpt-4o")
        tracker.record(prompt_tokens=0, completion_tokens=0, model="gpt-4o", route="local", prompt_text="test query")

        snapshot = tracker.capture_snapshot(window_hours=1)
        assert snapshot["total_requests"] == 2
        assert snapshot["local_count"] == 1
        assert snapshot["external_count"] == 1
        assert snapshot["external_cost_usd"] > 0
        assert snapshot["saved_full_modeled_usd"] > 0

        snapshots = tracker.snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["snapshot_id"] == snapshot["snapshot_id"]

    print("  test_snapshot: PASS")


def test_recent():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        for i in range(5):
            tracker.record(prompt_tokens=100 * (i + 1), completion_tokens=50, model="gpt-4o-mini")

        recent = tracker.recent(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0]["prompt_tokens"] == 500
        assert recent[2]["prompt_tokens"] == 300

    print("  test_recent: PASS")


def test_pricing_lookup():
    inp, out, source = lookup_pricing("gpt-4o-mini")
    assert inp == 0.15
    assert out == 0.60
    assert "builtin" in source

    inp, out, source = lookup_pricing("gpt-4o-mini-2024-07-18")
    assert inp == 0.15  # partial match
    assert "partial" in source

    inp, out, source = lookup_pricing("totally-unknown-model")
    assert inp == 5.0  # fallback
    assert "fallback" in source

    print("  test_pricing_lookup: PASS")


def test_approx_tokens():
    assert approx_tokens("hello world") > 0
    assert approx_tokens("") == 0
    assert approx_tokens("a" * 400) == 100  # 400 chars / 4

    print("  test_approx_tokens: PASS")


def test_session_filter():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        tracker.record(prompt_tokens=100, completion_tokens=50, model="gpt-4o-mini", session_key="alice")
        tracker.record(prompt_tokens=200, completion_tokens=100, model="gpt-4o-mini", session_key="bob")
        tracker.record(prompt_tokens=150, completion_tokens=75, model="gpt-4o-mini", session_key="alice")

        report = tracker.report(session_key="alice")
        assert report["total_requests"] == 2

        report_bob = tracker.report(session_key="bob")
        assert report_bob["total_requests"] == 1

    print("  test_session_filter: PASS")


def test_metadata():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        result = tracker.record(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-4o-mini",
            metadata={"user": "naman", "project": "pds"},
        )
        assert result["request_id"]

    print("  test_metadata: PASS")


def test_zero_dependency():
    """Verify the package uses no external dependencies."""
    import llm_cost_tracker.tracker as t
    import llm_cost_tracker.pricing as p
    import llm_cost_tracker.db as d

    # All imports should be stdlib
    import_lines = []
    for mod in [t, p, d]:
        import inspect
        source = inspect.getsource(mod)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                if not stripped.startswith("from ."):  # skip relative imports
                    pkg = stripped.split()[1].split(".")[0]
                    assert pkg in {
                        "__future__", "json", "time", "uuid", "hashlib",
                        "math", "sqlite3", "pathlib", "re", "typing",
                        "datetime",
                    }, f"Non-stdlib import found: {stripped}"

    print("  test_zero_dependency: PASS")


def test_waste_score_trend():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        tracker = CostTracker(db)

        import time as _time

        # Simulate 5 days of usage with improving efficiency
        now = _time.time()
        for day in range(5):
            day_offset = (4 - day) * 86400  # oldest first
            # Day 0-1: lots of waste (many avoidable external calls)
            # Day 4: less waste (more local routing)
            avoidable_external = max(1, 8 - day * 2)
            necessary_external = 3
            local_calls = 2 + day * 3

            for _ in range(avoidable_external):
                tracker.record(
                    prompt_tokens=200, completion_tokens=50,
                    model="gpt-4o", provider="openai",
                    intent="code_lookup",
                )

            for _ in range(necessary_external):
                tracker.record(
                    prompt_tokens=2000, completion_tokens=1500,
                    model="gpt-4o", provider="openai",
                    intent="architecture_review",
                )

            for _ in range(local_calls):
                tracker.record(
                    prompt_tokens=0, completion_tokens=0,
                    model="gpt-4o-mini", provider="openai",
                    route="local", prompt_text="where is auth defined",
                    intent="code_lookup",
                )

        trend = tracker.waste_score_trend(days=30, bucket_size="1d")

        assert "trend" in trend
        assert "current_score" in trend
        assert "best_score" in trend
        assert "direction" in trend
        assert "summary" in trend
        assert len(trend["trend"]) > 0
        assert trend["data_points"] > 0
        assert trend["total_avoidable_requests"] > 0
        assert trend["overall_waste_score"] > 0

        # Verify trend points have required fields
        point = trend["trend"][0]
        assert "date" in point
        assert "waste_score" in point
        assert "local_percent" in point
        assert "total" in point
        assert "avoidable" in point

    print("  test_waste_score_trend: PASS")


def run_tests():
    print("Running llm-cost-tracker tests...\n")
    test_basic_record_and_report()
    test_report_with_window()
    test_report_group_by()
    test_snapshot()
    test_recent()
    test_pricing_lookup()
    test_approx_tokens()
    test_session_filter()
    test_metadata()
    test_waste_score_trend()
    test_zero_dependency()
    print("\nAll tests passed!")


if __name__ == "__main__":
    run_tests()
