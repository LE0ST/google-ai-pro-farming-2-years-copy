# Repository guidance

GaiConsole handles account credentials, browser sessions, payment settings, and subscription operations. Keep development and tests offline and use synthetic account data.

## Security boundaries

- Never add checkout request interception, offer-token substitution, eligibility manipulation, or automation that changes a provider request's price, authorization, or selected commercial offer.
- The browser worker may use only the offer and terms shown by the provider's own interface. Preserve fail-closed price/date checks and independent post-submit verification.
- Do not automate plan changes, resubscription, or offer stacking. Existing active or ambiguous subscriptions must stop for verification.
- Diagnose and itinerary code must remain read-only. Do not treat a diagnostic result as permission to mutate an account.
- Never print, commit, or include account passwords, TOTP seeds, payment data, cookies, browser profiles, data directories, tokens, logs, or private screenshots.
- Do not expose the local console beyond loopback without a separately reviewed authentication and deployment design.

## Development

- Python 3.12+; dependencies are hash-pinned in `requirements.lock`.
- Use synthetic data, temporary directories, and local browser fixtures for tests.
- Preserve the MIT license notice and review dependency licenses before redistribution.
- Document externally visible operations and limitations in English in `README.md`.
