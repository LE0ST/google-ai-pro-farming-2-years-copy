"""Read visible subscription evidence and compare it with the local cursor.

The persisted step is only a hint. This module reports unknown or conflicting
evidence explicitly and never changes account state or chooses an action itself.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import inspect
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")
LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SUBSCRIPTIONS_URL = "https://play.google.com/store/account/subscriptions?hl=en"
ONE_SETTINGS_URL = "https://one.google.com/u/0/settings?expand=upgrade&g1_landing_page=1&utm_campaign=my_google_subscriptions_card&hl=en"
_DATE_RE = re.compile(r"\b([A-Z][a-z]{2})[a-z]*\s+(\d{1,2}),\s+(\d{4})\b")
_ES_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?"
    r"(ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|"
    r"jul(?:io)?|ago(?:sto)?|sep(?:tiembre|t)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?)"
    r"\.?(?:\s+de|\s*,)?\s+(\d{4})\b",
    re.I,
)
_PRICE_RE = re.compile(r"(?:[$€£]\s?\d[\d,.]*|\b(?:USD|EUR|GBP)\s?\d[\d,.]*)", re.I)
_NO_SUBSCRIPTION = re.compile(
    r"\b(?:no subscriptions?|no active subscriptions?|you don['’]t have any (?:active )?subscriptions|"
    r"you have no (?:active )?subscriptions|no tienes (?:ninguna suscripción|suscripciones(?: activas)?)|"
    r"no hay (?:ninguna suscripción|suscripciones(?: activas)?)|ninguna suscripción activa|"
    r"sin suscripciones(?: activas)?)\b", re.I
)
_ACTIVE = re.compile(
    r"\b(?:active|activa|activo|renews? on|next payment|next billing date|próximo pago|próximo cobro|"
    r"se renueva|plan actual|current plan|free trial ends? on|trial ends? on|promo(?:tion)? ends? on|"
    r"promoción termina el|vence el)\b",
    re.I,
)
_CANCELED = re.compile(r"\b(?:canceled|cancelled|cancelad[ao]s?)\b", re.I)
_LOADING = re.compile(r"\b(?:loading|cargando|please wait|espera un momento)\b", re.I)
_SIGN_IN = re.compile(r"\b(?:sign in to (?:your )?(?:google )?account|use your google account|"
                      r"inicia sesión (?:en|con)|acceder a tu cuenta)\b", re.I)
_PRIMARY_SERVICE_RE = re.compile(r"\bGoogle\s+AI\s+Pro(?:\s*\(\s*\d+\s*TB\s*\))?", re.I)
_ADDON_SERVICE_RE = re.compile(
    r"\bYouTube\s+Premium(?:\s+Individual)?(?:\s+Add[- ]?On)?\b",
    re.I,
)
_BUNDLE_HEADER_RE = re.compile(
    r"(Google\s+AI\s+Pro(?:\s*\(\s*\d+\s*TB\s*\))?)\s*\+\s*"
    r"(YouTube\s+Premium(?:\s+Individual)?(?:\s+Add[- ]?On)?)",
    re.I,
)
_PROMO_DURATION_RE = re.compile(
    r"(?:(?:[$€£]|USD|EUR|GBP)\s?0(?:[.,]00)?\s*(?:/\s*(?:mes|month|mo)\s*)?(?:durante|for|por)\s+(\d+)\s*(?:mes(?:es)?|months?)"
    r"|\b(\d+)[- ]month\s+free\s+trial\b"
    r"|\bprueba\s+gratuita\s+de\s+(\d+)\s+mes(?:es)?\b"
    r"|\b(\d+)\s+(?:mes(?:es)?|months?)\s+(?:de\s+prueba\s+gratuita|sin\s+costo|gratis|free)\b)",
    re.I,
)
_PROMO_MARKER_RE = re.compile(
    r"\b(?:free\s+trial|trial|promo(?:tion)?|promoci[oó]n|prueba(?:\s+gratuita)?|"
    r"durante\s+\d+\s+mes(?:es)?|for\s+\d+\s+months?)\b",
    re.I,
)
_NEXT_PAYMENT_RE = re.compile(
    r"\b(?:next\s+payment|next\s+billing(?:\s+date)?|pr[oó]ximo\s+(?:pago|cobro)|"
    r"pr[oó]xima\s+fecha\s+de\s+(?:facturaci[oó]n|pago|cobro))\b",
    re.I,
)
_SENSITIVE_URL_PARTS = (
    "checkout", "purchase", "payment", "billing", "resubscribe",
    "cancel", "/subscribe", "/buy", "upgrade", "downgrade", "delete", "remove",
)

# months already granted -> (first layer still worth submitting, human label)
STAGES = [
    (0, "layer 1 (kosong)"),
    (12, "layer 2 (12 bulan)"),
    (18, "layer 3 (18 bulan)"),
    (24, "selesai (24 bulan)"),
]


def today() -> date:
    """Google bills in the browser timezone, which the runner pins to LA."""
    return datetime.now(LA).date()


def add_months(base: date, months: int) -> date:
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def months_from(base: date, target: date) -> int:
    delta = (target.year - base.year) * 12 + (target.month - base.month)
    if target.day < base.day - 3:
        delta -= 1
    return delta


@dataclass
class Stage:
    start_layer: int          # 1..3, or 4 meaning "already full"
    have_months: int
    label: str

    @property
    def done(self) -> bool:
        return self.start_layer == 4


class SubscriptionStatus(str, Enum):
    NO_SUBSCRIPTION = "no_subscription"
    NOT_APPLICABLE = "not_applicable"
    ACTIVE_KNOWN = "active_known_date"
    ACTIVE_DURATION = "active_known_duration"
    ACTIVE_UNKNOWN = "active_unknown_date"
    INCONSISTENT = "inconsistent"
    DETECTION_FAILED = "detection_failed"


class ReconciliationStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ServiceComponent:
    name: str
    role: str  # "primary" or "addon"
    renewal_date: date | None = None
    promo_end_date: date | None = None
    promo_months: int | None = None
    promo_price: str | None = None
    regular_price: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "renewal_date": self.renewal_date.isoformat() if self.renewal_date else None,
            "promo_end_date": self.promo_end_date.isoformat() if self.promo_end_date else None,
            "promo_months": self.promo_months,
            "promo_price": self.promo_price,
            "regular_price": self.regular_price,
        }


@dataclass(frozen=True)
class SubscriptionDetails:
    plan_change_pending: bool = False
    plan_change_observations: tuple[dict[str, object], ...] = ()
    evidence: tuple[dict[str, object], ...] = ()
    primary_service: str | None = None
    addons: tuple[str, ...] = ()
    bundle_next_payment_date: date | None = None
    primary_next_payment_date: date | None = None
    bundle_next_payment_price: str | None = None
    primary_renewal_date: date | None = None
    ai_pro_promo_end_date: date | None = None
    ai_pro_promo_months: int | None = None
    ai_pro_promo_source: str | None = None
    primary_price: str | None = None
    addon_renewal_date: date | None = None
    addon_promo_months: int | None = None
    addon_price: str | None = None
    components: tuple[ServiceComponent, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_change_pending": self.plan_change_pending,
            "plan_change_observations": list(self.plan_change_observations),
            "evidence": list(self.evidence),
            "bundle_payment_date_observed": self.bundle_next_payment_date is not None,
            "primary_service": self.primary_service,
            "addons": list(self.addons),
            "bundle_next_payment_date": (
                self.bundle_next_payment_date.isoformat() if self.bundle_next_payment_date else None
            ),
            "primary_next_payment_date": (self.primary_next_payment_date.isoformat() if self.primary_next_payment_date else None),
            "bundle_next_payment_price": self.bundle_next_payment_price,
            "primary_renewal_date": (
                self.primary_renewal_date.isoformat() if self.primary_renewal_date else None
            ),
            "ai_pro_promo_end_date": (
                self.ai_pro_promo_end_date.isoformat() if self.ai_pro_promo_end_date else None
            ),
            "ai_pro_promo_months": self.ai_pro_promo_months,
            "ai_pro_promo_source": self.ai_pro_promo_source,
            "primary_price": self.primary_price,
            "addon_renewal_date": (
                self.addon_renewal_date.isoformat() if self.addon_renewal_date else None
            ),
            "addon_promo_months": self.addon_promo_months,
            "addon_price": self.addon_price,
            "components": [component.to_dict() for component in self.components],
        }


@dataclass(frozen=True)
class SubscriptionObservation:
    status: SubscriptionStatus
    renewal_date: date | None = None
    price: str | None = None
    months_remaining: int | None = None
    reason: str = ""
    signals: tuple[str, ...] = ()
    elapsed_ms: int = 0
    source: str = "unverified"
    certainty: str = "insufficient"
    details: SubscriptionDetails = field(default_factory=SubscriptionDetails)
    read_events: tuple[dict[str, object], ...] = ()

    @property
    def primary_service(self) -> str | None:
        return self.details.primary_service

    @property
    def addons(self) -> tuple[str, ...]:
        return self.details.addons

    @property
    def bundle_next_payment_date(self) -> date | None:
        return self.details.bundle_next_payment_date

    @property
    def bundle_next_payment_price(self) -> str | None:
        return self.details.bundle_next_payment_price

    @property
    def primary_renewal_date(self) -> date | None:
        return self.details.primary_renewal_date or self.renewal_date

    @property
    def ai_pro_promo_end_date(self) -> date | None:
        return self.details.ai_pro_promo_end_date

    @property
    def ai_pro_promo_months(self) -> int | None:
        return self.details.ai_pro_promo_months

    @property
    def primary_price(self) -> str | None:
        return self.details.primary_price or self.price

    @property
    def addon_renewal_date(self) -> date | None:
        return self.details.addon_renewal_date

    @property
    def addon_promo_months(self) -> int | None:
        return self.details.addon_promo_months

    @property
    def addon_price(self) -> str | None:
        return self.details.addon_price


def _is_zero_price(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    return bool(digits) and set(digits) == {"0"}


def _price_cents(raw: str) -> int | None:
    token = re.search(r"\d[\d.,]*", raw)
    if not token:
        return None
    num = token.group(0).rstrip(".,")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,2})?", num):
        num = num.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", num):
        num = num.replace(",", ".")
    elif not re.fullmatch(r"\d+(\.\d{1,2})?", num):
        return None
    try:
        return round(float(num) * 100)
    except ValueError:
        return None


def _normalize_price(raw: str | None) -> tuple[str, int] | str | None:
    if not raw:
        return None
    cents = _price_cents(raw)
    upper = raw.upper()
    if "EUR" in upper or "€" in upper:
        currency = "EUR"
    elif "GBP" in upper or "£" in upper:
        currency = "GBP"
    else:
        currency = "USD"
    return (currency, cents) if cents is not None else raw.strip().upper()


def _parse_dates(segment: str) -> tuple[list[date], bool]:
    en_months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                 "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    es_months = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
                 "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
    found: list[tuple[int, date]] = []
    for match in _DATE_RE.finditer(segment):
        month = en_months.get(match.group(1).lower())
        if month is None:
            continue
        try:
            found.append((match.start(), date(int(match.group(3)), month, int(match.group(2)))))
        except ValueError:
            return [], True
    for match in _ES_DATE_RE.finditer(segment):
        month = es_months.get(match.group(2)[:3].lower())
        if month is None:
            continue
        try:
            found.append((match.start(), date(int(match.group(3)), month, int(match.group(1)))))
        except ValueError:
            return [], True
    found.sort(key=lambda item: item[0])
    return [item[1] for item in found], False


def _dedupe_service_names(names: Sequence[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for raw in names:
        norm = " ".join(raw.split())
        if not norm:
            continue
        replaced = False
        for idx, existing in enumerate(cleaned):
            if norm.lower() == existing.lower():
                replaced = True
                break
            if existing.lower() in norm.lower():
                cleaned[idx] = norm
                replaced = True
                break
            if norm.lower() in existing.lower():
                replaced = True
                break
        if not replaced:
            cleaned.append(norm)
    return tuple(cleaned)


def _split_clauses(text: str) -> list[str]:
    working = _BUNDLE_HEADER_RE.sub("\n__BUNDLE_HEADER__\n", text)
    raw_parts = re.split(r"\r?\n|[·|]|;\s*(?!(?:despu[eé]s|then|luego)\b)", working)
    clauses: list[str] = []
    service_or_bundle = re.compile(
        r"__BUNDLE_HEADER__|" + _PRIMARY_SERVICE_RE.pattern + r"|" + _ADDON_SERVICE_RE.pattern,
        re.I,
    )
    preposition_tail = re.compile(r"\b(?:for|para|de|of|en|in)\s*$", re.I)
    for raw_part in raw_parts:
        for sentence in re.split(r"\.\s+(?=[A-Z_])", raw_part):
            part = " ".join(sentence.split())
            if not part:
                continue
            matches = list(service_or_bundle.finditer(part))
            if len(matches) <= 1 and (not matches or matches[0].start() == 0):
                clauses.append(part)
                continue
            last_idx = 0
            for match in matches:
                pos = match.start()
                if pos <= last_idx:
                    if match.group(0) == "__BUNDLE_HEADER__" and match.end() < len(part):
                        clauses.append("__BUNDLE_HEADER__")
                        last_idx = match.end()
                    continue
                prefix = part[last_idx:pos].strip()
                suffix = part[pos:].strip()
                if not prefix or preposition_tail.search(prefix):
                    continue
                prefix_dates, _ = _parse_dates(prefix)
                suffix_dates, _ = _parse_dates(suffix)
                prefix_has_evidence = bool(
                    service_or_bundle.search(prefix)
                    or prefix_dates
                    or _PROMO_DURATION_RE.search(prefix)
                )
                suffix_has_evidence = bool(
                    suffix_dates
                    or _PROMO_DURATION_RE.search(suffix)
                    or _PRICE_RE.search(suffix)
                    or _ACTIVE.search(suffix)
                )
                if prefix_has_evidence and suffix_has_evidence:
                    clauses.append(prefix)
                    last_idx = pos
                    if match.group(0) == "__BUNDLE_HEADER__" and match.end() < len(part):
                        clauses.append("__BUNDLE_HEADER__")
                        last_idx = match.end()
            tail = part[last_idx:].strip()
            if tail:
                clauses.append(tail)
    return clauses


def merge_subscription_details(first: SubscriptionDetails, second: SubscriptionDetails) -> SubscriptionDetails:
    primary_candidates = [name for name in (first.primary_service, second.primary_service) if name]
    deduped_primary = _dedupe_service_names(primary_candidates)
    primary_service = deduped_primary[0] if deduped_primary else None
    addons = _dedupe_service_names((*first.addons, *second.addons))
    bundle_date = first.bundle_next_payment_date or second.bundle_next_payment_date
    bundle_price = first.bundle_next_payment_price or second.bundle_next_payment_price
    primary_renewal = first.primary_renewal_date or second.primary_renewal_date
    promo_end = first.ai_pro_promo_end_date or second.ai_pro_promo_end_date
    promo_months = (
        first.ai_pro_promo_months
        if first.ai_pro_promo_months is not None
        else second.ai_pro_promo_months
    )
    primary_price = first.primary_price or second.primary_price
    addon_renewal = first.addon_renewal_date or second.addon_renewal_date
    addon_promo_months = (
        first.addon_promo_months
        if first.addon_promo_months is not None
        else second.addon_promo_months
    )
    addon_price = first.addon_price or second.addon_price
    components: list[ServiceComponent] = []
    if primary_service or primary_renewal or promo_end or promo_months is not None or primary_price:
        components.append(
            ServiceComponent(
                name=primary_service or "Google AI Pro",
                role="primary",
                renewal_date=primary_renewal,
                promo_end_date=promo_end,
                promo_months=promo_months,
                promo_price=next(
                    (c.promo_price for c in (*first.components, *second.components) if c.role == "primary" and c.promo_price),
                    None,
                ),
                regular_price=primary_price,
            )
        )
    if addons or addon_renewal or addon_promo_months is not None or addon_price:
        components.append(
            ServiceComponent(
                name=addons[0] if addons else "YouTube Premium",
                role="addon",
                renewal_date=addon_renewal,
                promo_months=addon_promo_months,
                promo_price=next(
                    (c.promo_price for c in (*first.components, *second.components) if c.role == "addon" and c.promo_price),
                    None,
                ),
                regular_price=addon_price,
            )
        )
    return SubscriptionDetails(
        plan_change_pending=first.plan_change_pending or second.plan_change_pending,
        plan_change_observations=tuple(item for i, item in enumerate((*first.plan_change_observations, *second.plan_change_observations))
            if item not in (*first.plan_change_observations, *second.plan_change_observations)[:i]),
        evidence=tuple(item for i, item in enumerate((*first.evidence, *second.evidence))
                       if item not in (*first.evidence, *second.evidence)[:i]),
        primary_service=primary_service,
        addons=addons,
        bundle_next_payment_date=bundle_date,
        primary_next_payment_date=first.primary_next_payment_date or second.primary_next_payment_date,
        bundle_next_payment_price=bundle_price,
        primary_renewal_date=primary_renewal,
        ai_pro_promo_end_date=promo_end,
        ai_pro_promo_months=promo_months,
        ai_pro_promo_source=(first.ai_pro_promo_source if first.ai_pro_promo_months is not None
                             else second.ai_pro_promo_source),
        primary_price=primary_price,
        addon_renewal_date=addon_renewal,
        addon_promo_months=addon_promo_months,
        addon_price=addon_price,
        components=tuple(components),
    )


def is_allowed_plan_detail_url(url: str) -> bool:
    """Allow only official read-only Google Play or Google One plan/subscription URLs."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if parsed.scheme != "https" or host not in {"play.google.com", "one.google.com"}:
        return False
    target = path.lower()
    if any(part in target for part in _SENSITIVE_URL_PARTS):
        return False
    if host == "play.google.com":
        return path == "/store/account/subscriptions" or path.startswith("/store/account/subscriptions/")
    if path in {"/settings", "/about/plans", "/plans"} or bool(re.fullmatch(r"/u/\d+/settings", path)) or path.startswith("/settings/"):
        query = parse_qs(parsed.query.lower())
        # This exact documented UI flag selects the current-plan panel; other mutating-looking query terms stay rejected.
        return not any(part in "&".join(f"{k}={v}" for k, values in query.items() for v in values)
                       for part in _SENSITIVE_URL_PARTS if part != "upgrade")
    return False


