# Read-only diagnostic itinerary

This build has no subscription worker or transaction queue. Its complete browser itinerary is:

1. Load the selected local browser profile.
2. Read the visible Google Play subscriptions page using the bounded diagnostic reader.
3. Read the configured Google One plan/settings page when applicable.
4. Inspect only verified official detail links within the query and time limits.
5. Reconcile the evidence with the stored local cursor and persist a sanitized report.
6. Stop with a result such as `finished`, `authentication_required`, `detection_failed`, or `inconsistent`.

The diagnosis does not click subscription controls, submit forms, modify the cursor, or schedule a follow-up operation. No `Run`, `Run All`, cancellation, card, or checkout route is registered in this distribution.

## Checkpoint behavior

The itinerary checkpoint is separate from the account cursor. It stores a bounded list of hashed route keys, query progress, and fixed result fields. It does not store page text, credentials, cookies, request bodies, or full URLs. Repeated links are deduplicated, and the planner stops when its visit or time limit is reached.

An interrupted or timed-out diagnostic does not advance the cursor. A prior checkpoint never grants permission for an operation; this build does not implement operations.

## Evidence limits

An active subscription without an individual renewal date remains `active_unknown_date`. A bundle payment date is kept separate from the primary service. An explicit duration and a user-attested duration are distinct fields and neither is converted into an exact date without evidence. Pending or contradictory plan-change text remains unresolved.

Offline tests use synthetic pages and mocked observations. They do not establish behavior for a live Google account or every localized version of the site.
