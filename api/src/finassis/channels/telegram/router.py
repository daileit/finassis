"""POST /channels/telegram/webhook — secret-token verified; delegates to Handlers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, Request

from ...client.inprocess import InProcessClient
from ...config import get_settings
from ...errors import NotFound, Unauthorized
from ...logging import get_logger
from .handlers import Handlers
from .tg import TelegramAPI

log = get_logger(__name__)
router = APIRouter(prefix="/channels/telegram", tags=["channels"])

_handlers: Handlers | None = None


def _get(request: Request) -> Handlers:
    global _handlers
    if _handlers is None:
        s = get_settings()
        assert s.telegram_bot_token
        st = request.app.state.services
        _handlers = Handlers(InProcessClient(st), TelegramAPI(s.telegram_bot_token))
    return _handlers


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request, bg: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    s = get_settings()
    if not s.telegram_bot_token:
        raise NotFound("telegram channel disabled")
    if s.telegram_webhook_secret and x_telegram_bot_api_secret_token != s.telegram_webhook_secret:
        raise Unauthorized("bad webhook secret")
    update = await request.json()
    h = _get(request)
    bg.add_task(_safe_handle, h, update)
    return {"ok": True}


async def _safe_handle(h: Handlers, update: dict[str, Any]) -> None:
    try:
        await h.handle_update(update)
    except Exception as e:  # never let Telegram retry storms grow
        log.exception("telegram.handler_failed", error=str(e))


@router.post("/set-webhook", include_in_schema=False)
async def set_webhook(request: Request) -> dict[str, Any]:
    """Admin convenience: point Telegram at this deployment. Protected by the same secret."""
    s = get_settings()
    if not s.telegram_bot_token:
        raise NotFound("telegram channel disabled")
    secret = request.headers.get("x-telegram-bot-api-secret-token")
    if s.telegram_webhook_secret and secret != s.telegram_webhook_secret:
        raise Unauthorized("bad secret")
    h = _get(request)
    return await h.api.set_webhook(f"{s.public_base_url}/channels/telegram/webhook", s.telegram_webhook_secret)