async def discover_plan_detail_urls(page) -> tuple[str, ...]:
    """Read official plan-detail links from the DOM without clicking any control."""
    if not hasattr(page, "evaluate"):
        return ()
    script = r"""() => {
      const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
      const links = [...document.querySelectorAll('a[href]')].filter(visible);
      const out = [];
      for (const a of links) {
        try {
          const resolved = new URL(a.getAttribute('href') || '', location.href).toString();
          out.push(resolved);
        } catch {}
      }
      return out.slice(0, 10);
    }"""
    try:
        raw = await asyncio.wait_for(page.evaluate(script), 1)
    except Exception:
        return ()
    if not isinstance(raw, list):
        return ()
    current_url = getattr(page, "url", "")
    urls: list[str] = []
    for item in raw:
        if type(item) is str and item != current_url and is_allowed_plan_detail_url(item):
            if item not in urls:
                urls.append(item)
    return tuple(urls)


def merge_observations(
    base: SubscriptionObservation,
    detail: SubscriptionObservation,
    *,
    today_date: date | None = None,
) -> SubscriptionObservation:
    """Enrich an active observation with official read-only plan details without guessing dates."""
    active = {SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.ACTIVE_DURATION, SubscriptionStatus.ACTIVE_UNKNOWN}
    if base.status in {SubscriptionStatus.NOT_APPLICABLE, SubscriptionStatus.DETECTION_FAILED} and detail.status in active:
        return detail
    if detail.status == SubscriptionStatus.INCONSISTENT:
        return replace(detail, details=merge_subscription_details(base.details, detail.details))
    if base.status not in active or detail.status not in active:
        return base
    merged_details = merge_subscription_details(base.details, detail.details)
    if (base.bundle_next_payment_date and detail.bundle_next_payment_date
            and base.bundle_next_payment_date != detail.bundle_next_payment_date):
        return SubscriptionObservation(SubscriptionStatus.INCONSISTENT,
            reason="official sources show contradictory bundle payment dates", details=merged_details)
    if (base.details.ai_pro_promo_months is not None and detail.details.ai_pro_promo_months is not None
            and base.details.ai_pro_promo_months != detail.details.ai_pro_promo_months):
        return SubscriptionObservation(SubscriptionStatus.INCONSISTENT,
                                       reason="official sources show different Google AI Pro promotional durations",
                                       source=base.source, details=merged_details)
    if (
        base.renewal_date
        and detail.renewal_date
        and base.renewal_date != detail.renewal_date
    ):
        return SubscriptionObservation(
            SubscriptionStatus.INCONSISTENT,
            price=merged_details.primary_price,
            reason="multiple dates make the renewal date ambiguous",
            signals=tuple(dict.fromkeys((*base.signals, *detail.signals))),
            source=base.source,
            certainty="insufficient",
            details=merged_details,
        )
    if (
        base.details.addon_renewal_date
        and detail.details.addon_renewal_date
        and base.details.addon_renewal_date != detail.details.addon_renewal_date
    ):
        return SubscriptionObservation(
            SubscriptionStatus.INCONSISTENT,
            price=merged_details.primary_price,
            reason="multiple dates make the renewal date ambiguous",
            signals=tuple(dict.fromkeys((*base.signals, *detail.signals))),
            source=base.source,
            certainty="insufficient",
            details=merged_details,
        )
    if (
        base.price
        and detail.price
        and _normalize_price(base.price) != _normalize_price(detail.price)
    ):
        return SubscriptionObservation(
            SubscriptionStatus.INCONSISTENT,
            price=None,
            reason="official sources show different prices",
            signals=tuple(dict.fromkeys((*base.signals, *detail.signals))),
            source=base.source,
            certainty="insufficient",
            details=merged_details,
        )
    renewal = base.renewal_date or detail.renewal_date or merged_details.primary_renewal_date
    price = base.price or detail.price or merged_details.primary_price
    signals = tuple(dict.fromkeys((*base.signals, *detail.signals)))
    if renewal is not None:
        current = today_date or today()
        remaining = months_from(current, renewal)
        if remaining < 0:
            return SubscriptionObservation(
                SubscriptionStatus.INCONSISTENT,
                price=price,
                reason="renewal date is in the past",
                signals=signals,
                source=base.source,
                certainty="insufficient",
                details=merged_details,
            )
        return replace(
            base,
            status=SubscriptionStatus.ACTIVE_KNOWN,
            renewal_date=renewal,
            price=price,
            months_remaining=remaining,
            reason="subscription evidence and renewal date found",
            signals=signals,
            details=merged_details,
        )
    return replace(
        base,
        status=(SubscriptionStatus.ACTIVE_DURATION if merged_details.primary_service and
                merged_details.ai_pro_promo_months is not None else SubscriptionStatus.ACTIVE_UNKNOWN),
        renewal_date=None,
        price=price,
        months_remaining=None,
        signals=signals,
        details=merged_details,
    )


