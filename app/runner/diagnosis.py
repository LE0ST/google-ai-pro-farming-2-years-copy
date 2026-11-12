"""Read-only subscription diagnostics, isolated from the subscription runner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import async_playwright

from .. import db
from ..config import settings
from ..sites.google_one import discover
from . import browser, queue

_ID = re.compile(r"^acc_[0-9a-f]{12}$")
_LOADED_OBSERVER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def detector_version() -> dict[str, str]:
    path = Path(discover.__file__).resolve()
    observer_path = Path(__file__).resolve()
    return {
        "sha256": discover.LOADED_SOURCE_SHA256,
        "source_path": str(path),
        "observer_sha256": _LOADED_OBSERVER_SHA256,
        "observer_path": str(observer_path),
    }


async def empty_state_conditions(page, page_errors: int = 0) -> dict[str, object]:
    """Return fixed booleans and tag names, without returning page text or attributes."""
    script = r"""() => {
      const vis=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
      const all=s=>[...document.querySelectorAll(s)].filter(vis);
      const selected=all('[role="tab"][aria-selected="true"]');
      const headings=all('h1,h2,h3,[role="heading"],div,span,p');
      const title=headings.filter(e=>/^(buscar suscripciones|discover subscriptions)$/i.test((e.innerText||'').trim()));
      const description=/descubre miles de servicios disponibles en google play y adminístralos fácilmente desde aquí|discover thousands of services (?:available )?(?:on|in) google play.{0,80}manage/i;
      let container=null, illustration=false, desc=false;
      for(const h of title){let e=h.parentElement; for(let i=0;e&&e!==document.body&&i<3;i++,e=e.parentElement){if(description.test((e.innerText||'').replace(/\s+/g,' '))){desc=true;container=e;illustration=all('img,svg,[role="img"]').some(x=>e.contains(x));break;}}}
      const busy=all('[aria-busy="true"],[role="progressbar"],progress').length>0;
      const rows=all('[data-subscription-id],[data-subscription-card],[data-subscription-row],main article,main [role="listitem"],main li').length;
      const controls=all('button,a,[role="button"]').filter(e=>/^(manage subscription|cancel subscription|administrar suscripción|cancelar suscripción)$/i.test((e.innerText||e.getAttribute('aria-label')||'').trim())).length;
      const alerts=all('[role="alert"],[aria-invalid="true"]').length;
      const elements=all('body *');
      const titleRe=/^(buscar suscripciones|discover subscriptions)$/i;
      const descriptions=/descubre miles de servicios disponibles en google play|discover thousands of services (?:available )?(?:on|in) google play/i;
      const norm=s=>(s||'').replace(/\s+/g,' ').trim();
      const match=(e,kind,regex)=>regex.test(norm(kind==='innerText'?e.innerText:kind==='textContent'?e.textContent:e.getAttribute('aria-label')));
      const tags=nodes=>[...new Set(nodes.map(e=>e.tagName).filter(x=>['DIV','SPAN','P','H1','H2','H3','SECTION','MAIN','BUTTON','A','IMG','SVG'].includes(x)))].slice(0,12);
      const titleInner=elements.filter(e=>match(e,'innerText',titleRe));
      const titleText=elements.filter(e=>match(e,'textContent',titleRe));
      const titleAria=elements.filter(e=>match(e,'aria',titleRe));
      const descInner=elements.filter(e=>match(e,'innerText',descriptions));
      const descText=elements.filter(e=>match(e,'textContent',descriptions));
      const descAria=elements.filter(e=>match(e,'aria',descriptions));
      const cssIllustrations=elements.filter(e=>{const b=getComputedStyle(e).backgroundImage; return b && b!=='none';});
      const leafDesc=descInner.filter(e=>!descInner.some(other=>other!==e && e.contains(other)));
      let shared=null, depth=null, imageAtShared=false;
      for(const t of titleInner){let e=t.parentElement; for(let i=1;e && e!==document.body && i<=12;i++,e=e.parentElement){
        if(leafDesc.some(d=>e.contains(d))){shared=e;depth=i;imageAtShared=[...e.querySelectorAll('svg,img,[role="img"]')].some(vis);break;}}
        if(shared)break;}
      const allowedTag=e=>e&&['DIV','SPAN','P','SECTION','MAIN','ARTICLE','H1','H2','H3'].includes(e.tagName)?e.tagName:null;
      return {page_subscription:/\/store\/account\/subscriptions\/?$/.test(location.pathname),
        selected_tab:selected.some(e=>/^(suscripciones|subscriptions)$/i.test((e.innerText||e.getAttribute('aria-label')||'').trim())),
        empty_title:title.length>0,context_description:desc,illustration:illustration,empty_container:!!container,
        no_subscription_rows:rows===0,no_manage_controls:controls===0,no_loading:!busy,no_visible_errors:alerts===0,
        ready:document.readyState==='complete',title_tags:title.map(e=>e.tagName).filter(x=>['H1','H2','H3','DIV','SPAN'].includes(x)).slice(0,5),
        container_tag:container&&['DIV','SECTION','MAIN','ARTICLE'].includes(container.tagName)?container.tagName:null,
        structural:{title_inner_count:Math.min(1000,titleInner.length),title_text_count:Math.min(1000,titleText.length),
          title_aria_count:Math.min(1000,titleAria.length),description_inner_count:Math.min(1000,descInner.length),
          description_text_count:Math.min(1000,descText.length),description_aria_count:Math.min(1000,descAria.length),
          title_match_tags:tags([...titleInner,...titleText,...titleAria]),description_match_tags:tags([...descInner,...descText,...descAria]),
          visible_svg_count:Math.min(1000,all('svg').length),visible_img_count:Math.min(1000,all('img,[role="img"]').length),
          css_background_count:Math.min(1000,cssIllustrations.length),shared_depth:depth,shared_tag:allowedTag(shared),
          shared_has_illustration:imageAtShared,description_leaf_tags:tags(leafDesc)}};
    }"""
    keys = ("page_subscription", "selected_tab", "empty_title", "context_description", "illustration",
            "empty_container", "no_subscription_rows", "no_manage_controls", "no_loading", "no_visible_errors", "ready")
    try:
        raw = await asyncio.wait_for(page.evaluate(script), timeout=3)
        if not isinstance(raw, dict):
            raise ValueError("invalid shape")
        result = {key: raw.get(key) if type(raw.get(key)) is bool else None for key in keys}
        result["no_page_errors"] = page_errors == 0
        # Illustration is reported, but the contextual detector does not require it.
        required = tuple(key for key in keys if key != "illustration")
        result["blocking_conditions"] = [key for key in (*required, "no_page_errors") if result[key] is not True]
        result["title_tags"] = [x for x in raw.get("title_tags", []) if x in {"H1", "H2", "H3", "DIV", "SPAN"}][:5] if isinstance(raw.get("title_tags"), list) else []
        result["container_tag"] = raw.get("container_tag") if raw.get("container_tag") in {"DIV", "SECTION", "MAIN", "ARTICLE"} else None
        structure = raw.get("structural") if isinstance(raw.get("structural"), dict) else {}
        count_keys = ("title_inner_count", "title_text_count", "title_aria_count", "description_inner_count", "description_text_count", "description_aria_count", "visible_svg_count", "visible_img_count", "css_background_count")
        tag_allow = {"DIV", "SPAN", "P", "H1", "H2", "H3", "SECTION", "MAIN", "BUTTON", "A", "IMG", "SVG"}
        result["structural"] = {key: structure[key] if type(structure.get(key)) is int and 0 <= structure[key] <= 1000 else None for key in count_keys}
        for key in ("title_match_tags", "description_match_tags"):
            result["structural"][key] = [tag for tag in structure.get(key, []) if tag in tag_allow][:12] if isinstance(structure.get(key), list) else []
        result["structural"]["description_leaf_tags"] = [tag for tag in structure.get("description_leaf_tags", []) if tag in tag_allow][:12] if isinstance(structure.get("description_leaf_tags"), list) else []
        result["structural"]["shared_depth"] = structure.get("shared_depth") if type(structure.get("shared_depth")) is int and 1 <= structure["shared_depth"] <= 12 else None
        result["structural"]["shared_tag"] = structure.get("shared_tag") if structure.get("shared_tag") in tag_allow else None
        result["structural"]["shared_has_illustration"] = structure.get("shared_has_illustration") if type(structure.get("shared_has_illustration")) is bool else None
        return result
    except Exception:
        return {**{key: None for key in keys}, "no_page_errors": None, "blocking_conditions": list(keys) + ["no_page_errors"]}


def request_block_reason(url: str, method: str) -> str | None:
    """Return a fixed, non-sensitive reason; never retain the URL or its parameters."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"}:
        return "unsupported_scheme"
    if method.upper() not in {"GET", "HEAD"}:
        return "non_read_method"
    if not any(host == suffix or host.endswith("." + suffix) for suffix in
               ("google.com", "gstatic.com", "googleusercontent.com", "googleapis.com")):
        return "untrusted_host"
    if any(host == prefix + ".google.com" or host.endswith("." + prefix + ".google.com")
           for prefix in ("payments", "pay", "wallet")):
        return "payment_host"
    target = path + "?" + parsed.query.lower()
    if any(part in target for part in
           ("checkout", "purchase", "payment", "billing", "resubscribe", "cancel", "/subscribe", "/buy")):
        return "sensitive_destination"
    return None


