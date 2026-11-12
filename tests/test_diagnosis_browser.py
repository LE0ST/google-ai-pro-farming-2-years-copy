"""Offline semantic regression with synthetic HTML and sandboxed Edge."""
from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.async_api import async_playwright  # noqa: E402
from app.runner.diagnosis import semantic_evidence  # noqa: E402
from app.sites.google_one.discover import ReconciliationStatus, SubscriptionStatus, observe_subscription_text, accessible_subscription_state, diagnose_page  # noqa: E402


class SemanticBrowserTests(unittest.TestCase):
    def test_async_current_plan_rendering_without_network(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="msedge", headless=True, chromium_sandbox=True)
                try:
                    context = await browser.new_context(offline=True, service_workers="block")
                    page = await context.new_page()
                    await page.set_content('''<main aria-busy="true">Loading current plan</main>
                        <script>window.finishPlan = () => { document.querySelector('main').outerHTML =
                        '<main><h1>Plan actual</h1><h2>Google AI Pro (5 TB)</h2>' +
                        '<p>USD 0/mes durante 12 meses; después USD 19.99/mes</p></main>'; };</script>''')
                    from app.sites.google_one.discover import ONE_SETTINGS_URL
                    original_read = page.inner_text
                    first_read = True
                    async def read_then_render(*args, **kwargs):
                        nonlocal first_read
                        text = await original_read(*args, **kwargs)
                        if first_read:
                            first_read = False
                            await page.evaluate("setTimeout(window.finishPlan, 100)")
                        return text
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)), \
                         patch.object(page, "inner_text", side_effect=read_then_render):
                        obs, rec = await diagnose_page(page, 1, source_url=ONE_SETTINGS_URL,
                                                       timeout_ms=1500, poll_interval=0.02)
                    self.assertEqual(obs.status, SubscriptionStatus.ACTIVE_DURATION)
                    self.assertEqual(obs.ai_pro_promo_months, 12)
                    self.assertIsNone(obs.renewal_date)
                    self.assertEqual(rec.local_step, 1)
                    self.assertEqual(rec.status, ReconciliationStatus.INDETERMINATE)
                    self.assertTrue(any(e.get("load_state") == "busy" for e in obs.read_events))
                    await page.set_content('<main><h1>Google AI Pro</h1><p>USD 0/month for 12 months</p></main>')
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        obs, _ = await diagnose_page(page, 1, source_url="https://one.google.com/plans", timeout_ms=100)
                    self.assertEqual(obs.status, SubscriptionStatus.DETECTION_FAILED)
                    await context.close()
                finally:
                    await browser.close()
        asyncio.run(run())

    def test_discovery_heading_requires_explicit_empty_state(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="msedge", headless=True, chromium_sandbox=True,
                                                  args=["--disable-background-networking", "--no-proxy-server"])
                try:
                    context = await browser.new_context(offline=True, service_workers="block")
                    page = await context.new_page()
                    await page.set_content("<main><h1>Subscriptions</h1><p>Discover subscriptions</p></main>")
                    evidence = await semantic_evidence(page)
                    self.assertEqual(evidence["explicit_empty_regions"], 0)
                    self.assertEqual(observe_subscription_text(await page.inner_text("body")).status,
                                     SubscriptionStatus.DETECTION_FAILED)
                    await page.set_content("<main><h1>Subscriptions</h1><p>Discover subscriptions</p>"
                                           "<div role='status'>You have no active subscriptions</div></main>")
                    evidence = await semantic_evidence(page)
                    self.assertGreaterEqual(evidence["explicit_empty_regions"], 1)
                    self.assertEqual(observe_subscription_text(await page.inner_text("body")).status,
                                     SubscriptionStatus.NO_SUBSCRIPTION)
                    await page.set_content('<main><h1>Subscriptions</h1><img alt="No active subscriptions"></main>')
                    self.assertIn("explicit-empty", await accessible_subscription_state(page))
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=200)
                        self.assertEqual(detected.status, SubscriptionStatus.NO_SUBSCRIPTION)
                    await page.set_content('<main aria-busy="true"><h1>Discover subscriptions</h1></main>')
                    signals = await accessible_subscription_state(page)
                    self.assertIn("busy", signals)
                    self.assertNotIn("explicit-empty", signals)
                    await page.set_content('<main><div role="status" aria-label="Your subscription is active"></div></main>')
                    # An empty div has no layout box; give the visible status a size.
                    await page.locator('[role="status"]').evaluate("e => e.style.cssText='width:200px;height:20px'")
                    self.assertIn("explicit-active", await accessible_subscription_state(page))
                    await page.set_content('<main><p>No subscriptions</p><div role="status" aria-label="Your subscription is active" style="height:20px"></div></main>')
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=200)
                        self.assertEqual(detected.status, SubscriptionStatus.INCONSISTENT)
                    fixture = '''<div role="tab" aria-selected="true">Suscripciones</div><main><div>
                        <div>Buscar suscripciones</div><svg width="20" height="20"></svg>
                        <div>Descubre miles de servicios disponibles en Google Play y adminístralos fácilmente desde aquí</div>
                        </div></main>'''
                    await page.set_content(fixture)
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, rec_diag = await diagnose_page(page, 0, timeout_ms=300, contextual_guard=lambda: True)
                        self.assertEqual(detected.status, SubscriptionStatus.NO_SUBSCRIPTION)
                        self.assertEqual(rec_diag.status, ReconciliationStatus.MATCH)
                        blocked, _ = await diagnose_page(page, 0, timeout_ms=120, poll_interval=0.01,
                                                         contextual_guard=lambda: False)
                        self.assertEqual(blocked.status, SubscriptionStatus.DETECTION_FAILED)
                        self.assertEqual(blocked.reason, "data_request_unverified")
                        for reason in ("data_request_blocked", "data_request_pending", "data_request_unverified"):
                            failure, reconciliation = await diagnose_page(page, 0, timeout_ms=120, poll_interval=0.01,
                                                                         contextual_guard=lambda: reason)
                            self.assertEqual(failure.status, SubscriptionStatus.DETECTION_FAILED)
                            self.assertEqual(failure.reason, reason)
                            self.assertIsNone(reconciliation.detected_layer)
                    await page.set_content(fixture.replace('<svg width="20" height="20"></svg>', ''))
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=300, contextual_guard=lambda: True)
                        self.assertEqual(detected.status, SubscriptionStatus.NO_SUBSCRIPTION)
                    english = '''<div role="tab" aria-selected="true">Subscriptions</div><main><section>
                        <h1>Discover subscriptions</h1><svg width="20" height="20"></svg>
                        <p>Discover thousands of services available on Google Play and manage them here</p>
                        </section></main>'''
                    await page.set_content(english)
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=300, contextual_guard=lambda: True)
                        self.assertEqual(detected.status, SubscriptionStatus.NO_SUBSCRIPTION)
                    await page.set_content(english.replace('aria-selected="true"', 'aria-selected="false"'))
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=1000, poll_interval=0.01)
                        self.assertEqual(detected.status, SubscriptionStatus.DETECTION_FAILED)
                    await page.set_content(fixture.replace('<main>', '<main aria-busy="true">'))
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=1000, poll_interval=0.01)
                        self.assertEqual(detected.status, SubscriptionStatus.DETECTION_FAILED)
                        self.assertEqual(detected.reason, "loading")
                    await page.set_content(fixture.replace('</div></main>', '<div data-subscription-card>Active subscription</div></div></main>'))
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=120, poll_interval=0.01)
                        self.assertNotEqual(detected.status, SubscriptionStatus.NO_SUBSCRIPTION)
                    await page.set_content('<main><h1>Email preferences</h1><p>Manage email notifications</p></main>')
                    from app.sites.google_one.discover import ONE_SETTINGS_URL
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, _ = await diagnose_page(page, 0, timeout_ms=200, source_url=ONE_SETTINGS_URL)
                        self.assertEqual(detected.status, SubscriptionStatus.NOT_APPLICABLE)
                    composite_play_html = '''<main>
                        <h2>Google AI Pro (5 TB) + YouTube Premium Individual Add On</h2>
                        <p>Next payment: $11.99 on Dec 11, 2026</p>
                    </main>'''
                    await page.set_content(composite_play_html)
                    with patch.object(page, "goto", new=AsyncMock(return_value=None)):
                        detected, rec_comp = await diagnose_page(page, 1, timeout_ms=200)
                        self.assertEqual(detected.status, SubscriptionStatus.ACTIVE_UNKNOWN)
                        self.assertIsNone(detected.renewal_date)
                        self.assertIsNone(detected.months_remaining)
                        self.assertEqual(detected.bundle_next_payment_date.isoformat(), "2026-12-11")
                        self.assertEqual(detected.bundle_next_payment_price, "$11.99")
                        self.assertEqual(rec_comp.status, ReconciliationStatus.INDETERMINATE)
                        self.assertIsNone(rec_comp.detected_layer)
                    await context.close()
                finally:
                    await browser.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
