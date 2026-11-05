import asyncio
import gc
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, Update
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, roles, runtime
from app.config import cfg
from app.engine import Engine
from app.handlers import get_routers
from app.middlewares import StaffGateMiddleware, ThrottleMiddleware
from app.services import store
from app.utils import spawn
from app.web import auth
from app.web.api import router as api_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("main")
STATIC = Path(__file__).parent / "app" / "web" / "static"

bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
dp = Dispatcher(storage=MemoryStorage())

for _ev in (dp.message, dp.callback_query):
    _ev.outer_middleware(StaffGateMiddleware())
    _ev.outer_middleware(ThrottleMiddleware())
for _r in get_routers():
    dp.include_router(_r)


@dp.errors()
async def on_error(event: ErrorEvent):
    log.exception("Unhandled error", exc_info=event.exception)
    upd = event.update
    with suppress(Exception):
        if upd.callback_query:
            await upd.callback_query.answer("⚠️ Something went wrong. Please try again.", show_alert=True)
        elif upd.message:
            await upd.message.answer("⚠️ Something went wrong. Please try again.")
    return True


async def setup_commands() -> None:
    await bot.set_my_commands([BotCommand(command="start", description="🚀 Start / Home"),
                               BotCommand(command="cancel", description="❌ Cancel current action"),
                               BotCommand(command="stats", description="📊 Engine stats"),
                               BotCommand(command="admins", description="👥 List admins (owner)")])


async def housekeeping() -> None:
    """Nothing here ever deletes data - it only forces garbage collection to keep RAM flat on the
    free tier. Logs, removed channels/sessions/admins etc. are intentionally kept forever."""
    while True:
        await asyncio.sleep(6 * 3600)
        gc.collect()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(db.init, cfg)
    await roles.load()
    me = await bot.get_me()
    owner_chat = None
    with suppress(Exception):
        owner_chat = await bot.get_chat(cfg.owner_id)
    await roles.ensure_owner_record(owner_chat.full_name if owner_chat else "Owner", owner_chat.username if owner_chat else "")
    await auth.ensure_admin()
    if cfg.api_id and cfg.api_hash and not await store.get_apis():
        await db.put(f"settings/apis/{int(cfg.api_id)}", {"hash": cfg.api_hash})
        db.invalidate("settings")
    if cfg.proxy_url and not (await store.get_proxy())["url"]:
        await store.set_setting("proxy", {"enabled": True, "url": cfg.proxy_url})
    runtime.bot = bot
    runtime.engine = Engine(bot)
    await runtime.engine.start()
    await setup_commands()
    poller, hk = None, asyncio.create_task(housekeeping())
    if cfg.base_url:
        await bot.set_webhook(f"{cfg.base_url}/webhook", secret_token=cfg.webhook_secret,
                              allowed_updates=dp.resolve_used_update_types(), max_connections=40)
        log.info("Webhook set: %s/webhook  |  Admin panel: %s/admin", cfg.base_url, cfg.base_url)
    else:
        await bot.delete_webhook()
        poller = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        log.info("No BASE_URL -> long polling (local dev). Admin panel: http://localhost:%s/admin", cfg.port)
    log.info("Bot @%s ready. Owner=%s, admins=%d", me.username, cfg.owner_id, len(roles.ADMINS))
    yield
    hk.cancel()
    if poller:
        poller.cancel()
    await runtime.engine.stop()
    await bot.session.close()


api = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
api.include_router(api_router)
api.mount("/admin/static", StaticFiles(directory=STATIC), name="static")


@api.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(("/admin", "/api")):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return resp


@api.api_route("/", methods=["GET", "HEAD"])
@api.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}


@api.get("/admin", include_in_schema=False)
@api.get("/admin/", include_in_schema=False)
async def admin_page():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@api.post("/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if x_telegram_bot_api_secret_token != cfg.webhook_secret:
        raise HTTPException(status_code=403)
    update = Update.model_validate(await request.json(), context={"bot": bot})
    spawn(dp.feed_update(bot, update))
    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run("main:api", host="0.0.0.0", port=cfg.port)
