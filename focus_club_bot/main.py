import asyncio
import logging
import os
import ssl
import sys
from datetime import datetime

from aiohttp import web, ClientSession, TCPConnector
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database import init_db
from handlers import main_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class UnverifiedSSLSession(AiohttpSession):
    async def create_session(self):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = TCPConnector(ssl=ssl_context)
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                connector=connector,
                json_serialize=self.json_dumps,
            )
        return self._session


async def handle_ping(request):
    return web.Response(text="FOCUS 21 Bot (@focus_club1_bot) is online 24/7!")


async def handle_status(request):
    return web.json_response({
        "status": "online",
        "bot": "@focus_club1_bot",
        "project": "FOCUS 21",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


async def start_web_server():
    """Render Web Service healthcheck va 24/7 port tinglovchi server."""
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    app.router.add_get("/status", handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Healthcheck veb-server 0.0.0.0:{port} portda ishga tushdi.")


async def keep_alive_ping():
    """Render bepul serveri 15 daqiqada uxlab qolmasligi uchun har 8 daqiqada o'zini ping qiladi."""
    await asyncio.sleep(40)
    while True:
        app_url = os.getenv("RENDER_EXTERNAL_URL", "https://focus-club-bot.onrender.com").rstrip("/")
        ping_url = f"{app_url}/health"
        try:
            async with ClientSession(connector=TCPConnector(ssl=False)) as session:
                async with session.get(ping_url, timeout=15) as resp:
                    logger.info(f"Keep-alive ping: {ping_url} -> {resp.status}")
        except Exception as e:
            logger.warning(f"Keep-alive ping xatosi: {e}")
        await asyncio.sleep(480)


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN aniqlanmadi! .env faylni tekshiring.")
        return

    logger.info("Ma'lumotlar bazasi initsializatsiya qilinmoqda...")
    await init_db()

    # Start HTTP server for Render healthcheck & keep-alive when running standalone
    if os.getenv("STANDALONE_SERVER", "1") == "1":
        await start_web_server()
        asyncio.create_task(keep_alive_ping())

    bot = Bot(
        token=BOT_TOKEN,
        session=UnverifiedSSLSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot (@focus_club1_bot) 24/7 rejimida ishga tushdi!")

    try:
        while True:
            try:
                await dp.start_polling(bot, drop_pending_updates=True)
            except Exception as e:
                logger.error(f"Polling xatosi, 3 soniyada qayta ulanadi: {e}")
                await asyncio.sleep(3)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
