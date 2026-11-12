# Subscription diagnosis and evidence model

The diagnostic reads visible content from the Google Play subscription page and, when applicable, a Google One plan/settings page. It does not run account-login automation. The profile must already exist locally; otherwise the result remains an authentication or evidence failure.

## Result fields

- `status`: the detected source state, including `no_subscription`, `active_unknown_date`, `active_known_duration`, `active_known_date`, `not_applicable`, `detection_failed`, or `inconsistent`.
- `source` and `certainty`: which source supplied the evidence and how strong the classification is.
- `subscription_details`: structured primary-service, add-on, bundle-payment, plan-change, and promotion fields.
- `reconciliation`: comparison against the local cursor; the diagnostic does not change the cursor.
- `sources[].read_events`: sanitized navigation and extraction stages.
- `network_evidence`: destination category, method, response class, and counts; it omits full URLs, query strings, headers, cookies, and bodies.
- `semantic_evidence`: counts and fixed indicators, not raw page content.

The result is persisted as a local JSON diagnostic report under the configured data directory. The report is private local data; do not commit or share it without inspection.

## Network and browser boundary

The reader permits HTTPS `GET` and `HEAD` requests to a limited set of Google-owned hosts. It blocks other methods, payment hosts, recognized checkout or billing destinations, and untrusted hosts. TLS verification stays enabled, Chromium sandboxing stays enabled, and service workers are blocked. The diagnostic does not install a request-rewriting hook or capture a screenshot.

This is an application-level request filter, not an OS network sandbox. A permitted HTTP read is not a proof that the remote server has no side effects. Unknown or blocked data requests keep the result unverified; the application does not replay them.

## Attribution rules

Primary-service renewal, promotion duration, package next payment, and add-on billing are separate observations. A bundle date is never copied into an individual primary-service renewal field. A user-attested visual duration uses the `user_confirmed_visual_evidence` source label and is not automatically extracted evidence. An explicit pending plan change remains separate from local operation state.

## Test coverage

Synthetic regression tests cover explicit empty and active states, delayed rendering, authentication-required pages, ambiguous pages, failed resources, contradictory sources, bundle/add-on date separation, plan changes, and cursor preservation. Browser tests use local HTML in Microsoft Edge with networking disabled. No live Google transaction is part of the test procedure.
