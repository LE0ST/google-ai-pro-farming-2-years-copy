# Read-only itinerary and operation recovery

The itinerary planner runs only when subscription evidence is indeterminate. It follows a bounded set of verified official detail links, deduplicates equivalent views, and reconciles fresh evidence with the local cursor. It does not submit forms, authorize a subscription, or change progress.

## Checkpoint

The checkpoint under the external artifacts directory stores a phase, route hashes, next action, and fixed reason codes. It does not store credentials, cookies, page content, or full URLs. A short-lived evidence fingerprint bounds reuse. An interrupted detail read can be retried; cached results do not authorize mutations.

## Operation journal

`operations.py` records `submitted`, `pending_verification`, and `completed` phases. `submitted` records intent before a UI action; it does not prove that the provider accepted it. If verification is missing, the account is blocked from automatic retry. Completion requires separate visible evidence.

## Supported worker behavior

The worker only considers an initial offer rendered by the provider when no active subscription is detected. It does not intercept checkout traffic, replace offer tokens, resubscribe, or stack offers. It requires visible price and term checks before submission and independent verification afterward. An ambiguous or contradictory result stops the run.

After a verified successful signup, the worker can cancel renewal and attempt payment-method removal. These are real account mutations. The user must be authorized to manage the account and should verify the resulting state with the provider.

## Evidence limits

A local account cursor is execution metadata, not proof of a provider state or elapsed term. Bundle next-payment dates are kept separate from the primary service's renewal date. A user-attested promotion duration is not treated as an automatically verified renewal date.

Offline validation uses synthetic observations only. No live account state is documented in this file.
