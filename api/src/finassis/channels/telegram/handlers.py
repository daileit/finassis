"""Telegram command/callback handlers. Stateless: everything lives in interactions/identities/users.
Depends only on the FinassisClient port, never on domain modules directly."""

from __future__ import annotations

import html
import uuid
from typing import Any

from ...client.port import FinassisClient, UserRef
from ...errors import AppError
from ...i18n import t
from .tg import TelegramAPI

PROVIDER = "telegram"
DEFAULT_SCOPES = ["ledger:read", "ledger:write", "accounts:write", "tags:write", "reports:read", "annotations:write", "interactions:write"]


class Handlers:
    def __init__(self, client: FinassisClient, api: TelegramAPI) -> None:
        self.client = client
        self.api = api

    # ---- entry -------------------------------------------------------------------------------
    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._callback(update["callback_query"])
            return
        msg = update.get("message") or update.get("edited_message")
        if not msg or "text" not in msg:
            return
        chat_id = msg["chat"]["id"]
        from_ = msg.get("from") or {}
        provider_id = str(from_.get("id") or chat_id)
        display = " ".join(x for x in (from_.get("first_name"), from_.get("last_name")) if x) or from_.get("username")
        lang = (from_.get("language_code") or "en")[:2]
        text: str = msg["text"].strip()

        user = await self.client.find_user_by_identity(PROVIDER, provider_id)
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()

        try:
            if cmd == "/start":
                await self._start(chat_id, user, provider_id, display, lang)
            elif cmd == "/link":
                await self._link(chat_id, provider_id, display, arg.strip(), lang)
            elif user is None:
                await self.api.send(chat_id, t(lang, "telegram.not_registered"))
            elif cmd == "/help":
                await self.api.send(chat_id, t(user.locale, "telegram.help"))
            elif cmd == "/keys":
                await self._keys(chat_id, user, arg.strip())
            elif cmd == "/lang":
                await self._lang(chat_id, user, arg.strip())
            elif cmd == "/currency":
                await self._currency(chat_id, user, arg.strip())
            elif cmd == "/balance":
                await self._balance(chat_id, user)
            elif cmd == "/spend":
                await self._spend(chat_id, user, arg.strip() or "month")
            elif cmd == "/inbox":
                await self._inbox(chat_id, user)
            elif cmd.startswith("/"):
                await self.api.send(chat_id, t(user.locale, "telegram.unknown_command"))
            else:
                # Phase 1: free text → /raw. For now, guide the user.
                await self.api.send(chat_id, t(user.locale, "telegram.help"))
        except AppError as e:
            await self.api.send(chat_id, html.escape(e.message))

    # ---- commands ---------------------------------------------------------------------------
    async def _start(self, chat_id: int, user: UserRef | None, provider_id: str, display: str | None, lang: str) -> None:
        if user is None:
            user = await self.client.register_user(PROVIDER, provider_id, display, "vi" if lang == "vi" else "en")
            await self.api.send(chat_id, t(user.locale, "telegram.start.welcome", name=html.escape(display or "")))
            await self._inbox(chat_id, user)
        else:
            await self.api.send(chat_id, t(user.locale, "telegram.start.returning", name=html.escape(user.display_name or display or "")))

    async def _link(self, chat_id: int, provider_id: str, display: str | None, code: str, lang: str) -> None:
        if not code:
            await self.api.send(chat_id, t(lang, "telegram.link.prompt"))
            return
        try:
            user = await self.client.redeem_link_code(code, PROVIDER, provider_id, display)
        except AppError:
            await self.api.send(chat_id, t(lang, "telegram.link.invalid"))
            return
        await self.api.send(chat_id, t(user.locale, "telegram.link.success"))

    async def _keys(self, chat_id: int, user: UserRef, arg: str) -> None:
        sub, _, rest = arg.partition(" ")
        if sub == "create":
            name = rest.strip() or "telegram"
            _row, plain = await self.client.create_key(user, name, DEFAULT_SCOPES)
            text = t(user.locale, "telegram.keys.created", name=html.escape(name), key=html.escape(plain))
            kb = [[{"text": t(user.locale, "telegram.keys.delete_message"), "callback_data": "msg:delete"}]]
            await self.api.send(chat_id, text, keyboard=kb)
            return
        if sub == "revoke" and rest.strip():
            await self.client.revoke_key(user, uuid.UUID(rest.strip()))
            await self.api.send(chat_id, t(user.locale, "telegram.keys.revoked", name=rest.strip()[:8]))
            return
        items = await self.client.list_keys(user)
        lines = [f"<b>{t(user.locale, 'telegram.keys.header')}</b>"]
        for k in items:
            if k.get("revoked_at"):
                continue
            lines.append(t(user.locale, "telegram.keys.line", name=html.escape(k["name"]), scopes=",".join(k["scopes"]),
                           last_used=k["last_used_at"].strftime("%d/%m") if k.get("last_used_at") else "—") + f"\n<code>{k['id']}</code>")
        lines.append("\n/keys create &lt;name&gt; · /keys revoke &lt;id&gt;")
        await self.api.send(chat_id, "\n".join(lines))

    async def _lang(self, chat_id: int, user: UserRef, arg: str) -> None:
        if arg not in ("vi", "en"):
            kb = [[{"text": "Tiếng Việt", "callback_data": "lang:vi"}, {"text": "English", "callback_data": "lang:en"}]]
            await self.api.send(chat_id, "vi / en", keyboard=kb)
            return
        user = await self.client.update_me(user, locale=arg)
        await self.api.send(chat_id, t(user.locale, "telegram.lang.set", language="Tiếng Việt" if arg == "vi" else "English"))

    async def _currency(self, chat_id: int, user: UserRef, arg: str) -> None:
        code = arg.upper()
        if code not in ("VND", "USD", "EUR"):
            kb = [[{"text": "VND", "callback_data": "cur:VND"}, {"text": "USD", "callback_data": "cur:USD"}]]
            await self.api.send(chat_id, t(user.locale, "interaction.onboarding.currency.prompt"), keyboard=kb)
            return
        user = await self.client.update_me(user, default_currency=code)
        await self.api.send(chat_id, t(user.locale, "telegram.currency.set", currency=code))

    async def _balance(self, chat_id: int, user: UserRef) -> None:
        items = await self.client.balances(user)
        lines = [f"<b>{t(user.locale, 'telegram.balance.header')}</b>"]
        for b in items:
            lines.append(t(user.locale, "telegram.balance.line", account=html.escape(b["name"]), amount=b["balance"]["display"]))
        if not items:
            lines.append("—")
        await self.api.send(chat_id, "\n".join(lines))

    async def _spend(self, chat_id: int, user: UserRef, period: str) -> None:
        rep = await self.client.spend(user, "week" if period.startswith("w") else "month")
        total = " · ".join(x["display"] for x in rep["totals"]) or "0"
        lines = [f"<b>{t(user.locale, 'telegram.spend.header', period=t(user.locale, 'common.this_week' if period.startswith('w') else 'common.this_month').lower(), total=total)}</b>"]
        grand = sum(abs(x["amount"]) for x in rep["totals"]) or 1
        for it in rep["items"][:10]:
            label = it.get("name") or t(user.locale, f"tag.{it['key']}")
            pct = round(100 * abs(it["amount"]["amount"]) / grand)
            lines.append(t(user.locale, "telegram.spend.line", tag=html.escape(label), amount=it["amount"]["display"], percent=pct))
        await self.api.send(chat_id, "\n".join(lines))

    async def _inbox(self, chat_id: int, user: UserRef) -> None:
        items = await self.client.list_interactions(user)
        await self.api.send(chat_id, t(user.locale, "telegram.inbox.header", count=len(items)))
        for it in items[:5]:
            await self._send_interaction(chat_id, user, it)

    async def _send_interaction(self, chat_id: int, user: UserRef, it: dict[str, Any]) -> None:
        prompt = t(user.locale, it["prompt"]["key"], **(it["prompt"].get("params") or {}))
        row: list[dict[str, str]] = []
        kb: list[list[dict[str, str]]] = []
        for o in it["options"]:
            label = o.get("label") or {}
            text = label.get("text") or (t(user.locale, label["key"]) if label.get("key") else o["key"])
            row.append({"text": text, "callback_data": f"ix:{it['id']}:{o['key']}"})
            if len(row) == 3:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        if it.get("allow_free_text"):
            kb.append([{"text": t(user.locale, "common.other"), "callback_data": f"ix:{it['id']}:__text"}])
        kb.append([{"text": t(user.locale, "common.skip"), "callback_data": f"ixd:{it['id']}"}])
        await self.api.send(chat_id, html.escape(prompt), keyboard=kb)

    # ---- callbacks --------------------------------------------------------------------------
    async def _callback(self, cq: dict[str, Any]) -> None:
        data: str = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat_id = msg.get("chat", {}).get("id")
        provider_id = str((cq.get("from") or {}).get("id"))
        user = await self.client.find_user_by_identity(PROVIDER, provider_id)
        await self.api.answer_callback(cq["id"])
        if data == "msg:delete" and chat_id and msg.get("message_id"):
            await self.api.delete(chat_id, msg["message_id"])
            return
        if user is None or chat_id is None:
            return
        try:
            if data.startswith("lang:"):
                await self._lang(chat_id, user, data[5:])
            elif data.startswith("cur:"):
                await self._currency(chat_id, user, data[4:])
            elif data.startswith("ixd:"):
                await self.client.dismiss_interaction(user, uuid.UUID(data[4:]))
                await self.api.send(chat_id, t(user.locale, "common.done"))
            elif data.startswith("ix:"):
                _, iid, key = data.split(":", 2)
                if key == "__text":
                    await self.api.send(chat_id, t(user.locale, "common.other"))
                    return
                res = await self.client.resolve_interaction(user, uuid.UUID(iid), key, None)
                await self.api.send(chat_id, t(user.locale, "common.done") + (f" · {html.escape(str(res.get('resolution', {}).get('result') or ''))}" if res.get("resolution") else ""))
        except AppError as e:
            await self.api.send(chat_id, html.escape(e.message))
