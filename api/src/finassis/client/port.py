"""FinassisClient port: everything a channel (Telegram, later Zalo) asks the core to do.

Two implementations: InProcessClient (now) and HttpClient (generated from OpenAPI, at split time).
Methods mirror public API operations one-to-one so both stay equivalent (tech/07)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UserRef:
    user_id: uuid.UUID
    locale: str
    default_currency: str
    timezone: str
    display_name: str | None = None


class FinassisClient(Protocol):
    # identity (privileged)
    async def find_user_by_identity(self, provider: str, provider_id: str) -> UserRef | None: ...
    async def register_user(self, provider: str, provider_id: str, display_name: str | None, locale: str) -> UserRef: ...
    async def redeem_link_code(self, code: str, provider: str, provider_id: str, display_name: str | None) -> UserRef: ...

    # profile
    async def get_me(self, user: UserRef) -> dict[str, Any]: ...
    async def update_me(self, user: UserRef, **fields: Any) -> UserRef: ...

    # keys
    async def list_keys(self, user: UserRef) -> list[dict[str, Any]]: ...
    async def create_key(self, user: UserRef, name: str, scopes: list[str]) -> tuple[dict[str, Any], str]: ...
    async def revoke_key(self, user: UserRef, key_id: uuid.UUID) -> None: ...

    # ledger & reports
    async def balances(self, user: UserRef) -> list[dict[str, Any]]: ...
    async def spend(self, user: UserRef, period: str) -> dict[str, Any]: ...

    # interactions
    async def list_interactions(self, user: UserRef) -> list[dict[str, Any]]: ...
    async def resolve_interaction(self, user: UserRef, interaction_id: uuid.UUID, option_key: str | None, text: str | None) -> dict[str, Any]: ...
    async def dismiss_interaction(self, user: UserRef, interaction_id: uuid.UUID) -> dict[str, Any]: ...
