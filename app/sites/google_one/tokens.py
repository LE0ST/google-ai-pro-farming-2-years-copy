"""Harvested offer tokens. Data, not code: when one rots, replace one line.

Captured from a build dated 2026-11 and universal across accounts/currencies.
They do not survive forever — a total that refuses to reach zero across several
attempts while the hook is confirmed installed means the token, not the flow.
"""

from __future__ import annotations

PREFIX = "subs:com.google.android.apps.subscriptions.red:"

PRO_12M = (
    "AVbCH/cspB8VdqKJ+ax/uPxUFAVuYRPX4iSjtg4Fu0PG9zUeURSM7y//LlQva1dCd/"
    "rbKVM6EJmyiG/C9BoQ95pMl8yDCHuiwCvM5CuGJME="
)
PRO_6M_SS = (
    "AVbCH/cgl4zvPT2Lqw95Gn+RYAJIVwFwi783i6uVprX7u3MCa5J4CcTXH5RABa5WjBrUoQnwf"
    "8vI10TNV7ktd26myIdxqIwGmNoJ5ofYNqpQ10RKoSvHs75wsfA="
)
PRO_6M = (
    "AVbCH/fUON3w8hpfeutprA3BMQwrBwYIB6Y8CSWi1isDCDswKUUhGQ3dluj5WXsNKOvr8K5"
    "UULBGrRsf4KlW1rHLz0fF3SvxQhBFRbBOHCA="
)
PRO_6M_ALT = (
    "AVbCH/cJ5SeHgkVRt+bdcrapx/aLSu2kbsBgMdI413zbllDNsNQoC/zVlG/Npbu55WlMzBwWP"
    "hUedIvZFDg7fw0CmCv/8DKj04IZ21pK7mrV/hh5"
)
YT_FREE = (
    "AVbCH/eW4Nfp8KBl0zlm7x9PhaiCvpnqwUuL2D8NFPBQgmM9+HuYS1h2xH5lLWU/BsBj775LK5"
    "GqK8UHKqoSzhDDz+PqP3paNesqLU0r9i6XCrYa"
)

# layer -> (token, subscription mode)
LAYERS = {
    1: (PRO_12M, 5),
    2: (PRO_6M_SS, 5),
    3: (PRO_6M, 5),
    4: (PRO_6M_ALT, 5),
}


def full_items(token: str) -> list:
    """The line items swapped into the offer payload. Shape is positional."""
    return [
        [[PREFIX + "g1.2tb.ai.subs_variant", 21], None, None, None, 1, [token]],
        [[PREFIX + "g1.youtube_premium_individual_add_on", 21], None, None, None, 1, [YT_FREE]],
    ]


def config(layer: int, sub_id: str | None) -> dict:
    token, mode = LAYERS[layer]
    return {
        "name": f"layer{layer}",
        "full_items": full_items(token),
        "ear8": [None, mode, None, None, None, [sub_id]] if sub_id else None,
    }
