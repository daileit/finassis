"""Minimal Telegram Bot API client (sendMessage, deleteMessage, answerCallbackQuery, setWebhook)."""

from __future__ import annotations

from typing import Any

import httpx

from ...logging import get_logger

log = get_logger(__name__)


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base = f"https://api.telegram.org/bot{token}"
        self._http = httpx.AsyncClient(timeout=10)

    async def close(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        r = await self._http.post(f"{self.base}/{method}", json={k: v for k, v in params.items() if v is not None})
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram.api_error", method=method, error=data.get("description"))
        return data  # type: ignore[no-any-return]

    async def send(self, chat_id: int | str, text: str, *, keyboard: list[list[dict[str, str]]] | None = None,
                   parse_mode: str | None = "HTML") -> dict[str, Any]:
        markup = {"inline_keyboard": keyboard} if keyboard else None
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=markup,
                               disable_web_page_preview=True)

    async def delete(self, chat_id: int | str, message_id: int) -> dict[str, Any]:
        return await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

    async def answer_callback(self, callback_query_id: str, text: str | None = None) -> dict[str, Any]:
        return await self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    async def set_webhook(self, url: str, secret: str | None) -> dict[str, Any]:
        return await self.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])
