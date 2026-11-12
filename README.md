# GaiConsole

GaiConsole is a local FastAPI dashboard for managing account records, inspecting Google subscription state, and running a guarded browser workflow. This fork adds bounded subscription diagnosis, a read-only itinerary planner, durable operation journaling, visual evidence tracking, persistence safeguards, and offline tests.

This repository derives from [`ade1256/google-ai-pro-farming-2-years`](https://github.com/ade1256/google-ai-pro-farming-2-years) and the `danskiv/google-ai-pro-farming-2-years-copy` fork. The MIT license and its original copyright notice are retained in [`LICENSE`](LICENSE). GaiConsole is independent and is not endorsed by Google or the upstream authors.

## Capabilities

- Local authenticated dashboard for account and payment-method records, queue status, logs, TOTP display, diagnosis, itinerary, and operation history.
- OS-backed or file-backed secret storage where available; browser profiles, SQLite, logs, artifacts, and the console token live in an external per-user data directory.
- Read-only subscription diagnosis from Google Play and Google One, including evidence provenance, primary-plan versus bundle billing distinctions, and explicit unknown or conflicting states.
- Bounded read-only itinerary continuation over verified official detail links, with a separate resumable checkpoint.
- Durable operation journal that distinguishes intent, pending verification, and independently confirmed completion. Uncertain submissions block automatic repeats.
- Guarded browser workflow for a provider-rendered initial offer when no active subscription is detected. It uses the visible provider UI and checks the displayed price and term before submission. It does not intercept checkout requests, replace offer tokens, resubscribe, or stack offers.
- Existing user-directed cancellation and payment-method removal workflows. These are real account mutations and require a user-owned account and payment method.

The application is **not read-only**. Diagnosis and itinerary reads do not authorize a later subscription, cancellation, or payment-method change. No offer, eligibility, price, duration, or zero-cost outcome is guaranteed.

## Requirements

- Python 3.12 or newer.
- Windows 10/11, macOS, or Linux.
- Chrome/Chromium or the Playwright-managed Chromium browser.
- A graphical session for the live browser workflow. Linux servers without a display need Xvfb for headed browser operation; headless login may be rejected by the identity provider.
- Dependencies are hash-pinned in [`requirements.lock`](requirements.lock); top-level constraints are in [`pyproject.toml`](pyproject.toml).

## Installation and launch

### Windows PowerShell

```powershell
.\run.ps1 --check
.\run.ps1
```

The wrapper creates `.venv`, installs locked dependencies, and installs Playwright Chromium if it cannot find system Chrome/Chromium. Manual setup:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --no-input -r requirements.lock
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

### Linux and macOS

```bash
./run.sh --check
./run.sh
```

The wrapper creates `.venv`, installs locked dependencies, and installs Playwright Chromium if it cannot find a system browser. Ubuntu may require Python 3.12 and its venv package. Manual setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes --no-input -r requirements.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

The dashboard defaults to `http://127.0.0.1:8765`. Startup generates a console token in the data directory and prints a tokenized local URL. Treat that URL and startup log as secrets. Keep the bind address on loopback; do not expose the console directly to a network.

## Configuration and local data

Environment variables use the `GAI_` prefix. Common settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GAI_DATA_DIR` | OS-specific per-user `GaiConsole` directory | Database, vault, token, browser profiles, logs, and artifacts. Keep outside the checkout. |
| `GAI_BIND_HOST` | `127.0.0.1` | Local web server interface. |
| `GAI_BIND_PORT` | `8765` | Local web server port. |
| `GAI_SECRET_BACKEND` | `auto` | `auto`, `security` (macOS Keychain), `secret-tool` (Linux Secret Service), or `file`. |
| `GAI_BROWSER_CHANNEL` | `auto` | Browser selection: `auto`, `chrome`, `chromium`, or `bundled`. |
| `GAI_BROWSER_EXECUTABLE` | empty | Explicit browser executable path. |
| `GAI_HEADLESS` | `auto` | `auto`, `1`, or `0`. Identity-provider login may require headed mode. |
| `GAI_RUN_TIMEOUT` | 900 seconds | Maximum duration of one worker run. |

Default data directories are `%LOCALAPPDATA%\\GaiConsole` on Windows, `~/.local/share/GaiConsole` on Linux (or `$XDG_DATA_HOME/GaiConsole`), and `~/Library/Application Support/GaiConsole` on macOS. On Windows, file-backed secrets rely on the user profile ACL. On Linux/macOS, the fallback vault file is permission-restricted; an OS keyring is preferable when available.

Never commit or share the data directory. It can contain account credentials, TOTP seeds, payment data, session cookies, screenshots, and diagnostic logs. `.gitignore` excludes common local artifacts but does not replace reviewing commit contents.

## Architecture

| Path | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI app, authenticated dashboard/API, account import, diagnosis and queue endpoints. |
| `app/security.py` | Console-token authentication and same-origin protection for POST operations. |
| `app/vault.py`, `app/secrets.py`, `app/db.py` | Secret references, OS/file secret backends, and SQLite persistence. |
| `app/runner/queue.py` | Serial subprocess queue, stop/timeout handling, run logs, and terminal state. |
| `app/runner/diagnosis.py` | Bounded, read-only subscription diagnosis and sanitized evidence. |
| `app/runner/itinerary.py` | Bounded, deduplicated read-only detail queries and resumable checkpoints. |
| `app/runner/operations.py`, `app/runner/outcomes.py` | Durable operation journal and explicit uncertain/verification outcomes. |
| `app/runner/visual_evidence.py` | Separate storage for user-attested visual observations. |
| `app/sites/google_one/discover.py` | Visible evidence parsing, service separation, dates/prices, and local cursor comparison. |
| `app/sites/google_one/guard.py` | Fail-closed visible price guard for the initial offer workflow. |
| `tasks/subscribe_flow.py` | Provider-rendered initial offer flow and user-directed cancellation/card-removal operations. |
| `app/templates/index.html` | Local dashboard. |
| `docs/` | Diagnosis, itinerary, plan-change evidence, and visual-evidence behavior. |

## How it works

The web app stores account and payment-method records locally and runs one browser worker at a time. Credentials and payment details are stored through the configured secret backend. Browser profiles persist outside the repository so a user can complete identity prompts in the browser.

Diagnosis reads official pages and reports source, confidence, renewal or bundle dates, and unresolved discrepancies. If evidence remains indeterminate, the itinerary planner can inspect a small number of verified official detail links. Its checkpoint stores route hashes and query progress, not page contents, credentials, or cookies. Diagnosis and itinerary results do not authorize account mutations.

The operational worker only handles an initial offer rendered by Google's own page and only when no active subscription is detected. It checks visible price and term evidence, records a submission intent before pressing the provider's visible control, then requires independent state verification. A failed or ambiguous check stops the workflow. After successful verification, the flow can cancel renewal and attempt payment-method removal. Those actions change real account state.

## Using the dashboard

1. Start the console and open the tokenized loopback URL printed at startup. Keep the token private.
2. Add only accounts and payment methods you are authorized to manage. Import formats are one record per line: `email|password|optional-TOTP-seed` for accounts and `card-number|MM/YY|CVC` for payment methods. These values are stored outside the repository through the configured secret backend.
3. Use **Diagnose Subscription** to collect read-only evidence. Unknown, incomplete, or conflicting states remain unresolved.
4. Use **Run** only when you intend a real operation. The worker stops for active or ambiguous subscriptions. On an eligible account it may submit only the initial offer visible in the provider interface, and only after its visible guards pass. The provider may require a payment method or human identity verification.
5. After a verified signup, the worker may cancel renewal and remove the associated payment method. Check the resulting subscription and payment state directly with the provider. Use **Stop** to stop the current worker; an interrupted submission may remain uncertain and will require review.
6. Treat logs, screenshots, browser profiles, and diagnostic artifacts as private. They are kept under the external data directory.

## Tests and startup checks

Run from the repository root after installing dependencies:

```powershell
.\.venv\Scripts\python.exe tests\selftest.py
.\.venv\Scripts\python.exe tests\test_diagnosis.py
.\.venv\Scripts\python.exe tests\test_itinerary.py
.\.venv\Scripts\python.exe tests\test_operation_progress.py
.\.venv\Scripts\python.exe tests\test_plan_changes.py
.\.venv\Scripts\python.exe tests\test_visual_evidence.py
.\.venv\Scripts\python.exe tests\test_diagnosis_browser.py
.\.venv\Scripts\python.exe -m compileall -q app tasks tests
```

Linux/macOS use `.venv/bin/python` instead of the Windows interpreter path. `run.ps1 --check` and `run.sh --check` inspect the Python runtime, data directory, secret backend, and browser availability without starting the server. Offline tests use synthetic data and local fixtures; they do not validate live account state or current commercial terms.

## Security, privacy, and real operations

- Use only accounts and payment methods you own or are explicitly authorized to manage.
- Review every displayed term before any real subscription action. A visible guard can fail on changed, incomplete, localized, or contradictory UI text; it cannot guarantee future charges or provider eligibility.
- Cancellation and payment-method removal are external mutations. Verify the resulting state directly with the provider.
- Browser profiles contain live cookies. Screenshots, user-attested evidence, and logs may contain private account information.
- Console access is protected by a local token, but processes running as the same user may access local files. Do not bind to a public interface or share the tokenized URL.
- Do not run tests against production credentials or a personal browser profile. Tests use disposable temporary data and synthetic fixtures.
- The license is MIT. Preserve the upstream notice in source redistributions. The locked runtime dependency licenses are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); preserve required notices when redistributing built artifacts.

## Limitations

- Google pages, selectors, authentication flows, promotional terms, and localization change over time.
- Diagnosis is based on visible and network metadata; missing or contradictory evidence remains unknown rather than guessed.
- Bundle payment dates are not assumed to be the primary service's renewal date.
- A user-attested visual duration is not automatic evidence and does not establish eligibility or an exact future bill.
- The browser workflow is limited to the provider-rendered initial offer. It does not automate plan changes, resubscription, or offer stacking.
- This is experimental software, not a billing, eligibility, or account-state guarantee.