async def read_official_plan_details(
    page,
    base_observation: SubscriptionObservation,
    *,
    timeout_ms: int = 10000,
    detail_urls: Sequence[str] | None = None,
    today_date: date | None = None,
) -> SubscriptionObservation:
    """Query official plan-detail URLs via read-only GET navigation when available."""
    candidates = tuple(detail_urls) if detail_urls is not None else await discover_plan_detail_urls(page)
    if not candidates:
        return base_observation
    current = base_observation
    deadline = time.monotonic() + max(1, timeout_ms) / 1000
    for url in candidates:
        if not is_allowed_plan_detail_url(url):
            raise ValueError("unsupported diagnostic source")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Share the bounded asynchronous reader; never recursively follow links.
        detail_obs, _ = await diagnose_page(
            page, 0, source_url=url, timeout_ms=max(1, int(remaining * 500)),
            explore_details=False,
        )
        if detail_obs.details.ai_pro_promo_months is not None:
            detail_obs = replace(detail_obs, details=replace(detail_obs.details, ai_pro_promo_source=(
                "google_one_plan_details" if urlsplit(url).hostname == "one.google.com" else "google_play_plan_details")))
        events = (*current.read_events, *detail_obs.read_events)
        current = replace(merge_observations(current, detail_obs, today_date=today_date), read_events=events)
        if current.status in {SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.INCONSISTENT}:
            break
    return current