def request_allowed(url: str, method: str) -> bool:
    return request_block_reason(url, method) is None


def destination_category(url: str) -> str:
    """Summarize a small allowlist of destinations without returning paths or queries."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host == "play.google.com":
        if path == "/store/account/subscriptions":
            return "play_subscriptions"
        if "batchexecute" in path:
            return "play_batch_rpc"
        if path.startswith("/_/") and "/data/" in path:
            return "play_data_endpoint"
        if any(marker in path for marker in ("/log", "/gen_204", "/cspreport")):
            return "play_telemetry"
        if path.startswith("/_/"):
            return "play_app_endpoint"
        if path.startswith("/store/"):
            return "play_store_endpoint"
        return "play_other"
    if host == "accounts.google.com":
        return "google_signin"
    if any(host == p + ".google.com" or host.endswith("." + p + ".google.com")
           for p in ("payments", "pay", "wallet")):
        return "payment_host"
    if host == "gstatic.com" or host.endswith(".gstatic.com"):
        return "google_static"
    if host == "googleapis.com" or host.endswith(".googleapis.com"):
        return "google_apis"
    if host == "googleusercontent.com" or host.endswith(".googleusercontent.com"):
        return "google_user_content"
    if host == "google.com" or host.endswith(".google.com"):
        return "google_other"
    if host == "googletagmanager.com" or host.endswith(".googletagmanager.com"):
        return "tag_manager"
    if host == "google-analytics.com" or host.endswith(".google-analytics.com"):
        return "analytics"
    return "other"


def _method(method: str) -> str:
    value = method.upper()
    return value if value in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"


def _resource_type(request) -> str:
    value = getattr(request, "resource_type", "other")
    allowed = {"document", "stylesheet", "image", "media", "font", "script", "xhr", "fetch", "websocket"}
    return value if value in allowed else "other"


class NetworkEvidence:
    """Count sanitized events; request identity is kept only while a request is pending."""

    def __init__(self, *, passive: bool = False) -> None:
        self.counts: Counter[tuple] = Counter()
        self.pending: dict[int, str] = {}
        self.page_errors = 0
        self.page_error_types: Counter[str] = Counter()
        self.passive = passive

    def page_error(self, error) -> None:
        self.page_errors += 1
        match = re.match(r"^(TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError|Error)\b", str(error))
        kind = match.group(1) if match else "OtherError"
        self.page_error_types[kind] += 1

    def requested(self, request) -> None:
        self.pending[id(request)] = destination_category(request.url)
        self.record("initiated", request, "observed")

    def completed(self, request) -> None:
        self.pending.pop(id(request), None)

    def failed(self, request) -> None:
        self.completed(request)
        self.record("failed", request, "network_or_blocked")

    def response(self, response) -> None:
        status = response.status
        self.record("response", response.request, str(status) if type(status) is int and 100 <= status <= 599 else "other")

    def record(self, event: str, request, detail: str) -> None:
        if event == "blocked":
            self.pending.pop(id(request), None)
        self.counts[(event, destination_category(request.url), _method(request.method),
                     _resource_type(request), detail)] += 1

    def summary(self) -> dict[str, object]:
        return {"events": [{"event": key[0], "destination": key[1], "method": key[2],
                            "resource_type": key[3], "detail": key[4], "count": count}
                           for key, count in sorted(self.counts.items())],
                "pending_requests": len(self.pending), "page_errors": self.page_errors,
                "page_error_types": dict(self.page_error_types)}


_PLAY_DATA_DESTINATIONS = {"play_app_endpoint", "play_data_endpoint", "play_batch_rpc", "play_store_endpoint", "play_other"}
_NON_DATA_DESTINATIONS = {"play_telemetry", "analytics", "tag_manager"}
_SEMANTIC_SIGNALS = {"explicit-empty", "explicit-active", "contextual-empty", "busy"}


def _relevant_service_worker(worker) -> bool:
    """Only Play-origin (or unknown/mocked) service workers can intercept Play subscription reads."""
    url = getattr(worker, "url", None)
    if not isinstance(url, str) or not url.strip():
        return True
    host = (urlsplit(url).hostname or "").lower()
    return not host or host == "play.google.com" or host.endswith(".play.google.com")


def _belongs_to_page(page, request) -> bool:
    frame = getattr(request, "frame", None)
    req_page = getattr(frame, "page", None)
    return req_page is None or req_page is page


def contextual_network_reason(evidence: NetworkEvidence | None) -> str | None:
    """Return a stable failure code, or None only when observed traffic is sufficient."""
    if evidence is None:
        return "data_request_unverified"
    if any(event == "blocked" and dest in _PLAY_DATA_DESTINATIONS and
           (method not in {"GET", "HEAD"} or resource in {"fetch", "xhr", "document"})
           for event, dest, method, resource, _detail in evidence.counts):
        return "data_request_blocked"
    if any(dest not in _NON_DATA_DESTINATIONS for dest in evidence.pending.values()):
        return "data_request_pending"
    if evidence.page_errors:
        return "data_request_unverified"
    for event, dest, method, resource, detail in evidence.counts:
        if dest not in _PLAY_DATA_DESTINATIONS:
            continue
        if evidence.passive and event == "initiated" and method not in {"GET", "HEAD"}:
            return "data_request_unverified"
        if event == "failed" and resource in {"fetch", "xhr", "document", "script", "stylesheet"}:
            return "data_request_unverified"
        if event == "response" and detail.isdigit() and int(detail) >= 400 and resource in {"fetch", "xhr", "document", "script", "stylesheet"}:
            return "data_request_unverified"
    return None


def contextual_network_safe(evidence: NetworkEvidence) -> bool:
    return contextual_network_reason(evidence) is None


async def diagnose_run_page(page, local_step: int, **kwargs):
    """Observe Run's existing context without intercepting or changing any request."""
    context = getattr(page, "context", None)
    if context is None:
        return await discover.diagnose_page(page, local_step, **kwargs)
    evidence = NetworkEvidence(passive=True)

    def on_request(request) -> None:
        if _belongs_to_page(page, request):
            evidence.requested(request)

    def on_completed(request) -> None:
        if _belongs_to_page(page, request):
            evidence.completed(request)

    def on_failed(request) -> None:
        if _belongs_to_page(page, request):
            evidence.failed(request)

    def on_response(response) -> None:
        req = getattr(response, "request", None)
        if req is None or _belongs_to_page(page, req):
            evidence.response(response)

    listeners = (("request", on_request), ("requestfinished", on_completed),
                 ("requestfailed", on_failed), ("response", on_response))
    for event, callback in listeners:
        context.on(event, callback)
    if hasattr(page, "on"):
        page.on("pageerror", evidence.page_error)
    service_worker_seen = any(_relevant_service_worker(w) for w in getattr(context, "service_workers", ()))
    worker_check = {"status": "not_checked"}

    def service_worker_started(worker) -> None:
        nonlocal service_worker_seen
        if _relevant_service_worker(worker):
            service_worker_seen = True

    context.on("serviceworker", service_worker_started)

    async def guard() -> str | None:
        reason = contextual_network_reason(evidence)
        if reason:
            return reason
        if service_worker_seen or any(_relevant_service_worker(w) for w in getattr(context, "service_workers", ())):
            worker_check["status"] = "active_worker"
            return "data_request_unverified"
        try:
            count = await asyncio.wait_for(page.evaluate(
                "async () => ('serviceWorker' in navigator && ['http:', 'https:'].includes(location.protocol))"
                " ? (await navigator.serviceWorker.getRegistrations()).length : 0"
            ), timeout=2)
            if isinstance(count, list) and count and all(type(x) is str and x in _SEMANTIC_SIGNALS for x in count):
                count = 0
            if type(count) is not int or count != 0:
                worker_check["status"] = "registrations_present_or_invalid"
                return "data_request_unverified"
        except Exception:
            worker_check["status"] = "check_failed"
            return "data_request_unverified"
        worker_check["status"] = "no_registrations_observed"
        return contextual_network_reason(evidence)

    try:
        play_obs, play_rec = await discover.diagnose_page(page, local_step, contextual_guard=guard, **kwargs)
        if (
            play_obs.status in {discover.SubscriptionStatus.ACTIVE_UNKNOWN, discover.SubscriptionStatus.ACTIVE_DURATION}
            and kwargs.get("source_url", discover.SUBSCRIPTIONS_URL) == discover.SUBSCRIPTIONS_URL
            and play_obs.details.primary_renewal_date is None
            and (play_obs.details.bundle_next_payment_date is not None or play_obs.details.addons)
            and hasattr(page, "goto")
        ):
            one_kwargs = {k: v for k, v in kwargs.items() if k not in {"source_url", "screenshot_path"}}
            one_obs, _ = await discover.diagnose_page(
                page, local_step, source_url=discover.ONE_SETTINGS_URL, **one_kwargs
            )
            if one_obs.status in {
                discover.SubscriptionStatus.ACTIVE_KNOWN,
                discover.SubscriptionStatus.ACTIVE_UNKNOWN,
                discover.SubscriptionStatus.ACTIVE_DURATION,
                discover.SubscriptionStatus.INCONSISTENT,
                discover.SubscriptionStatus.NO_SUBSCRIPTION,
            }:
                play_obs = discover.contrast_sources(play_obs, one_obs)
                play_rec = discover.reconcile_progress(play_obs, local_step)
        print("[DISC_NET] " + json.dumps({"mode": "passive", "service_worker_seen": service_worker_seen,
              "service_worker_check": worker_check["status"],
              "evidence": evidence.summary()}, separators=(",", ":")), flush=True)
        return play_obs, play_rec
    finally:
        if hasattr(page, "remove_listener"):
            page.remove_listener("pageerror", evidence.page_error)
        for event, callback in (*listeners, ("serviceworker", service_worker_started)):
            context.remove_listener(event, callback)


