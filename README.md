# LLM Cost Tracker

**Track LLM API costs per request. Know where your tokens go.**

Zero dependencies. Pure Python. Works with any LLM provider.

```bash
pip install llm-costlog
```

## Quickstart

```python
from llm_cost_tracker import CostTracker

tracker = CostTracker("./llm_costs.db")

# Track an OpenAI call
tracker.record(
    prompt_tokens=847,
    completion_tokens=234,
    model="gpt-4o-mini",
    provider="openai",
)

# Track an Anthropic call
tracker.record(
    prompt_tokens=1200,
    completion_tokens=890,
    model="claude-3-5-sonnet",
    provider="anthropic",
)

# Track a request you handled locally (no LLM call)
tracker.record(
    prompt_tokens=0,
    completion_tokens=0,
    model="gpt-4o-mini",
    provider="openai",
    route="local",
    prompt_text="where is the login function defined",
    intent="code_lookup",
)

# See where your money is going
report = tracker.report(window="7d")
print(f"Total cost: ${report['total_cost_usd']:.4f}")
print(f"Total tokens: {report['total_tokens']:,}")
print(f"Requests: {report['total_requests']}")
print(f"Local vs external: {report['local_count']} / {report['external_count']}")
print(f"Estimated savings: ${report['total_saved_full_modeled_usd']:.4f}")
print(f"Cost by model: {report['cost_by_model']}")

# The important part — how much are you wasting?
print(f"\n--- Waste Analysis ---")
print(f"Avoidable external requests: {report['avoidable_external_requests']}")
print(f"Money wasted on unnecessary LLM calls: ${report['avoidable_cost_usd']:.4f}")
print(f"Additional savings from model downgrades: ${report['potential_model_downgrade_savings_usd']:.4f}")
print(report['optimization_summary'])
```

## What it tracks

Every call to `tracker.record()` stores:

- **Tokens used** — prompt + completion, per request
- **Cost in USD** — calculated from built-in pricing tables (40+ models)
- **Route** — was this handled locally or sent to an LLM?
- **Counterfactual savings** — if you handled it locally, how much did you save vs sending it to the LLM?
- **Model, provider, intent, session** — slice your costs any way you want

## Reports

```python
# Last 7 days, grouped by model
report = tracker.report(window="7d", group_by="model")

# Last 24 hours, specific session
report = tracker.report(window="1d", session_key="user-123")

# All time
report = tracker.report()
```

Report fields:
- `total_requests`, `total_cost_usd`, `total_tokens`
- `total_prompt_tokens`, `total_completion_tokens`
- `local_count`, `external_count`
- `total_saved_prompt_only_usd`, `total_saved_full_modeled_usd`
- `requests_by_route`, `cost_by_model`, `cost_by_provider`
- `tokens_by_model`, `savings_by_intent`
- `avoidable_external_requests` — requests sent to LLM that didn't need one
- `avoidable_cost_usd` — money wasted on those unnecessary calls
- `avoidable_percent` — what % of your external calls were avoidable
- `potential_model_downgrade_savings_usd` — savings from using cheaper models
- `optimization_summary` — human-readable summary of waste found
- `breakdown` (when `group_by` is specified)

## Snapshots (for dashboards & cron jobs)

```python
# Capture a daily snapshot
snapshot = tracker.capture_snapshot(window_hours=24, job_name="daily-cost-report")
print(f"Net savings: ${snapshot['net_savings_conservative_usd']:.4f}")

# View recent snapshots
for s in tracker.snapshots(limit=7):
    print(f"{s['job_name']}: saved ${s['saved_full_modeled_usd']:.4f}, spent ${s['external_cost_usd']:.4f}")
```
## Waste score trend (v0.2.0)

Track how your efficiency improves over time. The waste score is the percentage of external API calls that were avoidable.

```python
trend = tracker.waste_score_trend(days=30)
print(trend["summary"])
# Waste score: 20.0% (↓ improving). 43 of 71 external calls were avoidable ($0.03 wasted) over 30 days.

print(f"Direction: {trend['direction']}")      # improving / worsening / stable
print(f"Current: {trend['current_score']}%")   # today's waste score
print(f"Best: {trend['best_score']}%")         # lowest waste score achieved

for point in trend["trend"]:
    print(f"  {point['date']}  waste={point['waste_score']}%  ({point['avoidable']}/{point['external']} avoidable)")
```

Output:
```
  2026-04-12  waste=75.0%  (12/16 avoidable)
  2026-04-14  waste=66.7%  (8/12 avoidable)
  2026-04-16  waste=50.0%  (4/8 avoidable)
  2026-04-18  waste=20.0%  (1/5 avoidable)
```

## Built-in pricing (40+ models)

Pricing is built in for OpenAI, Anthropic, Google, Meta, Mistral, and DeepSeek models. Prices are USD per 1M tokens and auto-matched by model name.

```python
from llm_cost_tracker import lookup_pricing

inp, out, source = lookup_pricing("gpt-4o-mini")
print(f"Input: ${inp}/1M tokens, Output: ${out}/1M tokens")
# Input: $0.15/1M tokens, Output: $0.6/1M tokens
```

Custom pricing:
```python
from llm_cost_tracker.pricing import DEFAULT_PRICING
DEFAULT_PRICING["my-custom-model"] = (1.00, 3.00)  # input, output per 1M tokens
```

## Integration examples

### With OpenAI

```python
import openai
from llm_cost_tracker import CostTracker

client = openai.OpenAI()
tracker = CostTracker("./costs.db")

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)

tracker.record(
    prompt_tokens=response.usage.prompt_tokens,
    completion_tokens=response.usage.completion_tokens,
    model="gpt-4o-mini",
    provider="openai",
)
```

### With Anthropic

```python
import anthropic
from llm_cost_tracker import CostTracker

client = anthropic.Anthropic()
tracker = CostTracker("./costs.db")

response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)

tracker.record(
    prompt_tokens=response.usage.input_tokens,
    completion_tokens=response.usage.output_tokens,
    model="claude-sonnet-4-20250514",
    provider="anthropic",
)
```

### With LiteLLM

```python
import litellm
from llm_cost_tracker import CostTracker

tracker = CostTracker("./costs.db")

response = litellm.completion(model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}])

tracker.record(
    prompt_tokens=response.usage.prompt_tokens,
    completion_tokens=response.usage.completion_tokens,
    model="gpt-4o-mini",
    provider="openai",
)
```

## How it works

- **SQLite database** — all data stored locally in a single file. No external services.
- **Zero dependencies** — pure Python stdlib. No numpy, no pandas, no requests.
- **WAL mode** — concurrent reads while writing. Safe for multi-threaded apps.
- **Built-in pricing** — 40+ models with auto-matching. Falls back gracefully for unknown models.
- **Counterfactual tracking** — when you handle a request locally, it estimates what the LLM call would have cost, so you can see real savings.

## License

MIT
