# Plan-change diagnosis

The diagnostic reports plan-change evidence separately from subscription status and GaiConsole's operation journal:

- `plan_change_pending`: the page contains an explicit pending or ambiguous change signal.
- `operation.pending_operation`: GaiConsole has an unresolved submission record.
- `primary_renewal_unknown`: no explicit renewal date for the primary service was established.

A pending plan change does not create a local operation record. An empty operation journal does not prove that a provider-side change completed.

## Read-only evidence

The parser extracts explicit change clauses and reports status, named service scope, effective date, and resulting plan only when those details appear in the evidence. Source and confidence are retained. A nearby bundle payment date is not treated as a change date or the primary service renewal date. Conflicting pending and completed signals remain unresolved.

The diagnostic itinerary follows only verified read-only detail links within its query and time budgets. Payment, checkout, and cancellation destinations are blocked. It does not replay unknown requests or perform a login flow.

## Dashboard counters

`completed_queries` counts additional detail-view visits, not baseline page navigations. `checks_performed` summarizes baseline sources, link inventory, and reconciliation. `link_search_result` distinguishes no verified links, already visited views, available links, and an exhausted budget.

## Offline verification

`tests/test_plan_changes.py` uses synthetic page text and mocked navigation for pending, completed, unknown, and conflicting examples. These tests do not describe any real account or establish live provider behavior.