def source_failure_category(observation, page_errors: int) -> str:
    """Classify diagnostic failure without inferring its cause from page-error counts."""
    if observation.status not in {discover.SubscriptionStatus.DETECTION_FAILED, discover.SubscriptionStatus.INCONSISTENT}:
        return "observed"
    if "unauthenticated" in observation.reason:
        return "authentication_required"
    if page_errors or "page-read" in observation.reason or "loading" in observation.reason:
        return "rendering_failed"
    return "insufficient_evidence"


def guard_not_applicable(observation, page_errors: int):
    # A pageerror alone is not proof that visible, readable preference content failed to render.
    # diagnose_page already classifies concrete navigation/read failures separately.
    return observation


async def semantic_evidence(page) -> dict[str, object]:
    """Return counts of visible semantic elements, never their contents or attributes."""
    script = r"""() => {
      const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
      const count = selector => Math.min(1000, [...document.querySelectorAll(selector)].filter(visible).length);
      const messages = [...document.querySelectorAll('main, [role="main"], [role="status"], [role="alert"], [aria-live]')]
        .filter(visible);
      const empty = /\b(no subscriptions?|no active subscriptions?|you have no (?:active )?subscriptions|you don['’]t have any (?:active )?subscriptions|no tienes (?:ninguna suscripción|suscripciones(?: activas)?)|no hay (?:ninguna suscripción|suscripciones(?: activas)?))\b/i;
      return {headings: count('h1,h2,h3,[role="heading"]'),
              main_regions: count('main,[role="main"]'),
              status_regions: count('[role="status"],[role="alert"],[aria-live]'),
              list_regions: count('ul,ol,[role="list"],[role="listitem"]'),
              loading_indicators: count('[role="progressbar"],progress,[aria-busy="true"]'),
              subscription_actions: Math.min(1000, [...document.querySelectorAll('button,[role="button"],a')].filter(e => visible(e) && /^(manage|manage subscription|cancel subscription|gestionar|administrar suscripción|cancelar suscripción)$/i.test((e.innerText || '').trim())).length),
              subscription_links: Math.min(1000, [...document.querySelectorAll('a[href]')].filter(e => visible(e) && /subscription|suscripcion/i.test(e.getAttribute('href') || '')).length),
              explicit_empty_regions: Math.min(1000, messages.filter(e => empty.test(e.innerText || '')).length)};
    }"""
    try:
        raw = await asyncio.wait_for(page.evaluate(script), timeout=3)
        keys = ("headings", "main_regions", "status_regions", "list_regions", "loading_indicators",
                "subscription_actions", "subscription_links", "explicit_empty_regions")
        if not isinstance(raw, dict) or any(type(raw.get(key)) is not int or not 0 <= raw[key] <= 1000 for key in keys):
            return {"available": False, "error_type": "InvalidSemanticData"}
        return {"available": True, **{key: raw[key] for key in keys}}
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__}


