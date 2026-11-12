# Visual-evidence metadata

The dashboard can display an optional user-attested duration record stored in the local artifacts directory. The record is separate from automatic page extraction and is not used to authorize or trigger an action.

Synthetic example:

```json
{
  "confirmed_promo_months": 12,
  "source": "user_confirmed_visual_evidence"
}
```

The application does not capture screenshots in this public build. It stores no image, authenticated HTML, page text, email, cookie, or payment detail as part of this evidence record. A manual attestation does not establish an exact promotion end date or renewal date.

Automated tests use fictitious account identifiers and temporary directories to verify account scoping, validation, and separation from automatically observed evidence. No actual account observation is included in the public documentation.
