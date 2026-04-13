"""LLM Cost Tracker — Track LLM API costs per request. Know where your tokens go.

Usage:
    from llm_cost_tracker import CostTracker

    tracker = CostTracker("./llm_costs.db")

    # After every LLM call:
    tracker.record(
        prompt_tokens=847,
        completion_tokens=234,
        model="gpt-4o-mini",
        provider="openai",
    )

    # Get a report:
    report = tracker.report(window="7d")
    print(f"Total cost: ${report['total_cost_usd']:.4f}")
    print(f"Total saved: ${report['total_saved_full_modeled_usd']:.4f}")

    # For local-handled requests (no LLM call made):
    tracker.record(
        prompt_tokens=0,
        completion_tokens=0,
        model="gpt-4o-mini",
        provider="openai",
        route="local",
        prompt_text="where is the login function defined",
    )

    # Periodic snapshot for dashboards:
    tracker.capture_snapshot(window_hours=24)
"""

from .tracker import CostTracker
from .pricing import DEFAULT_PRICING, lookup_pricing, approx_tokens

__version__ = "0.1.0"
__all__ = ["CostTracker", "DEFAULT_PRICING", "lookup_pricing", "approx_tokens"]
