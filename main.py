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
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent, Update
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, runtime
from app.config import cfg
from app.engine import Engine
from app.handlers import get_routers
from app.middlewares import BanMiddleware, ForceJoinMiddleware, ThrottleMiddleware
from app.services import forcejoin, store
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
    _ev.outer_middleware(BanMiddleware())
    _ev.outer_middleware(ThrottleMiddleware())
    _ev.outer_middleware(ForceJoinMiddleware())
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
    user_cmds = [BotCommand(command="start", description="🚀 Start / Home"),
                 BotCommand(command="cancel", description="❌ Cancel current action")]
    await bot.set_my_commands(user_cmds)
    for aid in cfg.admin_ids:
        with suppress(Exception):
            await bot.set_my_commands(user_cmds + [BotCommand(command="admin", description="🛠 Admin commands")],
                                      scope=BotCommandScopeChat(chat_id=aid))


async def housekeeping() -> None:
    """Keeps Firebase and RAM small: trims old logs once a day, forces GC."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            for src in ("bot", "admin"):
                keys = sorted(await db.keys(f"logs/{src}"))
                if len(keys) > 5000:
                    await db.multi_update({f"logs/{src}/{k}": None for k in keys[:-5000]})
            gc.collect()
        except Exception:  # noqa: BLE001
            log.exception("housekeeping failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(db.init, cfg)
    await store.seed_defaults()
    await store.migrate()
    await auth.ensure_admin()
    if cfg.api_id and cfg.api_hash and not await store.get_apis():  # seed API pool from env on first run
        await db.put(f"settings/apis/{int(cfg.api_id)}", {"hash": cfg.api_hash})
        db.invalidate("settings")
    store.BANNED.update(int(k) for k in await db.keys("idx/banned"))
    runtime.bot = bot
    runtime.engine = Engine(bot)
    await runtime.engine.start()
    await setup_commands()
    if cfg.force_channel and not await forcejoin.get_force_channel():
        _, msg = await forcejoin.set_force_channel(bot, cfg.force_channel)
        log.info("FORCE_CHANNEL from env: %s", msg)
    poller, hk = None, asyncio.create_task(housekeeping())
    if cfg.base_url:
        await bot.set_webhook(f"{cfg.base_url}/webhook", secret_token=cfg.webhook_secret,
                              allowed_updates=dp.resolve_used_update_types(), max_connections=40)
        log.info("Webhook set: %s/webhook  |  Admin panel: %s/admin", cfg.base_url, cfg.base_url)
    else:
        await bot.delete_webhook()
        poller = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        log.info("No BASE_URL -> long polling (local dev). Admin panel: http://localhost:%s/admin", cfg.port)
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
    spawn(dp.feed_update(bot, update))  # answer Telegram instantly, process in background -> never hangs
    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run("main:api", host="0.0.0.0", port=cfg.port)