async def accessible_subscription_state(page) -> tuple[str, ...]:
    """Read only fixed semantic signals, including accessible names omitted by innerText."""
    script = r"""() => {
      const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
      const nodes = [...document.querySelectorAll('[role="status"],[role="alert"],[aria-label],img[alt]')].filter(visible);
      const names = nodes.map(e => (e.getAttribute('aria-label') || e.getAttribute('alt') || e.innerText || '').trim());
      const empty = /^(?:you (?:have no|don['’]t have any) (?:active )?subscriptions|no (?:active )?subscriptions|no tienes (?:ninguna suscripción|suscripciones(?: activas)?)|no hay suscripciones(?: activas)?)[.!]?$/i;
      const active = /^(?:your subscription is active|subscription status: active|tu suscripción está activa)[.!]?$/i;
      const result = [];
      if (names.some(s => empty.test(s))) result.push('explicit-empty');
      if (names.some(s => active.test(s))) result.push('explicit-active');
      if ([...document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible)) result.push('busy');
      const tab = [...document.querySelectorAll('[role="tab"][aria-selected="true"]')].some(e => visible(e) && /^(suscripciones|subscriptions)$/i.test((e.innerText || e.getAttribute('aria-label') || '').trim()));
      const title = /^(buscar suscripciones|discover subscriptions)$/i;
      const description = /descubre miles de servicios disponibles en google play y adminístralos fácilmente desde aquí|discover thousands of services (?:available )?(?:on|in) google play.{0,80}manage/i;
      const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"],div,span,p')].filter(e => visible(e) && title.test((e.innerText || '').replace(/\s+/g, ' ').trim()));
      const errors = [...document.querySelectorAll('[role="alert"], [aria-invalid="true"]')].some(visible);
      const items = [...document.querySelectorAll('[data-subscription-id],[data-subscription-card],[data-subscription-row],main article,main [role="listitem"],main li')].some(visible) || [...document.querySelectorAll('button,a,[role="button"]')].some(e => visible(e) && /^(manage subscription|cancel subscription|administrar suscripción|cancelar suscripción)$/i.test((e.innerText || e.getAttribute('aria-label') || '').trim()));
      const context = headings.some(h => { let e=h.parentElement; for (let i=0;e && e!==document.body && i<3;i++,e=e.parentElement) { if (description.test((e.innerText || '').replace(/\s+/g, ' '))) return true; } return false; });
      if (document.readyState === 'complete' && tab && context && !errors && !items && !result.includes('busy') && !names.some(s => active.test(s))) result.push('contextual-empty');
      return result;
    }"""
    if not hasattr(page, "evaluate"):
        return ()
    try:
        result = await asyncio.wait_for(page.evaluate(script), 1)
        if isinstance(result, list):
            return tuple(value for value in result if type(value) is str and value in
                         {"explicit-empty", "explicit-active", "contextual-empty", "busy"})
    except Exception:
        pass
    return ()


def contrast_sources(first: SubscriptionObservation, second: SubscriptionObservation) -> SubscriptionObservation:
    """Require agreement; absence of contradictory evidence is not corroboration."""
    active = {SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.ACTIVE_DURATION, SubscriptionStatus.ACTIVE_UNKNOWN}
    source = "google_play+google_one"
    merged_details = merge_subscription_details(first.details, second.details)
    if first.status == SubscriptionStatus.NO_SUBSCRIPTION and second.status == SubscriptionStatus.NOT_APPLICABLE:
        return replace(first, source="google_play", certainty="explicit_context", reason="Google Play confirms empty state; Google One settings are not applicable")
    if first.status == second.status == SubscriptionStatus.NO_SUBSCRIPTION:
        return replace(first, source=source, certainty="corroborated", reason="both official pages explicitly report no subscription")
    if first.status in active and second.status in active:
        if (first.bundle_next_payment_date and second.bundle_next_payment_date
                and first.bundle_next_payment_date != second.bundle_next_payment_date):
            reason = "official sources show contradictory bundle payment dates"
        elif (first.details.ai_pro_promo_months is not None and second.details.ai_pro_promo_months is not None
                and first.details.ai_pro_promo_months != second.details.ai_pro_promo_months):
            reason = "official sources show different Google AI Pro promotional durations"
        elif first.renewal_date and second.renewal_date and first.renewal_date != second.renewal_date:
            reason = "official sources show different dates; renewal and term end may differ"
        elif (
            first.details.addon_renewal_date
            and second.details.addon_renewal_date
            and first.details.addon_renewal_date != second.details.addon_renewal_date
        ):
            reason = "official sources show different dates; renewal and term end may differ"
        elif first.price and second.price and _normalize_price(first.price) != _normalize_price(second.price):
            reason = "official sources show different prices"
        else:
            selected = first if first.renewal_date else second
            price = first.price or second.price or merged_details.primary_price
            signals = tuple(dict.fromkeys((*first.signals, *second.signals)))
            return replace(
                selected,
                status=(SubscriptionStatus.ACTIVE_KNOWN if selected.renewal_date else
                        SubscriptionStatus.ACTIVE_DURATION if merged_details.primary_service and
                        merged_details.ai_pro_promo_months is not None else SubscriptionStatus.ACTIVE_UNKNOWN),
                price=price,
                signals=signals,
                details=merged_details,
                source=source,
                certainty="corroborated",
                read_events=(*first.read_events, *second.read_events),
                reason="both official pages explicitly indicate an active subscription",
            )
    elif (first.status == SubscriptionStatus.NO_SUBSCRIPTION and second.status in active) or (
            second.status == SubscriptionStatus.NO_SUBSCRIPTION and first.status in active):
        reason = "official sources contradict each other: empty versus active"
    else:
        reason = f"insufficient evidence across sources: Google Play={first.status.value}; Google One={second.status.value}"
    return SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=reason, source=source, details=merged_details,
                                   read_events=(*first.read_events, *second.read_events))


@dataclass(frozen=True)
class ProgressReconciliation:
    status: ReconciliationStatus
    local_step: int
    local_expected_layer: int | None
    detected_layer: int | None
    reason: str


