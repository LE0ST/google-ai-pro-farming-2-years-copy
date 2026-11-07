"""DTOs. Account secrets are represented by reference, never by value."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AccountStatus(StrEnum):
    pending = "pending"
    running = "running"
    blocked = "blocked"
    subscribed = "subscribed"
    failed = "failed"
    cancelled = "cancelled"


class Step(StrEnum):
    login = "login"
    subscribe_base = "subscribe_base"
    stack_second = "stack_second"
    stack_third = "stack_third"
    cancel_renew = "cancel_renew"


class Account(BaseModel):
    id: str
    email: str
    secret_ref: str
    recovery_email: str = ""
    totp_ref: str = ""
    status: AccountStatus = AccountStatus.pending
    step: int = 0
    step_message: str = ""
    expiry_date: str = ""
    egress_override: str = ""

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not re.match(_EMAIL, v):
            raise ValueError(f"not an email: {v!r}")
        return v.lower()


class AccountCreate(BaseModel):
    email: str
    password: str
    recovery_email: str = ""
    totp_secret: str = ""
    egress_override: str = ""

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not re.match(_EMAIL, v):
            raise ValueError(f"not an email: {v!r}")
        return v.lower()


class CardIn(BaseModel):
    number: str = Field(min_length=12, max_length=19)
    exp_month: str
    exp_year: str
    cvc: str = Field(min_length=3, max_length=4)
    holder: str
    postal_code: str = "97201"
    locality: str = "Portland"
    province: str = "OR"


class CardPublic(BaseModel):
    """What the UI may see: last four only, never the CVC."""
    last4: str
    exp_month: str
    exp_year: str
    holder: str
    postal_code: str
    locality: str
    province: str


class RunRequest(BaseModel):
    account_ids: list[str]
    start_step: int = 1
