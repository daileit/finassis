"""InProcessClient: calls domain services through tenant/admin transactions — the same code paths
the REST handlers use, without HTTP."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from ..api.state import AppState
from ..domain import interactions, keys, reports, users
from .port import UserRef


def _ref(u: dict[str, Any]) -> UserRef:
    return UserRef(user_id=u["id"], locale=u["locale"], default_currency=u["default_currency"], timezone=u["timezone"],
                   display_name=u.get("display_name"))


class InProcessClient:
    def __init__(self, st: AppState) -> None:
        self.st = st

    async def find_user_by_identity(self, provider: str, provider_id: str) -> UserRef | None:
        async with self.st.db.admin_conn() as conn:
            u = await users.find_by_identity(conn, provider, provider_id)
        return _ref(u) if u else None

    async def register_user(self, provider: str, provider_id: str, display_name: str | None, locale: str) -> UserRef:
        async with self.st.db.admin_tx() as conn:
            u = await users.create_user(conn, locale=locale, default_currency="VND" if locale == "vi" else "USD", display_name=display_name)
            await users.link_identity(conn, u["id"], provider, provider_id, display_name)
            await interactions.onboarding(conn, u["id"])
        await self.st.meter.record(u["id"], "user.register", 1)
        return _ref(u)

    async def redeem_link_code(self, code: str, provider: str, provider_id: str, display_name: str | None) -> UserRef:
        async with self.st.db.admin_tx() as conn:
            u = await users.redeem_link_code(conn, code, provider, provider_id, display_name)
        return _ref(u)

    async def get_me(self, user: UserRef) -> dict[str, Any]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            u = await users.get_user(conn, user.user_id)
            allowance = await self.st.meter.allowance(conn, user.user_id)
        return {"user": u, "allowance": allowance}

    async def update_me(self, user: UserRef, **fields: Any) -> UserRef:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            u = await users.update_user(conn, user.user_id, **fields)
        return _ref(u)

    async def list_keys(self, user: UserRef) -> list[dict[str, Any]]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await keys.list_keys(conn, user.user_id)

    async def create_key(self, user: UserRef, name: str, scopes: list[str]) -> tuple[dict[str, Any], str]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await keys.create_key(conn, user.user_id, "user", name, scopes)

    async def revoke_key(self, user: UserRef, key_id: uuid.UUID) -> None:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            await keys.revoke_key(conn, user.user_id, key_id)

    async def balances(self, user: UserRef) -> list[dict[str, Any]]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await reports.balances(conn, user.user_id, self.st.units, user.locale)

    async def spend(self, user: UserRef, period: str = "month") -> dict[str, Any]:
        today = date.today()
        start = today.replace(day=1) if period == "month" else today - timedelta(days=today.weekday())
        end = (start + timedelta(days=32)).replace(day=1) if period == "month" else start + timedelta(days=7)
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await reports.rollup_by_tag(conn, user.user_id, self.st.units, user.locale, kind="expense", start=start, end=end)

    async def list_interactions(self, user: UserRef) -> list[dict[str, Any]]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await interactions.list_open(conn, user.user_id)

    async def resolve_interaction(self, user: UserRef, interaction_id: uuid.UUID, option_key: str | None, text: str | None) -> dict[str, Any]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await interactions.resolve(conn, user.user_id, interaction_id, option_key=option_key, text=text, via="telegram")

    async def dismiss_interaction(self, user: UserRef, interaction_id: uuid.UUID) -> dict[str, Any]:
        async with self.st.db.tenant_tx(user.user_id) as conn:
            return await interactions.dismiss(conn, user.user_id, interaction_id, via="telegram")
