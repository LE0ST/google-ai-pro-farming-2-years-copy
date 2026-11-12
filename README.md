# GaiConsole — Read-only Diagnostics

GaiConsole Read-only Diagnostics is a local FastAPI and Playwright application for inspecting visible subscription information on official Google Play and Google One pages. It records a sanitized diagnostic report and compares observations with a local, read-only cursor.

This is a safety-limited distribution derived from [`ade1256/google-ai-pro-farming-2-years`](https://github.com/ade1256/google-ai-pro-farming-2-years) and the [`danskiv` fork](https://github.com/danskiv/google-ai-pro-farming-2-years-copy). The original MIT license and copyright notice remain in [`LICENSE`](LICENSE). This project is independent and is not affiliated with or endorsed by Google or the upstream authors.

## Safety boundary

This branch is designed for read-only diagnostics. The public application has no account-login automation, password or TOTP vault, card-management UI, queue worker, checkout module, request-rewriting hook, subscription submission, or cancellation worker. Its POST routes are limited to creating a local display label and starting a diagnostic. The browser policy permits only HTTPS `GET` and `HEAD` requests to allowlisted Google domains, and blocks payment hosts and sensitive destinations. TLS verification and Chromium sandboxing remain enabled. Automated tests verify the route surface and the absence of transaction modules.

The diagnostic does not sign in. It can inspect only an existing local browser profile; if the profile is unauthenticated or unavailable, the result remains an authentication or evidence failure. This application does not import credentials, cookies, or browser profiles. A browser profile can contain session cookies and must be treated as sensitive. Screenshots are disabled in this distribution; the saved report contains structured signals and sanitized network metadata only.

An HTTP `GET` is not proof that a remote server has no side effects. The application reports unknown or ambiguous evidence as indeterminate and never treats a local cursor as proof of subscription duration. It does not guarantee eligibility, a promotion, a zero price, or a billing date.

## Capabilities

- Local dashboard bound to `127.0.0.1` with token and same-origin protections.
- Non-secret display labels for local browser profiles.
- Read-only Playwright diagnosis of visible Google Play and Google One subscription information.
- Separation of the primary service, add-ons, bundle payment observations, promotional duration, and individual renewal date.
- Bounded, deduplicated reads of verified official detail links.
- A sanitized, persisted JSON report with source status, confidence, evidence categories, timings, and reconciliation outcome.
- Local display of user-attested visual duration evidence, clearly separate from automatic extraction.
- Synthetic offline tests for rendering, network policy, cursor preservation, plan-change parsing, and report isolation.

This branch does not provide subscription execution, payment entry, cancellation, renewal, request rewriting, account password storage, or TOTP generation.

## Architecture

| Path | Purpose |
| --- | --- |
| [`app/main.py`](app/main.py) | Read-only FastAPI routes, local display-label creation, dashboard, and diagnostic report APIs. |
| [`app/runner/diagnosis.py`](app/runner/diagnosis.py) | Sandboxed Playwright reader, HTTPS read-method filter, semantic evidence, and sanitized report persistence. |
| [`app/runner/itinerary.py`](app/runner/itinerary.py) | Bounded and deduplicated read-only detail-page queries. |
| [`app/runner/browser.py`](app/runner/browser.py) | Browser selection only; no stealth, proxy, certificate, or launch-flag overrides. |
| [`app/runner/visual_evidence.py`](app/runner/visual_evidence.py) | Account-scoped user-attested duration metadata, stored separately from automatic observations. |
| [`app/sites/google_one/discover.py`](app/sites/google_one/discover.py) | Visible-text parsing, service separation, date attribution, and read-only reconciliation. |
| [`app/db.py`](app/db.py) | Minimal local schema for display labels and read-only cursor metadata. It refuses legacy transaction-capable schema versions. |
| [`app/security.py`](app/security.py) | Local console token and same-origin checks. |
| [`app/templates/index.html`](app/templates/index.html) | Diagnostic-only dashboard; no run, payment, or cancellation controls. |
| [`tests/`](tests/) | Synthetic parser, network-policy, route-surface, itinerary, and local Edge tests. |

## Diagnostic states

The HTTP runner reports `not_started`, `running`, `finished`, or `failed`. The report's separate subscription state may include `no_subscription`, `active_unknown_date`, `active_known_duration`, `active_known_date`, `not_applicable`, `detection_failed`, or `inconsistent`. These describe observed evidence only; none starts an operation.

Reconciliation preserves the local cursor. A bundle next-payment date is not automatically attributed to the primary service. User-attested visual duration is labelled `user_confirmed_visual_evidence`; it is not presented as an automatically extracted fact or an exact renewal date.

## Requirements and installation

- Python 3.12 or newer.
- The hash-pinned packages in [`requirements.lock`](requirements.lock).
- Playwright-managed Chromium or a supported Chrome/Edge installation.
- A new, private data directory. The default directory name is `GaiConsole-ReadOnly`, separate from legacy GaiConsole data.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-input -r requirements.lock
.\.venv\Scripts\python.exe -m playwright install chromium
$env:GAI_DATA_DIR = Join-Path $env:LOCALAPPDATA 'GaiConsole-ReadOnly'
$env:GAI_BIND_PORT = '8765'
.\run.ps1
```

Linux or macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --no-input -r requirements.lock
.venv/bin/python -m playwright install chromium
export GAI_DATA_DIR="$HOME/.local/share/GaiConsole-ReadOnly"
export GAI_BIND_PORT=8765
./run.sh
```

The wrappers also create a virtual environment and install the locked dependencies when needed. Browser installation downloads a browser binary. Review those steps for your environment. Both wrappers bind Uvicorn to `127.0.0.1`; they do not support public binding.

Open `http://127.0.0.1:8765/` with the local token from the `console.token` file in `GAI_DATA_DIR` (or the platform's default data directory). Enter the token locally as the `t` query parameter once; the console exchanges it for an HTTP-only, same-site cookie and redirects to a clean URL. Never paste the token into chat, logs, screenshots, or Git.

The app stores its SQLite metadata, diagnostic JSON reports, itinerary checkpoints, optional visual attestations, and browser profiles under `GAI_DATA_DIR`. Use a new empty directory. The database migration deliberately rejects the legacy schema version used by the transaction-capable application. Do not point this build at a legacy data directory.

The local profile form stores only a display label. It does not perform sign-in or create an authenticated browser session. No instructions for account login are included; an unauthenticated profile will return an authentication-required or insufficient-evidence result.

## Offline tests

Run from the repository root:

```powershell
$python = ".\.venv\Scripts\python.exe"
& $python tests/test_diagnosis.py
& $python tests/test_diagnosis_browser.py
& $python tests/test_itinerary.py
& $python tests/test_plan_changes.py
& $python tests/test_visual_evidence.py
& $python -m compileall -q app tests
git diff --check
```

The test data is synthetic. Browser tests use a local HTML fixture in Microsoft Edge with networking disabled and sandboxing enabled. They do not sign in to Google, access an account, or perform billing operations.

## Privacy and data handling

- Keep `GAI_DATA_DIR` outside the repository. It can contain browser profiles and session cookies.
- The persisted diagnostic report contains structured page classifications and sanitized event metadata, not page HTML, cookies, credentials, request bodies, or full URLs.
- A screenshot is not captured by the public application.
- The console token is stored outside the repository and is not logged by this branch.
- `.gitignore` excludes data directories, browser profiles, databases, logs, local environment files, and token/vault files.
- The MIT license requires retaining the upstream copyright and permission notice.

## Technical notes and limitations

- [Subscription diagnosis and evidence model](docs/SUBSCRIPTION_DIAGNOSIS.md)
- [Read-only itinerary](docs/EXECUTION_ITINERARY.md)
- [Plan-change interpretation](docs/PLAN_CHANGE_DIAGNOSIS.md)
- [Synthetic visual-evidence format](docs/VISUAL_VERIFICATION.md)

Google page content and localization can change. Some pages require an authenticated profile, and Google may restrict browser contexts. Missing, contradictory, or partially rendered evidence remains indeterminate. Plan-change scope, exact individual renewal dates, and future billing cannot be inferred from bundle-level amounts. No live-account compatibility claim is made by the offline test results.
