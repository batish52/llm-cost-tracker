from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .db import connect
from .pricing import (
    approx_tokens,
    ensure_pricing_snapshot,
    lookup_pricing,
    _to_int,
    _to_float,
)


class CostTracker:
    """Track LLM API costs per request. Zero dependencies beyond stdlib.

    Usage:
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
        print(report["total_cost_usd"])
        print(report["total_requests"])

        # Periodic snapshot for dashboards:
        tracker.capture_snapshot(window_hours=24)
    """

    def __init__(self, db_path: str | Path = "./llm_costs.db"):
        self.db_path = Path(db_path)
        # Touch the DB to ensure schema is created
        conn = connect(self.db_path)
        conn.close()

    def _connect(self):
        return connect(self.db_path)

    def record(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        route: str = "external",
        intent: str | None = None,
        session_key: str | None = None,
        request_id: str | None = None,
        prompt_text: str | None = None,
        counterfactual_model: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Record a single LLM API call.

        Args:
            prompt_tokens: Number of input tokens used.
            completion_tokens: Number of output tokens generated.
            model: Model name (e.g. "gpt-4o-mini", "claude-3-5-sonnet").
            provider: Provider name (e.g. "openai", "anthropic", "ollama").
            route: "external" (sent to LLM) or "local" (handled locally, no LLM call).
            intent: Optional classification of what this request was for.
            session_key: Optional grouping key (user ID, session ID, etc.).
            request_id: Optional unique ID. Auto-generated if not provided.
            prompt_text: Optional prompt text for counterfactual token estimation.
            counterfactual_model: Model to compare savings against. Defaults to same model.
            metadata: Optional dict of extra data to store as JSON.

        Returns:
            Dict with request_id, cost_usd, saved amounts.
        """
        conn = self._connect()
        try:
            now = time.time()
            request_id = request_id or str(uuid.uuid4())
            counterfactual_model = counterfactual_model or model
            route_mode = "local_only" if route in ("local", "local_only") else "external"

            # Get pricing
            pricing_snapshot_id = ensure_pricing_snapshot(conn, provider, model)
            row = conn.execute(
                "SELECT input_price_per_1m_tokens, output_price_per_1m_tokens "
                "FROM pricing_snapshots WHERE pricing_snapshot_id=?",
                (pricing_snapshot_id,),
            ).fetchone()
            in_price = _to_float(row[0] if row else 5.0)
            out_price = _to_float(row[1] if row else 15.0)

            total_tokens = prompt_tokens + completion_tokens

            # Actual cost
            cost_usd = 0.0
            if route_mode == "external":
                cost_usd = round(
                    (prompt_tokens / 1_000_000.0) * in_price
                    + (completion_tokens / 1_000_000.0) * out_price,
                    8,
                )

            # Counterfactual: what would this have cost if sent externally?
            cf_prompt = prompt_tokens
            cf_completion = completion_tokens
            estimation_method = "actual_tokens"

            if route_mode == "local_only" and prompt_text:
                cf_prompt = approx_tokens(prompt_text)
                estimation_method = "approx_from_prompt_text"
            elif route_mode == "local_only" and cf_prompt == 0:
                cf_prompt = prompt_tokens if prompt_tokens > 0 else 100
                estimation_method = "fallback_estimate"

            cf_total = cf_prompt + cf_completion

            # Savings (only meaningful for local routes)
            saved_prompt_only = 0.0
            saved_full = 0.0
            if route_mode == "local_only":
                cf_in_price, cf_out_price, _ = lookup_pricing(counterfactual_model, provider)
                saved_prompt_only = round((cf_prompt / 1_000_000.0) * cf_in_price, 8)
                saved_full = round(
                    (cf_prompt / 1_000_000.0) * cf_in_price
                    + (cf_completion / 1_000_000.0) * cf_out_price,
                    8,
                )

            conn.execute(
                """
                INSERT INTO request_usage_ledger(
                  request_id, timestamp, session_key, route_mode, intercept_class,
                  provider, model_name, counterfactual_model, pricing_snapshot_id,
                  prompt_tokens, completion_tokens, total_tokens, cost_usd,
                  counterfactual_prompt_tokens, counterfactual_completion_tokens,
                  counterfactual_total_tokens, saved_prompt_only_usd,
                  saved_full_modeled_usd, estimation_method, metadata_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id, now, session_key, route_mode, intent,
                    provider, model, counterfactual_model, pricing_snapshot_id,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd,
                    cf_prompt, cf_completion, cf_total,
                    saved_prompt_only, saved_full, estimation_method,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    now,
                ),
            )
            conn.commit()

            return {
                "request_id": request_id,
                "cost_usd": cost_usd,
                "saved_prompt_only_usd": saved_prompt_only,
                "saved_full_modeled_usd": saved_full,
                "route": route_mode,
                "model": model,
                "tokens": total_tokens,
            }
        finally:
            conn.close()

    def report(
        self,
        *,
        window: str | None = None,
        session_key: str | None = None,
        group_by: str | None = None,
        limit: int = 100,
    ) -> dict:
        """Generate a cost report.

        Args:
            window: Time window like "1d", "7d", "30d", "1h".
            session_key: Filter to a specific session.
            group_by: Group results by "model", "provider", "route", "intent", or "session".
            limit: Max rows to scan.

        Returns:
            Dict with totals, breakdowns, and recent requests.
        """
        conn = self._connect()
        try:
            now = time.time()
            window_seconds = self._parse_window(window)
            cutoff = now - window_seconds if window_seconds else None

            conditions = []
            params: list = []
            if cutoff:
                conditions.append("created_at >= ?")
                params.append(cutoff)
            if session_key:
                conditions.append("session_key = ?")
                params.append(session_key)

            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            rows = conn.execute(
                f"SELECT * FROM request_usage_ledger{where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, limit)),
            ).fetchall()

            total_cost = 0.0
            total_tokens = 0
            total_prompt = 0
            total_completion = 0
            total_saved_prompt = 0.0
            total_saved_full = 0.0
            local_count = 0
            external_count = 0
            by_model: dict[str, dict] = {}
            by_provider: dict[str, dict] = {}
            by_route: dict[str, dict] = {}
            by_intent: dict[str, dict] = {}
            by_session: dict[str, dict] = {}

            for r in rows:
                cost = _to_float(r["cost_usd"])
                tokens = _to_int(r["total_tokens"])
                prompt = _to_int(r["prompt_tokens"])
                completion = _to_int(r["completion_tokens"])
                saved_p = _to_float(r["saved_prompt_only_usd"])
                saved_f = _to_float(r["saved_full_modeled_usd"])
                route = r["route_mode"] or "unknown"
                model = r["model_name"] or "unknown"
                provider = r["provider"] or "unknown"
                intent = r["intercept_class"] or "unknown"
                session = r["session_key"] or "default"

                total_cost += cost
                total_tokens += tokens
                total_prompt += prompt
                total_completion += completion
                total_saved_prompt += saved_p
                total_saved_full += saved_f

                if "local" in route:
                    local_count += 1
                else:
                    external_count += 1

                for key, bucket in [
                    (model, by_model), (provider, by_provider),
                    (route, by_route), (intent, by_intent),
                    (session, by_session),
                ]:
                    if key not in bucket:
                        bucket[key] = {"requests": 0, "tokens": 0, "cost_usd": 0.0, "saved_usd": 0.0}
                    bucket[key]["requests"] += 1
                    bucket[key]["tokens"] += tokens
                    bucket[key]["cost_usd"] = round(bucket[key]["cost_usd"] + cost, 8)
                    bucket[key]["saved_usd"] = round(bucket[key]["saved_usd"] + saved_f, 8)

            # Waste analysis: identify requests that likely didn't need an LLM
            avoidable_intents = {"code_lookup", "config_lookup", "symbol_lookup",
                                 "file_search", "diagnostics", "status_check",
                                 "runtime_diagnostics", "config_manifest_lookup",
                                 "repo_navigation", "exact_body"}
            avoidable_count = 0
            avoidable_cost = 0.0
            for r in rows:
                intent_val = (r["intercept_class"] or "").lower()
                route_val = r["route_mode"] or ""
                if "external" in route_val and intent_val in avoidable_intents:
                    avoidable_count += 1
                    avoidable_cost += _to_float(r["cost_usd"])

            # Estimate potential savings if all external requests used a cheaper model
            cheapest_input = 0.15  # gpt-4o-mini pricing
            cheapest_output = 0.60
            potential_savings = 0.0
            for r in rows:
                if "external" in (r["route_mode"] or ""):
                    actual_cost = _to_float(r["cost_usd"])
                    cheap_cost = round(
                        (_to_int(r["prompt_tokens"]) / 1_000_000.0) * cheapest_input
                        + (_to_int(r["completion_tokens"]) / 1_000_000.0) * cheapest_output,
                        8,
                    )
                    if actual_cost > cheap_cost:
                        potential_savings += actual_cost - cheap_cost

            result = {
                "total_requests": len(rows),
                "total_cost_usd": round(total_cost, 6),
                "total_tokens": total_tokens,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_saved_prompt_only_usd": round(total_saved_prompt, 6),
                "total_saved_full_modeled_usd": round(total_saved_full, 6),
                "local_count": local_count,
                "external_count": external_count,
                "requests_by_route": {k: v["requests"] for k, v in by_route.items()},
                "cost_by_model": {k: round(v["cost_usd"], 6) for k, v in by_model.items()},
                "cost_by_provider": {k: round(v["cost_usd"], 6) for k, v in by_provider.items()},
                "tokens_by_model": {k: v["tokens"] for k, v in by_model.items()},
                "savings_by_intent": {k: round(v["saved_usd"], 6) for k, v in by_intent.items()},
                # Waste analysis — the conversion hook
                "avoidable_external_requests": avoidable_count,
                "avoidable_cost_usd": round(avoidable_cost, 6),
                "avoidable_percent": round(
                    (avoidable_count / max(1, external_count)) * 100, 1
                ) if external_count > 0 else 0.0,
                "potential_model_downgrade_savings_usd": round(potential_savings, 6),
                "optimization_summary": (
                    f"{avoidable_count} of {external_count} external requests "
                    f"(${round(avoidable_cost, 2)}) could have been handled locally. "
                    f"An additional ${round(potential_savings, 2)} could be saved by "
                    f"downgrading to cheaper models where possible."
                ) if external_count > 0 else "No external requests in this window.",
                "window": window,
                "window_seconds": window_seconds,
            }

            if group_by == "model":
                result["breakdown"] = by_model
            elif group_by == "provider":
                result["breakdown"] = by_provider
            elif group_by == "route":
                result["breakdown"] = by_route
            elif group_by == "intent":
                result["breakdown"] = by_intent
            elif group_by == "session":
                result["breakdown"] = by_session

            return result
        finally:
            conn.close()

    def recent(self, limit: int = 20) -> list[dict]:
        """Get recent tracked requests."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM request_usage_ledger ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [
                {
                    "request_id": r["request_id"],
                    "route": r["route_mode"],
                    "model": r["model_name"],
                    "provider": r["provider"],
                    "intent": r["intercept_class"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "cost_usd": r["cost_usd"],
                    "saved_usd": r["saved_full_modeled_usd"],
                    "session": r["session_key"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def capture_snapshot(
        self,
        *,
        window_hours: float = 24.0,
        job_name: str = "cost-tracker",
        snapshot_id: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Capture a periodic cost snapshot (for dashboards/cron jobs).

        Args:
            window_hours: How many hours back to aggregate.
            job_name: Name for this snapshot job.
            snapshot_id: Optional ID. Auto-generated if not provided.
            notes: Optional notes.

        Returns:
            Dict with aggregated savings and costs for the window.
        """
        conn = self._connect()
        try:
            now = time.time()
            started_at = now - (window_hours * 3600.0)
            snapshot_id = snapshot_id or f"{job_name}:{int(now)}:{uuid.uuid4().hex[:8]}"

            rows = conn.execute(
                "SELECT * FROM request_usage_ledger WHERE created_at >= ? AND created_at <= ? ORDER BY created_at",
                (started_at, now),
            ).fetchall()

            local_count = sum(1 for r in rows if (r["route_mode"] or "") == "local_only")
            external_count = sum(1 for r in rows if "external" in (r["route_mode"] or ""))
            ext_prompt = sum(_to_int(r["prompt_tokens"]) for r in rows if "external" in (r["route_mode"] or ""))
            ext_completion = sum(_to_int(r["completion_tokens"]) for r in rows if "external" in (r["route_mode"] or ""))
            ext_cost = round(sum(_to_float(r["cost_usd"]) for r in rows if "external" in (r["route_mode"] or "")), 8)
            saved_prompt = round(sum(_to_float(r["saved_prompt_only_usd"]) for r in rows if (r["route_mode"] or "") == "local_only"), 8)
            saved_full = round(sum(_to_float(r["saved_full_modeled_usd"]) for r in rows if (r["route_mode"] or "") == "local_only"), 8)
            net_conservative = round(saved_prompt - ext_cost, 8)
            net_modeled = round(saved_full - ext_cost, 8)
            snapshot_ids = sorted({r["pricing_snapshot_id"] for r in rows if r["pricing_snapshot_id"]})

            conn.execute(
                """
                INSERT INTO cost_snapshots(
                  snapshot_id, job_name, started_at, finished_at, status,
                  local_count, external_count,
                  external_prompt_tokens, external_completion_tokens, external_cost_usd,
                  saved_prompt_only_usd, saved_full_modeled_usd,
                  net_savings_conservative_usd, net_savings_modeled_usd,
                  pricing_snapshot_ids, notes, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id, job_name, started_at, now, "ok",
                    local_count, external_count,
                    ext_prompt, ext_completion, ext_cost,
                    saved_prompt, saved_full,
                    net_conservative, net_modeled,
                    json.dumps(snapshot_ids), notes, now,
                ),
            )
            conn.commit()

            return {
                "snapshot_id": snapshot_id,
                "window_hours": window_hours,
                "total_requests": len(rows),
                "local_count": local_count,
                "external_count": external_count,
                "external_cost_usd": ext_cost,
                "saved_prompt_only_usd": saved_prompt,
                "saved_full_modeled_usd": saved_full,
                "net_savings_conservative_usd": net_conservative,
                "net_savings_modeled_usd": net_modeled,
            }
        finally:
            conn.close()

    def snapshots(self, limit: int = 10) -> list[dict]:
        """Get recent cost snapshots."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM cost_snapshots ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
            return [
                {
                    "snapshot_id": r["snapshot_id"],
                    "job_name": r["job_name"],
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "local_count": r["local_count"],
                    "external_count": r["external_count"],
                    "external_cost_usd": r["external_cost_usd"],
                    "saved_full_modeled_usd": r["saved_full_modeled_usd"],
                    "net_savings_conservative_usd": r["net_savings_conservative_usd"],
                    "net_savings_modeled_usd": r["net_savings_modeled_usd"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    @staticmethod
    def _parse_window(window: str | None) -> int | None:
        if not window:
            return None
        import re
        text = str(window).strip().lower()
        m = re.match(r"^(\d+)([smhdw])$", text)
        if not m:
            return None
        n = int(m.group(1))
        unit = m.group(2)
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return n * mult
