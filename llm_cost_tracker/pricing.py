from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any

_log = logging.getLogger(__name__)
_warned_fallback_models: set[str] = set()

# Pricing: USD per 1M tokens (input, output)
# Update these as providers change pricing.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    # Anthropic
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-haiku-3-5": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
    # Google
    "gemini-1.5-pro": (3.50, 10.50),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-2.0-flash": (0.10, 0.40),
    # Meta / open models (typical API pricing)
    "llama-3.1-70b": (0.59, 0.79),
    "llama-3.1-8b": (0.10, 0.10),
    # Mistral
    "mistral-large": (2.00, 6.00),
    "mistral-small": (0.20, 0.60),
    # DeepSeek
    "deepseek-v3": (0.27, 1.10),
    "deepseek-r1": (0.55, 2.19),
}

# Fallback when model not found
FALLBACK_PRICING = (5.00, 15.00)

DEFAULT_CURRENCY = "USD"


def approx_tokens(text: str) -> int:
    """Fast token estimate: ~4 chars per token."""
    return math.ceil(len(text or "") / 4)


def lookup_pricing(model: str, provider: str | None = None) -> tuple[float, float, str]:
    """Look up pricing for a model. Returns (input_per_1m, output_per_1m, source).

    Matching rules (in order):
      1. Exact match on model name (case-insensitive).
      2. Prefix match where a known key is a prefix of the model name
         (e.g. "gpt-4o-mini-2024-07-18" starts with "gpt-4o-mini"). Longest
         prefix wins so "gpt-4o-mini" beats "gpt-4o".
      3. Fallback pricing.

    Notes:
      * Empty/None/whitespace-only model names return fallback pricing
        (previously returned whichever key was longest, silently billing
        as Sonnet).
      * We only match keys that are PREFIXES of the input, never the
        reverse. "gpt-4.1" previously matched "gpt-4" because "gpt-4"
        is a substring of "gpt-4.1"; that's still fine under prefix
        matching. But a bare "opus" input no longer matches
        "claude-opus-4-20250514" by reverse-substring.
      * Minimum key length of 3 for prefix matches prevents "o" matching
        "o1" etc.
    """
    model_lower = (model or "").strip().lower()
    provider = (provider or "").strip().lower()

    if not model_lower:
        return FALLBACK_PRICING[0], FALLBACK_PRICING[1], f"fallback:{provider}/"

    # Direct match (exact, case-insensitive).
    for key, (inp, out) in DEFAULT_PRICING.items():
        if key.lower() == model_lower:
            return inp, out, f"builtin:{key}"

    # Prefix match — prefer longest key that is a prefix of model_lower
    # (e.g. "gpt-4o-mini" beats "gpt-4o" for "gpt-4o-mini-2024-07-18").
    # Require at least 3 chars to avoid nonsense matches on single letters.
    best_match: tuple[float, float, str] | None = None
    best_len = 0
    for key, (inp, out) in DEFAULT_PRICING.items():
        key_lower = key.lower()
        if len(key_lower) < 3:
            continue
        if model_lower.startswith(key_lower) and len(key_lower) > best_len:
            best_match = (inp, out, key)
            best_len = len(key_lower)
    if best_match:
        return best_match[0], best_match[1], f"builtin_partial:{best_match[2]}"

    # Warn once per unknown model so users notice that costs are being
    # computed against the fallback rate rather than real pricing.
    if model_lower not in _warned_fallback_models:
        _warned_fallback_models.add(model_lower)
        _log.warning(
            "llm_cost_tracker: no pricing entry for model %r (provider=%r); "
            "falling back to $%.2f/$%.2f per 1M tokens. Add an entry to "
            "DEFAULT_PRICING to get accurate costs.",
            model, provider, FALLBACK_PRICING[0], FALLBACK_PRICING[1],
        )
    return FALLBACK_PRICING[0], FALLBACK_PRICING[1], f"fallback:{provider}/{model}"


def ensure_pricing_snapshot(conn, provider: str, model: str) -> str:
    """Insert pricing snapshot if not exists, return snapshot_id."""
    inp, out, source = lookup_pricing(model, provider)
    now = time.time()
    key = f"{provider}|{model}|{inp}|{out}|{DEFAULT_CURRENCY}"
    snapshot_id = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]
    conn.execute(
        """
        INSERT INTO pricing_snapshots(
          pricing_snapshot_id, provider, model_name,
          input_price_per_1m_tokens, output_price_per_1m_tokens,
          cached_input_price_per_1m_tokens, currency,
          effective_from, effective_to, source, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(pricing_snapshot_id) DO NOTHING
        """,
        (snapshot_id, provider, model, inp, out, inp, DEFAULT_CURRENCY, now, None, source, now),
    )
    return snapshot_id


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default
