# Subscription diagnosis

The dashboard diagnosis observes subscription state using the selected account's existing browser profile. It does not submit the subscription worker, click subscription controls, or write progress. Its request filter blocks POST requests and recognized checkout, purchase, payment, billing, and cancellation destinations. Network filtering is an application-level measure, not an operating-system sandbox or proof that every permitted GET is free of server-side effects.

## Evidence model

The detector classifies explicit visible and accessible evidence as active, no subscription, inconsistent, or unknown. A page heading alone does not establish an empty state. Loading indicators and contradictory sources remain unresolved. The dashboard reports the source, confidence, and reason for each classification.

For composite subscriptions, the detector separates the primary service, add-ons, bundle next-payment date/price, and primary-service renewal date. A bundle payment date is not assigned to the primary service without explicit evidence. Promotion duration, renewal date, and current price are distinct fields.

## Itinerary continuation

When baseline evidence is indeterminate, the planner may follow a small number of verified official detail links. It deduplicates views and records a bounded checkpoint outside the repository. No request is replayed to infer unknown semantics. See [Execution itinerary](EXECUTION_ITINERARY.md).

## Verification and limitations

Offline tests use synthetic accessible messages, mocked responses, and local browser fixtures. They cover incomplete pages, conflicting sources, sanitized network evidence, and account isolation. They do not establish behavior on live accounts or current provider page layouts.

An unknown application request remains blocked. The diagnostic cannot prove eligibility, promotional terms, future charges, or that every visible account state is complete. A diagnostic result never authorizes a transaction.
