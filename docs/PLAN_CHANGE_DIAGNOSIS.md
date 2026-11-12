# Plan-change diagnosis

The parser reports plan-change text as an observation, separate from the subscription state and local cursor. It extracts only recognized clauses and records:

- `status`: `pending`, `completed`, or `unknown`;
- `scope`: a named primary service, add-on, or `unknown`;
- `change_type`: a recognized addition/removal or `unknown`;
- `effective_date`: only when the text explicitly supplies one;
- `resulting_plan`: only when the text explicitly names it.

Examples in automated tests are synthetic, such as a pending add-on removal with an explicitly stated effective date. The date is associated with that change clause only; it is not used as the primary service renewal date. If two sources disagree, the result remains inconsistent or indeterminate.

This public build only reports the observation. It has no operation journal, resubmission, cancellation, or plan-change execution path. A pending or unknown change never triggers a follow-up mutation.

The diagnostic itinerary reports whether additional verified read-only details were available, how many bounded queries completed, and why evidence remains incomplete. A zero additional-query count does not mean the initial source pages were skipped.
