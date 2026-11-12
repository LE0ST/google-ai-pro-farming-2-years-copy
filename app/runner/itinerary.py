"""Bounded read-only continuation. Checkpoints never authorize a mutation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..sites.google_one import discover

MAX_QUERIES = 3
MAX_SECONDS = 15


def route_key(url: str) -> str:
    """Ignore UI query flags when deduplicating a read-only view."""
    parsed = urlsplit(url)
    path = re.sub(r"^/u/0/", "/", parsed.path.rstrip('/')) if parsed.hostname == "one.google.com" else parsed.path.rstrip('/')
    return hashlib.sha256(f"{parsed.hostname}{path}".encode()).hexdigest()


def checkpoint_path(directory: Path, account_id: str) -> Path:
    if not re.fullmatch(r"acc_[a-zA-Z0-9_]+", account_id):
        raise ValueError("invalid account identifier")
    return directory / f"itinerary_{account_id}.json"


def public_status(directory: Path, account_id: str) -> dict:
    """Expose fixed planner fields, never hashes, URLs or account content."""
    try:
        state = json.loads(checkpoint_path(directory, account_id).read_text(encoding="utf-8"))
        phase = state.get("phase")
        next_action = state.get("next_action")
        if phase not in {"observe", "collect_links", "query_details", "reconcile", "blocked", "reconciled"} or next_action not in {
            "read_official_plan_details", "verify_primary_renewal", "inspect_plan_change", "existing_guarded_flow"}:
            return {}
        return {"phase": phase, "next_action": next_action, "completed_queries": min(MAX_QUERIES, len(state.get("completed", []))),
                "plan_change_pending": state.get("plan_change_pending") is True,
                "primary_renewal_unknown": state.get("primary_renewal_unknown") is True,
                "subscription_status": state.get("subscription_status") if state.get("subscription_status") in {"active_unknown_date", "active_known_duration", "active_known_date", "no_subscription", "detection_failed", "inconsistent"} else "unknown",
                "bundle_next_payment_date": state.get("bundle_next_payment_date") if isinstance(state.get("bundle_next_payment_date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", state["bundle_next_payment_date"]) else None,
                "automatic_promo_months": state.get("automatic_promo_months") if type(state.get("automatic_promo_months")) is int else None,
                "plan_changes": [{"status": v.get("status") if v.get("status") in {"pending", "completed", "unknown"} else "unknown",
                    "scope": v.get("scope") if v.get("scope") in {"addon", "primary"} else "unknown",
                    "change_type": "addon_removal" if v.get("change_type") == "addon_removal" else "unspecified",
                    "effective_date": v.get("effective_date") if isinstance(v.get("effective_date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["effective_date"]) else None,
                    "resulting_plan": "Google AI Pro" if v.get("resulting_plan") == "Google AI Pro" else None}
                    for v in state.get("plan_changes", []) if isinstance(v, dict)],
                "blockers": ([{"check": "primary_renewal", "reason": "Individual renewal date is not demonstrated; bundle billing is separate"}] if state.get("primary_renewal_unknown") else [])
                            + ([{"check": "plan_change_confirmation", "reason": "Google reports a pending or ambiguous plan change; this is not a GaiConsole submission"}] if state.get("plan_change_pending") else []),
                "checks_performed": [v for v in state.get("checks_performed", []) if v in {"google_play_read", "google_one_read", "detail_links_examined", "reconciliation"}],
                "link_search_result": state.get("link_search_result") if state.get("link_search_result") in {"not_examined", "no_verified_links", "all_links_already_seen", "unvisited_links", "budget_exhausted"} else "not_examined",
                "pending_checks": (["primary_renewal"] if state.get("primary_renewal_unknown") else []) + (["plan_change_confirmation"] if state.get("plan_change_pending") else []),
                "blocked_operations": ["subscription", "cancellation", "resubmission"] if phase == "blocked" else [],
                "blocked_reason": "Plan change or individual renewal requires confirmation" if phase == "blocked" else ""}
    except (OSError, ValueError, TypeError):
        return {}


async def continue_reads(page, observation, local_step: int, path: Path):
    """Resume safe queries only; fresh observation is required on every invocation."""
    signature = hashlib.sha256(json.dumps({"version": discover.LOADED_SOURCE_SHA256,
        "status": observation.status.value, "details": observation.details.to_dict(),
        "step": local_step}, sort_keys=True).encode()).hexdigest()
    state = {"fingerprint": signature, "expires": time.time() + 300, "completed": [], "phase": "observe",
             "checks_performed": [], "link_search_result": "not_examined"}
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("fingerprint") == signature and saved.get("expires", 0) > time.time():
            state["completed"] = [k for k in saved.get("completed", []) if isinstance(k, str) and re.fullmatch(r"[0-9a-f]{64}", k)][:MAX_QUERIES]
    except (OSError, ValueError, TypeError):
        pass
    seen = set(state["completed"])
    seen.add(route_key(getattr(page, "url", "")))
    for event in observation.read_events:
        if event.get("stage") == "navigation-result" and event.get("final_host"):
            seen.add(route_key(f"https://{event['final_host']}{event.get('final_path', '')}"))
            label = "google_play_read" if event["final_host"] == "play.google.com" else "google_one_read" if event["final_host"] == "one.google.com" else None
            if label and label not in state["checks_performed"]:
                state["checks_performed"].append(label)

    def save(phase, next_action, reason):
        state.update(phase=phase, next_action=next_action, reason=reason, local_step=local_step,
                     subscription_status=observation.status.value, plan_change_pending=observation.details.plan_change_pending,
                     primary_renewal_unknown=observation.renewal_date is None,
                     bundle_next_payment_date=observation.bundle_next_payment_date.isoformat() if observation.bundle_next_payment_date else None,
                     automatic_promo_months=observation.ai_pro_promo_months,
                     plan_changes=list(observation.details.plan_change_observations))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    async def collect():
        nonlocal observation
        for _ in range(MAX_QUERIES):
            if observation.status in {discover.SubscriptionStatus.INCONSISTENT, discover.SubscriptionStatus.DETECTION_FAILED}:
                break
            if discover.reconcile_progress(observation, local_step).detected_layer is not None:
                break
            save("collect_links", "read_official_plan_details", "individual renewal date not established")
            urls = await discover.discover_plan_detail_urls(page)
            if "detail_links_examined" not in state["checks_performed"]:
                state["checks_performed"].append("detail_links_examined")
            candidates = [url for url in urls if route_key(url) not in seen]
            state["link_search_result"] = "no_verified_links" if not urls else "all_links_already_seen" if not candidates else "unvisited_links"
            if not candidates or len(state["completed"]) >= MAX_QUERIES:
                if candidates:
                    state["link_search_result"] = "budget_exhausted"
                break
            url = candidates[0]
            seen.add(route_key(url))
            save("query_details", "read_official_plan_details", "query in progress")
            observation = await discover.read_official_plan_details(page, observation, detail_urls=[url], timeout_ms=3000)
            state["completed"].append(route_key(url))
            save("reconcile", "verify_primary_renewal", "read-only query completed")

    try:
        await asyncio.wait_for(collect(), MAX_SECONDS)
    except asyncio.TimeoutError:
        save("blocked", "verify_primary_renewal", "read-only query time budget exhausted")
        return observation, discover.reconcile_progress(observation, local_step)
    reconciliation = discover.reconcile_progress(observation, local_step)
    state["checks_performed"].append("reconciliation")
    if reconciliation.detected_layer is None:
        reason = ("conflicting or insufficient subscription evidence" if observation.status in {
            discover.SubscriptionStatus.INCONSISTENT, discover.SubscriptionStatus.DETECTION_FAILED}
            else "No unvisited verified plan-detail link supplies an individual renewal date; bundle billing date is insufficient")
        save("blocked", "inspect_plan_change" if observation.details.plan_change_pending else "verify_primary_renewal", reason)
    else:
        save("reconciled", "existing_guarded_flow", "fresh evidence reconciled; existing guards still apply")
    print(f"[ITINERARY] phase={state['phase']}; next={state['next_action']}; reason={state['reason']}; cursor={local_step}", flush=True)
    return observation, reconciliation
