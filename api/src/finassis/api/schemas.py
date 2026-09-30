"""Request/response models. Money on the wire = {amount(int minor), currency, decimals, display}."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Money(BaseModel):
    amount: int
    currency: str
    decimals: int
    display: str


class Quantity(BaseModel):
    value: str
    unit: str
    decimals: int | None = None
    name: str | None = None


class QuantityIn(BaseModel):
    value: str
    unit: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# --- accounts ---------------------------------------------------------------
AccountType = Literal["cash", "bank", "term_deposit", "credit_card", "loan", "brokerage", "crypto", "real_estate",
                      "vehicle", "pension", "private_equity", "receivable", "payable", "collectible", "other"]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: AccountType
    currency: str
    valuation_mode: Literal["ledger", "mark_to_market"] = "ledger"
    is_liability: bool | None = None
    liquidity: Literal["liquid", "semi", "illiquid"] = "liquid"
    purpose: str | None = None
    labels: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    terms: dict[str, Any] | None = None
    institution: str | None = None
    external_ref: str | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    liquidity: Literal["liquid", "semi", "illiquid"] | None = None
    purpose: str | None = None
    labels: list[str] | None = None
    terms: dict[str, Any] | None = None
    institution: str | None = None
    external_ref: str | None = None
    is_archived: bool | None = None


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=40)


# --- tags -------------------------------------------------------------------
class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    root: str = Field(description="system_key of the root, e.g. 'food'")


class TagPatch(BaseModel):
    name: str | None = None
    is_hidden: bool | None = None
    display_name_override: str | None = None


# --- transactions -----------------------------------------------------------
class PostingIn(BaseModel):
    account: str | None = None
    tag: str | None = None
    amount: int
    currency: str


class TransactionSimple(BaseModel):
    account: str
    amount: int = Field(description="signed, integer minor units; negative leaves the account")
    currency: str | None = None
    occurred_at: datetime | date | str
    description: str | None = Field(default=None, max_length=500)
    tag: str | None = None
    counter_account: str | None = None
    transfer_tag: str | None = Field(default=None, description="off_report child for transfers; default self_transfer")
    quantity: QuantityIn | None = None
    instrument: str | None = None
    labels: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _excl(self) -> TransactionSimple:
        if self.tag and self.counter_account:
            raise ValueError("tag and counter_account are mutually exclusive")
        return self


class TransactionExplicit(BaseModel):
    occurred_at: datetime | date | str
    description: str | None = Field(default=None, max_length=500)
    postings: list[PostingIn] = Field(min_length=2)
    labels: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class SetTag(BaseModel):
    tag: str
    pin: bool = False


class Reverse(BaseModel):
    reason: str | None = None


# --- annotations ------------------------------------------------------------
class AnnotationIn(BaseModel):
    target_type: Literal["profile", "account", "valuation", "instrument", "tag", "period", "transaction"]
    target_id: str | None = None
    body: str = Field(min_length=1, max_length=4000)
    author_name: str | None = None
    tags: list[str] = Field(default_factory=list)
    valid_until: datetime | None = None


# --- interactions -----------------------------------------------------------
class ResolveIn(BaseModel):
    option_key: str | None = None
    text: str | None = None


# --- keys / identities / me -------------------------------------------------
class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(default_factory=lambda: ["ledger:read", "ledger:write", "reports:read"])


class LinkCodeIn(BaseModel):
    provider: Literal["telegram", "google", "email"] = "telegram"


class MePatch(BaseModel):
    locale: str | None = None
    default_currency: str | None = None
    timezone: str | None = None
    display_name: str | None = None
    personality_profile: dict[str, Any] | None = None


# --- admin ------------------------------------------------------------------
class AdminUserCreate(BaseModel):
    locale: str = "en"
    default_currency: str = "USD"
    timezone: str = "Asia/Ho_Chi_Minh"
    display_name: str | None = None
    telegram_id: str | None = None


class GrantIn(BaseModel):
    kind: Literal["donation", "promo", "admin", "referral"] = "admin"
    allowance: dict[str, float]
    note: str | None = None
    expires_at: datetime | None = None


class Ok(BaseModel):
    model_config = ConfigDict(extra="allow")
    ok: bool = True


def tx_id(v: uuid.UUID | str) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(v)
