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


# Global bot reference for Web API handlers
_bot_ref: Bot = None


async def handle_register(request):
    """Handle website /api/register requests and notify admin via Telegram Bot."""
    try:
        data = await request.json()
        user_name = data.get("userName")
        user_phone = data.get("userPhone")
        user_telegram = data.get("userTelegram") or "Kiritilmagan"
        tariff = data.get("tariff") or "🎯 FOCUS 21 — To'liq dastur"

        if not user_name or not user_phone:
            return web.json_response({"ok": False, "error": "Ism va telefon raqami talab qilinadi."}, status=400)

        import html
        from config import ADMIN_IDS
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = (
            f"🎯 <b>FOCUS 21 — Veb-saytdan yangi ariza!</b>\n\n"
            f"👤 <b>Ism-familiya:</b> {html.escape(str(user_name))}\n"
            f"📞 <b>Telefon:</b> <code>{html.escape(str(user_phone))}</code>\n"
            f"💬 <b>Telegram:</b> {html.escape(str(user_telegram))}\n"
            f"📦 <b>Tanlangan paket:</b> {html.escape(str(tariff))}\n"
            f"🕒 <b>Vaqt:</b> {now_str}\n"
            f"🌐 <b>Server:</b> Hetzner Cloud (178.105.244.3)"
        )

        if _bot_ref:
            for admin_id in ADMIN_IDS:
                try:
                    await _bot_ref.send_message(
                        chat_id=admin_id,
                        text=msg,
                        parse_mode="HTML"
                    )
                except Exception as err:
                    logger.error(f"Admin {admin_id} ga xabar yuborishda xatolik: {err}")

        return web.json_response({
            "ok": True,
            "botUrl": "https://t.me/focus_club1_bot?start=focus21"
        })
    except Exception as e:
        logger.error(f"Register API xatosi: {e}")
        return web.json_response({
            "ok": True,
            "botUrl": "https://t.me/focus_club1_bot?start=focus21"
        })


async def start_web_server(bot: Bot):
    """24/7 port tinglovchi veb-server va /api/register endpointi."""
    global _bot_ref
    _bot_ref = bot

    port = int(os.getenv("PORT", "8000"))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/api/register", handle_register)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Veb-server va /api/register 0.0.0.0:{port} portda muvaffaqiyatli ishga tushdi.")


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

    bot = Bot(
        token=BOT_TOKEN,
        session=UnverifiedSSLSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Start HTTP server for website /api/register & healthcheck
    if os.getenv("STANDALONE_SERVER", "1") == "1":
        await start_web_server(bot)
        # Faqat Render muhitida keep_alive kerak
        if os.getenv("RENDER"):
            asyncio.create_task(keep_alive_ping())

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot (@focus_club1_bot) 24/7 rejimida ishga tushdi!")

    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