def observe_plan_change(text: str) -> tuple[dict[str, object], ...]:
    """Extract only explicit change clauses, never a neighboring bundle billing date."""
    results = []
    for clause in re.split(r"[\n;·]+", text):
        pending = bool(re.search(r"plan changing|plan change pending|plan (?:will|is scheduled to) change|cambio de plan pendiente|cambiando (?:de )?plan", clause, re.I))
        completed = bool(re.search(r"plan change (?:completed|complete)|cambio de plan completado", clause, re.I))
        unknown = bool(re.search(r"plan change status unknown|estado del cambio desconocido", clause, re.I))
        if not (pending or completed or unknown):
            continue
        dates, invalid = _parse_dates(clause)
        dated = bool(re.search(r"effective (?:on|date)|takes effect on|scheduled (?:for|on)|change (?:on|to .+ on)|efectiv[oa] el|a partir del", clause, re.I))
        service = "YouTube Premium" if _ADDON_SERVICE_RE.search(clause) else "Google AI Pro" if _PRIMARY_SERVICE_RE.search(clause) else None
        removal = bool(re.search(r"remov|cancel|elimin|retir", clause, re.I))
        result = re.search(r"(?:change to|resulting plan:|plan resultante:)\s*(Google AI Pro(?:\s*\(\d+ TB\))?)", clause, re.I)
        results.append({"status": "unknown" if unknown or (pending and completed) else "pending" if pending else "completed",
            "scope": "addon" if service == "YouTube Premium" else "primary" if service else "unknown",
            "change_type": "addon_removal" if service == "YouTube Premium" and removal else "unspecified",
            "effective_date": dates[0].isoformat() if dated and not invalid and len(dates) == 1 else None,
            "resulting_plan": "Google AI Pro" if result else None,
            "source": "unverified", "confidence": "explicit_clause"})
    return tuple(results)


def observe_subscription_text(text: str, *, today_date: date | None = None) -> SubscriptionObservation:
    """Classify visible subscription-page text conservatively; never infer success from silence."""
    normalized = " ".join(text.split())
    if not normalized:
        return SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason="page text is empty")

    all_prices = list(dict.fromkeys(match.group(0).strip() for match in _PRICE_RE.finditer(normalized)))
    fallback_price = all_prices[0] if len(all_prices) == 1 else None
    parsed_dates, has_invalid_date = _parse_dates(normalized)
    if has_invalid_date:
        return SubscriptionObservation(SubscriptionStatus.INCONSISTENT, price=fallback_price,
                                       reason="subscription page contains an invalid date")

    bundle_primaries: list[str] = []
    bundle_addons: list[str] = []
    for match in _BUNDLE_HEADER_RE.finditer(normalized):
        bundle_primaries.append(match.group(1))
        bundle_addons.append(match.group(2))

    primary_matches = _dedupe_service_names(
        [*bundle_primaries, *(m.group(0) for m in _PRIMARY_SERVICE_RE.finditer(normalized))]
    )
    primary_service = primary_matches[0] if primary_matches else None
    addons = _dedupe_service_names(
        [*bundle_addons, *(m.group(0) for m in _ADDON_SERVICE_RE.finditer(normalized))]
    )
    is_composite = bool(addons) or bool(bundle_primaries)

    primary_dates: list[date] = []
    primary_payment_dates: list[date] = []
    ai_pro_promo_end_dates: list[date] = []
    ai_pro_promo_months_list: list[int] = []
    primary_prices: list[str] = []
    primary_promo_prices: list[str] = []
    addon_dates: list[date] = []
    addon_promo_months_list: list[int] = []
    addon_prices: list[str] = []
    addon_promo_prices: list[str] = []
    bundle_dates: list[date] = []
    bundle_prices: list[str] = []

    current_subject = "bundle" if bundle_primaries else "unscoped"
    bare_service_role: str | None = None

    for clause in _split_clauses(text):
        has_bundle_tok = "__BUNDLE_HEADER__" in clause
        clean_clause = " ".join(clause.replace("__BUNDLE_HEADER__", "").split())
        if observe_plan_change(clean_clause):
            # A change's effective date is not an individual renewal or a bundle payment.
            continue
        has_primary_tok = bool(_PRIMARY_SERVICE_RE.search(clean_clause))
        has_addon_tok = bool(_ADDON_SERVICE_RE.search(clean_clause))
        clause_dates, _ = _parse_dates(clean_clause)
        clause_prices = [m.group(0).strip() for m in _PRICE_RE.finditer(clean_clause)]
        clause_zero_prices = [p for p in clause_prices if _is_zero_price(p)]
        clause_nonzero_prices = [p for p in clause_prices if not _is_zero_price(p)]
        promo_match = _PROMO_DURATION_RE.search(clean_clause)
        clause_promo_months = (
            int(next(g for g in promo_match.groups() if g is not None))
            if promo_match else None
        )
        is_bare_service = (
            not clause_dates
            and not clause_prices
            and clause_promo_months is None
            and not _NEXT_PAYMENT_RE.search(clean_clause)
        )

        if has_bundle_tok or (has_primary_tok and has_addon_tok):
            current_subject = "bundle"
            bare_service_role = None
        elif has_primary_tok:
            if bare_service_role == "addon" and is_bare_service:
                current_subject = "bundle"
                bare_service_role = None
            else:
                current_subject = "primary"
                bare_service_role = "primary" if is_bare_service else None
        elif has_addon_tok:
            if bare_service_role == "primary" and is_bare_service:
                current_subject = "bundle"
                bare_service_role = None
            else:
                current_subject = "addon"
                bare_service_role = "addon" if is_bare_service else None
        else:
            bare_service_role = None

        if not is_composite:
            target = "primary"
        elif current_subject == "primary":
            only_addon_price = bool(clause_nonzero_prices) and all(
                _price_cents(p) == 1199 for p in clause_nonzero_prices
            )
            if only_addon_price or (
                not has_primary_tok
                and _NEXT_PAYMENT_RE.search(clean_clause)
                and not _PROMO_MARKER_RE.search(clean_clause)
            ):
                target = "bundle"
            else:
                target = "primary"
        elif current_subject == "addon":
            target = "addon"
        else:
            target = "bundle"

        if target == "primary":
            promo_end_only = bool(re.search(
                r"\b(?:trial ends? on|promo(?:tion)? ends? on|promoción termina el|vence el)\b",
                clean_clause, re.I,
            )) and not bool(re.search(r"\b(?:renews? on|renewal date|se renueva|fecha de renovación)\b", clean_clause, re.I))
            payment_only = bool(_NEXT_PAYMENT_RE.search(clean_clause)) and not bool(re.search(
                r"\b(?:renews? on|renewal date|se renueva|fecha de renovación)\b", clean_clause, re.I))
            if payment_only:
                primary_payment_dates.extend(clause_dates)
            if not promo_end_only and not payment_only:
                primary_dates.extend(clause_dates)
            if _PROMO_MARKER_RE.search(clean_clause) or clause_zero_prices:
                ai_pro_promo_end_dates.extend(clause_dates)
            if clause_promo_months is not None:
                ai_pro_promo_months_list.append(clause_promo_months)
            primary_prices.extend(clause_nonzero_prices)
            primary_promo_prices.extend(clause_zero_prices)
        elif target == "addon":
            addon_dates.extend(clause_dates)
            if _NEXT_PAYMENT_RE.search(clean_clause):
                bundle_dates.extend(clause_dates)
                bundle_prices.extend(clause_nonzero_prices)
            if clause_promo_months is not None:
                addon_promo_months_list.append(clause_promo_months)
            addon_prices.extend(clause_nonzero_prices)
            addon_promo_prices.extend(clause_zero_prices)
        else:
            bundle_dates.extend(clause_dates)
            bundle_prices.extend(clause_nonzero_prices)
            if any(_price_cents(p) == 1199 for p in clause_nonzero_prices):
                addon_dates.extend(clause_dates)
                addon_prices.extend(p for p in clause_nonzero_prices if _price_cents(p) == 1199)

    unique_primary_dates = list(dict.fromkeys(primary_dates))
    unique_primary_payment_dates = list(dict.fromkeys(primary_payment_dates))
    unique_promo_end_dates = list(dict.fromkeys(ai_pro_promo_end_dates))
    unique_addon_dates = list(dict.fromkeys(addon_dates))
    unique_bundle_dates = list(dict.fromkeys(bundle_dates))
    unique_primary_prices = list(dict.fromkeys(primary_prices))
    unique_addon_prices = list(dict.fromkeys(addon_prices))
    unique_bundle_prices = list(dict.fromkeys(bundle_prices))
    unique_primary_promo_months = list(dict.fromkeys(ai_pro_promo_months_list))
    unique_addon_promo_months = list(dict.fromkeys(addon_promo_months_list))

    primary_price = (
        unique_primary_prices[0]
        if len({_normalize_price(p) for p in unique_primary_prices}) == 1
        else (
            primary_promo_prices[0]
            if not unique_primary_prices
            and len(dict.fromkeys(primary_promo_prices)) == 1
            and not is_composite
            else None
        )
    )
    addon_price = (
        unique_addon_prices[0]
        if len({_normalize_price(p) for p in unique_addon_prices}) == 1
        else None
    )
    bundle_price = (
        unique_bundle_prices[0]
        if len({_normalize_price(p) for p in unique_bundle_prices}) == 1
        else None
    )
    primary_renewal = unique_primary_dates[0] if len(unique_primary_dates) == 1 else None
    promo_end = unique_promo_end_dates[0] if len(unique_promo_end_dates) == 1 else None
    addon_renewal = unique_addon_dates[0] if len(unique_addon_dates) == 1 else None
    bundle_next_date = unique_bundle_dates[0] if len(unique_bundle_dates) == 1 else None
    ai_pro_promo_months = (
        unique_primary_promo_months[0] if len(unique_primary_promo_months) == 1 else None
    )
    addon_promo_months = (
        unique_addon_promo_months[0] if len(unique_addon_promo_months) == 1 else None
    )

    components: list[ServiceComponent] = []
    if primary_service or primary_renewal or promo_end or ai_pro_promo_months is not None or primary_price:
        components.append(
            ServiceComponent(
                name=primary_service or "Google AI Pro",
                role="primary",
                renewal_date=primary_renewal,
                promo_end_date=promo_end,
                promo_months=ai_pro_promo_months,
                promo_price=primary_promo_prices[0] if primary_promo_prices else None,
                regular_price=primary_price,
            )
        )
    if addons or addon_renewal or addon_promo_months is not None or addon_price:
        components.append(
            ServiceComponent(
                name=addons[0] if addons else "YouTube Premium",
                role="addon",
                renewal_date=addon_renewal,
                promo_months=addon_promo_months,
                promo_price=addon_promo_prices[0] if addon_promo_prices else None,
                regular_price=addon_price,
            )
        )

    changes = observe_plan_change(text)
    details = SubscriptionDetails(
        plan_change_pending=any(item["status"] in {"pending", "unknown"} for item in changes),
        plan_change_observations=changes,
        primary_service=primary_service,
        addons=addons,
        bundle_next_payment_date=bundle_next_date,
        primary_next_payment_date=(unique_primary_payment_dates[0] if len(unique_primary_payment_dates) == 1 else None),
        bundle_next_payment_price=bundle_price,
        primary_renewal_date=primary_renewal,
        ai_pro_promo_end_date=promo_end,
        ai_pro_promo_months=ai_pro_promo_months,
        primary_price=primary_price,
        addon_renewal_date=addon_renewal,
        addon_promo_months=addon_promo_months,
        addon_price=addon_price,
        components=tuple(components),
    )

    no_subscription = bool(_NO_SUBSCRIPTION.search(normalized))
    stripped = _NO_SUBSCRIPTION.sub("", normalized)
    active = bool(
        _ACTIVE.search(stripped)
        or (
            primary_service
            and (
                unique_primary_promo_months
                or unique_primary_dates
                or unique_primary_payment_dates
                or unique_bundle_dates
                or unique_addon_dates
            )
        )
    )
    canceled = bool(_CANCELED.search(normalized))

    if no_subscription and (active or canceled or parsed_dates):
        return SubscriptionObservation(SubscriptionStatus.INCONSISTENT, price=primary_price,
                                       reason="page contains both no-subscription and subscription evidence",
                                       details=details)
    if no_subscription:
        return SubscriptionObservation(SubscriptionStatus.NO_SUBSCRIPTION, price=primary_price,
                                       months_remaining=0, reason="explicit no-subscription state",
                                       details=details)
    if canceled:
        return SubscriptionObservation(SubscriptionStatus.INCONSISTENT, price=primary_price,
                                       reason="canceled subscriptions require separate manual review",
                                       details=details)
    if active:
        if (
            len(unique_primary_dates) > 1
            or len(unique_primary_payment_dates) > 1
            or len(unique_addon_dates) > 1
            or len(unique_bundle_dates) > 1
            or len(unique_primary_promo_months) > 1
        ):
            return SubscriptionObservation(SubscriptionStatus.INCONSISTENT, price=primary_price,
                                           reason="multiple dates make the renewal date ambiguous",
                                           details=details)
        current = today_date or today()
        for candidate_date in (*unique_primary_dates, *unique_addon_dates, *unique_bundle_dates):
            if months_from(current, candidate_date) < 0:
                return SubscriptionObservation(SubscriptionStatus.INCONSISTENT, price=primary_price,
                                               reason="renewal date is in the past",
                                               details=details)
        if not unique_primary_dates:
            if is_composite and (unique_bundle_dates or unique_addon_dates):
                reason = "bundle next payment date does not establish Google AI Pro renewal date"
            elif ai_pro_promo_months is not None:
                reason = "promotion duration found without an explicit renewal date"
            else:
                reason = "subscription indicated without a readable renewal date"
            return SubscriptionObservation(SubscriptionStatus.ACTIVE_DURATION if primary_service and ai_pro_promo_months is not None else SubscriptionStatus.ACTIVE_UNKNOWN, price=primary_price,
                                           reason=reason, details=details)
        remaining = months_from(current, unique_primary_dates[0])
        return SubscriptionObservation(SubscriptionStatus.ACTIVE_KNOWN, unique_primary_dates[0], primary_price,
                                       remaining, "subscription evidence and renewal date found",
                                       details=details)
    return SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, price=primary_price,
                                   reason="page contains no recognized subscription state",
                                   details=details)


