"""Worker terminal outcomes shared with the queue; no automation imports."""
from enum import Enum


class RunOutcome(Enum):
    ACTIVE_NEEDS_VERIFICATION = "active_needs_verification"
    NEEDS_VERIFICATION = "needs_verification"

    def __bool__(self) -> bool:
        """Neither observation is a completed automation run."""
        return False


ACTIVE_NEEDS_VERIFICATION_EXIT = 20
NEEDS_VERIFICATION_EXIT = 21
VERIFICATION_MESSAGE = "Subscription evidence is incomplete or contradictory; no further operations performed; cursor preserved."
ACTIVE_VERIFICATION_MESSAGE = (
    "Active subscription confirmed by both official sources; promotional stage is unverified. "
    "Missing individual Google AI Pro renewal date. Next: verify_primary_renewal using official read-only evidence. "
    "Subscription changes blocked; cursor preserved."
)