class DiagnosticRunner:
    def __init__(self) -> None:
        self.current: str | None = None
        self.results: dict[str, dict[str, object]] = {}
        self.task: asyncio.Task | None = None

    async def start(self, acc_id: str, screenshot: bool = False) -> str:
        if not _ID.fullmatch(acc_id):
            return "invalid account"
        row = await db.fetch_one("SELECT id, step FROM accounts WHERE id = ?", (acc_id,))
        if not row:
            return "account not found"
        profile = settings.profiles_dir / f"profile_{acc_id}"
        if not profile.is_dir() or profile.resolve().parent != settings.profiles_dir.resolve():
            return "account browser profile not found"
        if self.current or queue.runner.current or queue.runner.pending or queue.runner._queued:
            return "browser or queue busy"
        self.current = acc_id
        self.results[acc_id] = {"status": "running"}
        self.task = asyncio.create_task(self._run(acc_id, profile, int(row["step"] or 0), screenshot))
        return ""

    async def _run(self, acc_id: str, profile: Path, step: int, screenshot: bool) -> None:
        ctx = None
        try:
            async with async_playwright() as p:
                ctx = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile), headless=True, chromium_sandbox=True,
                    ignore_https_errors=False, service_workers="block", offline=True,
                    timeout=20000, args=["--disable-background-networking", "--no-proxy-server"],
                    **browser.resolve_browser(),
                )
                counts = {"allowed_get_head": 0, "blocked": 0}
                evidence = NetworkEvidence()
                ctx.on("request", evidence.requested)
                ctx.on("requestfinished", evidence.completed)
                ctx.on("requestfailed", evidence.failed)
                ctx.on("response", evidence.response)

                async def restrict(route) -> None:
                    reason = request_block_reason(route.request.url, route.request.method)
                    if reason is None:
                        counts["allowed_get_head"] += 1
                        evidence.record("allowed", route.request, "read_method")
                        await route.continue_()
                    else:
                        counts["blocked"] += 1
                        evidence.record("blocked", route.request, reason)
                        await route.abort()

                await ctx.route("**/*", restrict)
                await ctx.set_offline(False)
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                page.on("pageerror", evidence.page_error)
                shot = None
                if screenshot:
                    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    shot = str(settings.artifacts_dir / f"diagnostic_{acc_id}_{uuid4().hex}.png")
                errors_before = evidence.page_errors
                guard_result: dict[str, object] = {"checked": False, "safe": False, "reason": "data_request_unverified"}

                def contextual_guard() -> str | None:
                    guard_result["checked"] = True
                    reason = contextual_network_reason(evidence)
                    guard_result["safe"] = reason is None
                    guard_result["reason"] = reason
                    return reason

                play_observation, _ = await discover.diagnose_page(
                    page, step, screenshot_path=shot, contextual_guard=contextual_guard)
                play_errors = evidence.page_errors - errors_before
                semantics = await semantic_evidence(page)
                conditions = await empty_state_conditions(page, play_errors)
                errors_before = evidence.page_errors
                one_observation, _ = await discover.diagnose_page(page, step, source_url=discover.ONE_SETTINGS_URL)
                one_errors = evidence.page_errors - errors_before
                one_observation = guard_not_applicable(one_observation, one_errors)
                one_semantics = await semantic_evidence(page)
                observation = discover.contrast_sources(play_observation, one_observation)
                if observation.details.plan_change_pending:
                    from . import itinerary
                    observation, _ = await itinerary.continue_reads(
                        page, observation, step, itinerary.checkpoint_path(settings.artifacts_dir, acc_id))
                reconciliation = discover.reconcile_progress(observation, step)
                from . import visual_evidence
                visual = visual_evidence.read(settings.artifacts_dir, acc_id)
                auto_months = observation.ai_pro_promo_months
                visual_conflict = bool(visual and auto_months is not None and visual["confirmed_promo_months"] != auto_months)
                self.results[acc_id] = {
                    "status": "finished", "account_id": acc_id, "local_step": step,
                    "detector_version": detector_version(), "play_empty_conditions": conditions,
                    "subscription_status": observation.status.value,
                    "visual_evidence": visual, "visual_automatic_conflict": visual_conflict,
                    "verification_report": {
                        "subscription_status": observation.status.value,
                        "subscription_source": observation.source,
                        "confirmed_promo_months": visual.get("confirmed_promo_months"),
                        "confirmed_promo_source": visual.get("source"),
                        "automatic_promo_months": auto_months,
                        "evidence_conflict": visual_conflict,
                        "bundle_next_payment_date": observation.bundle_next_payment_date.isoformat() if observation.bundle_next_payment_date else None,
                        "primary_renewal_date": observation.renewal_date.isoformat() if observation.renewal_date else None,
                        "plan_change_pending": observation.details.plan_change_pending,
                        "reconciliation": reconciliation.status.value,
                    },
                    "renewal_date": observation.renewal_date.isoformat() if observation.renewal_date else None,
                    "price": observation.price, "reason": observation.reason,
                    "subscription_details": observation.details.to_dict(),
                    "source": observation.source, "certainty": observation.certainty,
                    "sources": [{"source": label, "status": item.status.value, "reason": item.reason,
                                 "failure_category": source_failure_category(item, errors), "page_errors": errors,
                                 "signals": item.signals, "certainty": item.certainty,
                                 "read_events": item.read_events,
                                 "details": item.details.to_dict()}
                                for label, item, errors in (("google_play", play_observation, play_errors),
                                                            ("google_one", one_observation, one_errors))],
                    "signals": observation.signals, "elapsed_ms": observation.elapsed_ms,
                    "reconciliation": reconciliation.status.value,
                    "reconciliation_reason": reconciliation.reason,
                    "detected_layer": reconciliation.detected_layer,
                    "network_filter_counts": counts,
                    "play_contextual_guard": guard_result,
                    "network_evidence": evidence.summary(), "semantic_evidence": semantics,
                    "google_one_semantic_evidence": one_semantics,
                    "latest_run_log": queue.run_log_metadata(acc_id),
                    "screenshot": shot if shot and Path(shot).is_file() else None,
                }
                await ctx.close()
                ctx = None
        except Exception as exc:
            self.results[acc_id] = {"status": "failed", "account_id": acc_id,
                                    "reason": f"Browser diagnostic failed: {type(exc).__name__}"}
        finally:
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass
            self.current = None


runner = DiagnosticRunner()