def reconcile_progress(
    observation: SubscriptionObservation,
    local_step: int,
    *,
    today_date: date | None = None,
) -> ProgressReconciliation:
    """Compare visible state with the local cursor without changing either one."""
    expected_layer = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4}.get(local_step)
    if observation.status == SubscriptionStatus.NO_SUBSCRIPTION:
        detected_layer = 1
    elif observation.details.plan_change_pending:
        return ProgressReconciliation(ReconciliationStatus.INDETERMINATE, local_step, expected_layer, None,
                                      "plan change pending; no completed stage can be inferred")
    elif observation.status == SubscriptionStatus.ACTIVE_KNOWN and observation.renewal_date:
        current = today_date or today()
        months = months_from(current, observation.renewal_date)
        # Only recognized milestone terms are actionable. A readable date at an
        # unexpected duration (for example six months) remains a mismatch.
        milestones = ((12, 2), (18, 3), (24, 4), (30, 4))
        detected_layer = next((layer for target, layer in milestones if abs(months - target) <= 1), None)
        if detected_layer is None:
            return ProgressReconciliation(
                ReconciliationStatus.MISMATCH, local_step, expected_layer, None,
                f"observed term of {months} months does not match a recognized milestone",
            )
    else:
        return ProgressReconciliation(
            ReconciliationStatus.INDETERMINATE, local_step, expected_layer, None,
            f"subscription state is {observation.status.value}: {observation.reason}",
        )
    if expected_layer is None:
        return ProgressReconciliation(ReconciliationStatus.INDETERMINATE, local_step, None,
                                      detected_layer, "local step is outside the known range")
    status = ReconciliationStatus.MATCH if expected_layer == detected_layer else ReconciliationStatus.MISMATCH
    return ProgressReconciliation(status, local_step, expected_layer, detected_layer,
                                  "local cursor agrees with observed state" if status == ReconciliationStatus.MATCH
                                  else "local cursor disagrees with observed state; no automatic update applied")


async def diagnose_page(
    page,
    local_step: int,
    *,
    timeout_ms: int = 25000,
    screenshot_path: str | None = None,
    poll_interval: float = 0.25,
    source_url: str = SUBSCRIPTIONS_URL,
    contextual_guard=None,
    explore_details: bool = True,
) -> tuple[SubscriptionObservation, ProgressReconciliation]:
    """Read the visible subscriptions page without clicking controls or installing hooks.

    A screenshot is created only when the caller supplies an explicit path. Such an
    image can contain account details and must be stored outside shared artifacts.
    """
    started = time.monotonic()
    deadline = started + max(timeout_ms, 1) / 1000
    failed_stage: str | None = None
    failure: Exception | None = None
    page_errors: list[str] = []
    def record_page_error(error) -> None:
        page_errors.append(type(error).__name__)
    if hasattr(page, "on"):
        page.on("pageerror", record_page_error)
    text = ""
    category = "unknown content"
    contextual_reason: str | None = None
    read_events: list[dict[str, object]] = []

    async def read_stage(stage, operation, budget):
        stage_started = time.monotonic()
        error_type = None
        try:
            return await asyncio.wait_for(operation, max(0.001, budget))
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            # Fixed metadata only: no raw exceptions, body text, query strings or IDs.
            event = {"stage": stage, "elapsed_ms": int((time.monotonic() - stage_started) * 1000),
                     "error_type": error_type, "load_state": "unknown",
                     "source": "google_play" if source_url == SUBSCRIPTIONS_URL else "google_one"}
            read_events.append(event)
            print(f"[DIAG_READ] {event}", flush=True)

    try:
        try:
            if source_url not in {SUBSCRIPTIONS_URL, ONE_SETTINGS_URL} and not is_allowed_plan_detail_url(source_url):
                raise ValueError("unsupported diagnostic source")
            response = await read_stage("navigation", page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms),
                                        max(timeout_ms, 1) / 1000)
            if response is not None and response.status >= 400:
                raise RuntimeError("navigation returned an HTTP error")
            final_url = getattr(page, "url", source_url)
            parsed_final = urlsplit(final_url)
            expected_host = urlsplit(source_url).hostname
            expected_path = urlsplit(source_url).path.rstrip("/")
            actual_path = parsed_final.path.rstrip("/")
            is_login = parsed_final.hostname == "accounts.google.com"
            is_wrong_page = parsed_final.scheme in {"http", "https"} and not is_login and (parsed_final.hostname != expected_host or
                (expected_path and expected_path not in {"/settings", "/"} and actual_path != expected_path))
            read_events.append({"stage": "navigation-result", "elapsed_ms": read_events[-1]["elapsed_ms"],
                "http_status": getattr(response, "status", None), "final_host": parsed_final.hostname,
                "final_path": actual_path, "classification": "login_required" if is_login else
                "wrong_page" if is_wrong_page else "page_reached"})
            if is_login:
                category = "login_required"
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=category)
            elif is_wrong_page:
                category = "wrong_page"
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=category)
        except Exception as exc:  # Do not log raw browser messages; they may contain URL data.
            failed_stage, failure = "navigation", exc
        observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason="no recognized state")
        # Navigation must not consume the asynchronous rendering/extraction budget.
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000
        last_read_error = None
        previous_text = None
        while failure is None and category not in {"login_required", "wrong_page"}:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                read_budget = min(1.0, remaining)
                text = await read_stage("body-extraction", page.inner_text("body", timeout=max(1, int(read_budget * 1000))), read_budget)
                last_read_error = None
            except Exception as exc:
                last_read_error = exc
                # A transient detached document or missing body can recover on the next read.
                await asyncio.sleep(min(max(poll_interval, 0.01), max(0, deadline - time.monotonic())))
                continue
            contextual_reason = None
            url = getattr(page, "url", "")
            if urlsplit(url).hostname == "accounts.google.com" or _SIGN_IN.search(text):
                category = "unauthenticated"
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=category)
                break
            observation = observe_subscription_text(text)
            if source_url == ONE_SETTINGS_URL and re.search(
                r"email (?:preferences|notifications)|preferencias de correo|notificaciones por correo", text, re.I
            ) and not re.search(r"subscription|membership|suscripción|membresía|plan activo|plan actual|current plan", text, re.I):
                observation = SubscriptionObservation(SubscriptionStatus.NOT_APPLICABLE,
                    reason="settings show only email preferences")
                category = "wrong_page"
                read_events.append({"stage": "page-classification", "classification": "wrong_page",
                                    "reason": "settings_preferences_only"})
            if source_url == ONE_SETTINGS_URL:
                # Record only allowlisted structural counts and generic heading categories.
                structural_script = """() => ({title: Math.min(20, document.querySelectorAll('h1,h2,h3,[role=heading]').length), navigation: Math.min(20, document.querySelectorAll('nav,[role=navigation]').length), links: Math.min(50, document.querySelectorAll('a[href]').length), plan_terms: Math.min(20, [...document.querySelectorAll('main,section,h1,h2,h3')].filter(e => /current plan|plan actual|google ai pro/i.test(e.innerText||'')).length), preference_terms: Math.min(20, [...document.querySelectorAll('main,section,h1,h2,h3')].filter(e => /email preferences|preferencias de correo/i.test(e.innerText||'')).length)})"""
                try:
                    structural = await asyncio.wait_for(page.evaluate(structural_script), 1)
                    if isinstance(structural, dict):
                        read_events.append({"stage": "page-structure", **{k: min(50, v) for k, v in structural.items() if k in {"title", "navigation", "links", "plan_terms", "preference_terms"} and type(v) is int}})
                except Exception as exc:
                    read_events.append({"stage": "page-structure", "error_type": type(exc).__name__})
            if observation.status == SubscriptionStatus.NOT_APPLICABLE:
                break
            semantic_started = time.monotonic()
            semantic = await accessible_subscription_state(page)
            load_state = "busy" if "busy" in semantic or _LOADING.search(text) else "no_visible_loading_indicator"
            read_events[-1]["load_state"] = load_state
            read_events.append({"stage": "render-observation", "elapsed_ms": int((time.monotonic() - semantic_started) * 1000),
                                "load_state": load_state, "error_type": None})
            if urlsplit(source_url).path in {"/plans", "/about/plans"} and not re.search(
                r"\b(?:current plan|your plan|plan actual|tu plan)\b", text, re.I
            ):
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED,
                                                     reason="public plan offers do not establish the current plan")
            if source_url == SUBSCRIPTIONS_URL and "contextual-empty" in semantic and observation.status == SubscriptionStatus.DETECTION_FAILED:
                decision = contextual_guard() if contextual_guard is not None else "data_request_unverified"
                if inspect.isawaitable(decision):
                    try:
                        decision = await asyncio.wait_for(decision, timeout=max(0.001, deadline - time.monotonic()))
                    except Exception:
                        decision = "data_request_unverified"
                if not page_errors and contextual_guard is not None and (decision is None or decision is True):
                    observation = SubscriptionObservation(SubscriptionStatus.NO_SUBSCRIPTION, months_remaining=0, reason="contextual visible empty state")
                else:
                    contextual_reason = decision if isinstance(decision, str) and decision in {"data_request_blocked", "data_request_pending", "data_request_unverified"} else "data_request_unverified"
            if "busy" in semantic or _LOADING.search(text):
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason="accessible loading indicator")
            elif "explicit-empty" in semantic:
                if observation.status in {SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.ACTIVE_DURATION, SubscriptionStatus.ACTIVE_UNKNOWN, SubscriptionStatus.INCONSISTENT} or "explicit-active" in semantic:
                    observation = SubscriptionObservation(SubscriptionStatus.INCONSISTENT, reason="accessible and visible subscription evidence conflict")
                else:
                    observation = SubscriptionObservation(SubscriptionStatus.NO_SUBSCRIPTION, months_remaining=0,
                                                         reason="explicit visible accessible empty-state message")
            elif "explicit-active" in semantic:
                if observation.status == SubscriptionStatus.NO_SUBSCRIPTION:
                    observation = SubscriptionObservation(SubscriptionStatus.INCONSISTENT, reason="accessible active state conflicts with visible empty state")
                elif observation.status == SubscriptionStatus.DETECTION_FAILED:
                    observation = SubscriptionObservation(SubscriptionStatus.ACTIVE_UNKNOWN, reason="explicit accessible active state without date")
            if observation.status not in {SubscriptionStatus.DETECTION_FAILED, SubscriptionStatus.ACTIVE_UNKNOWN, SubscriptionStatus.ACTIVE_DURATION}:
                break
            if (observation.status == SubscriptionStatus.ACTIVE_UNKNOWN and text == previous_text
                    and "busy" not in semantic and not _LOADING.search(text)):
                # Repeated readable active evidence adds no date; details links are checked below.
                break
            previous_text = text
            if (
                observation.status in {SubscriptionStatus.ACTIVE_UNKNOWN, SubscriptionStatus.ACTIVE_DURATION}
                and "busy" not in semantic
                and not _LOADING.search(text)
                and (
                    observation.details.bundle_next_payment_date is not None
                    or observation.details.addon_renewal_date is not None
                    or observation.details.ai_pro_promo_months is not None
                )
            ):
                break
            if "busy" in semantic:
                category = "accessible loading indicator"
            else:
                category = contextual_reason or ("loading" if not text.strip() or _LOADING.search(text) else
                    observation.reason if observation.reason == "public plan offers do not establish the current plan" else "unknown content")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(max(poll_interval, 0.01), remaining))
        if last_read_error is not None:
            failed_stage, failure = "page-read", last_read_error
        if observation.details.ai_pro_promo_months is not None and not observation.details.ai_pro_promo_source:
            observation = replace(observation, details=replace(observation.details, ai_pro_promo_source=(
                "google_play" if source_url == SUBSCRIPTIONS_URL else "google_one")))
        if failure is not None:
            current_url = _safe_url(getattr(page, "url", ""))
            raw_error = str(failure).casefold()
            if isinstance(failure, TimeoutError) or "timeout" in raw_error:
                category = "timeout"
            elif "net::err" in raw_error or "connection" in raw_error:
                category = "network failure"
            else:
                category = "browser failure"
            reason = f"{failed_stage} {category} ({type(failure).__name__})"
            if failed_stage == "page-read" and category == "timeout" and contextual_reason:
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=contextual_reason)
            else:
                observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED,
                                                     reason=f"navigation/read failed at {current_url}: {reason}")
        elif observation.status == SubscriptionStatus.DETECTION_FAILED:
            final_reason = {"login_required": "login_required", "wrong_page": "wrong_page",
                            "accessible loading indicator": "loading", "loading": "loading"}.get(
                                category, "unrecognized_content")
            if page_errors and observation.status == SubscriptionStatus.DETECTION_FAILED and category not in {"wrong_page", "unauthenticated", "loading"}:
                read_events.append({"stage": "javascript-errors", "count": len(page_errors), "types": sorted(set(page_errors))})
            if category in {"data_request_blocked", "data_request_pending", "data_request_unverified"}:
                final_reason = category
            observation = SubscriptionObservation(SubscriptionStatus.DETECTION_FAILED, reason=final_reason)
        read_events.append({"stage": "final-classification", "classification":
            observation.reason if observation.status == SubscriptionStatus.DETECTION_FAILED else observation.status.value,
            "page_error_count": len(page_errors), "page_error_types": sorted(set(page_errors))})
        observation = replace(observation, read_events=tuple(read_events))
        source_label = "google_play" if urlsplit(source_url).hostname == "play.google.com" else "google_one"
        facts = {"subscription_active": observation.status in {SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.ACTIVE_UNKNOWN, SubscriptionStatus.ACTIVE_DURATION},
                 "promo_months": observation.ai_pro_promo_months,
                 "bundle_payment_date_observed": observation.bundle_next_payment_date.isoformat() if observation.bundle_next_payment_date else None,
                 "primary_renewal_date": observation.renewal_date.isoformat() if observation.renewal_date else None,
                 "plan_change_pending": True if observation.details.plan_change_pending else None}
        facts_evidence = tuple({"field": key, "value": value, "source": source_label, "confidence": "explicit_visible"}
                               for key, value in facts.items() if value is not None and value is not False)
        observation = replace(observation, details=replace(observation.details,
            evidence=(*observation.details.evidence, *facts_evidence),
            plan_change_observations=tuple({**item, "source": source_label} for item in observation.details.plan_change_observations)))
        if explore_details and failure is None and observation.status in {
            SubscriptionStatus.ACTIVE_UNKNOWN, SubscriptionStatus.ACTIVE_DURATION,
            SubscriptionStatus.NOT_APPLICABLE, SubscriptionStatus.DETECTION_FAILED,
        } and category not in {"unauthenticated", "loading", "accessible loading indicator"} and not page_errors and (
            observation.status != SubscriptionStatus.DETECTION_FAILED
            or (source_url != SUBSCRIPTIONS_URL and category == "unknown content")
        ):
            # Settings is only a link-discovery source, not proof of a current plan.
            observation = await read_official_plan_details(page, observation, timeout_ms=min(timeout_ms, 10000))
        signals = [label for label, pattern in (("loading", _LOADING), ("sign-in", _SIGN_IN),
                   ("empty-state", _NO_SUBSCRIPTION), ("active", _ACTIVE),
                   ("discover-subscriptions", re.compile(r"discover subscriptions|descubre suscripciones", re.I)),
                   ("subscriptions-heading", re.compile(r"subscriptions|suscripciones", re.I)),
                   ("google-play", re.compile(r"google play", re.I))) if pattern.search(text)]
        source = "google_play" if source_url == SUBSCRIPTIONS_URL else "google_one"
        certain = observation.status in {SubscriptionStatus.NO_SUBSCRIPTION, SubscriptionStatus.ACTIVE_KNOWN, SubscriptionStatus.ACTIVE_DURATION, SubscriptionStatus.ACTIVE_UNKNOWN}
        observation = replace(observation, signals=tuple(signals), source=source, certainty="explicit" if certain else "insufficient",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
        if observation.status == SubscriptionStatus.DETECTION_FAILED:
            # A fully masked screenshot and fixed vocabulary avoid exposing account data.
            if screenshot_path:
                try:
                    shot_timeout = min(max(timeout_ms, 1), 3000)
                    await asyncio.wait_for(page.screenshot(path=screenshot_path, full_page=True,
                                           mask=[page.locator("body")], timeout=shot_timeout),
                                           timeout=shot_timeout / 1000)
                except Exception:
                    print("[DIAG] masked screenshot unavailable", flush=True)
            print(f"[DIAG] evidence=redacted signals={','.join(signals) or 'none'} "
                  f"elapsed_ms={observation.elapsed_ms}", flush=True)
        reconciliation = reconcile_progress(observation, local_step)
        expected = "visible subscription state after page navigation; no controls clicked"
        print(f"[DIAG] stage=subscription-status url={_safe_url(getattr(page, 'url', ''))} "
              f"expected={expected}; result={observation.status.value}; "
              f"renewal={observation.renewal_date or 'unknown'}; price={observation.price or 'unknown'}; "
              f"reconciliation={reconciliation.status.value}; reason={observation.reason}", flush=True)
        return observation, reconciliation
    finally:
        if hasattr(page, "remove_listener"):
            page.remove_listener("pageerror", record_page_error)


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "unavailable"
    if parsed.hostname == "play.google.com" and parsed.path == "/store/account/subscriptions":
        return "https://play.google.com/store/account/subscriptions"
    return f"{parsed.scheme}://{parsed.hostname}/[redacted]"


def stage_for(months: int, slack: int = 1) -> Stage:
    for threshold, label in reversed(STAGES):
        if months >= threshold - slack:
            return Stage(1 if threshold == 0 else min(4, threshold // 6), months, label)
    return Stage(1, months, f"{months} bulan")
