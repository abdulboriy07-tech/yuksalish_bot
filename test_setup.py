import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot
from google import genai

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

async def test():
    print("--- 1. Testing Telegram Bot Token ---")
    try:
        bot = Bot(token=BOT_TOKEN)
        me = await bot.get_me()
        print(f"Telegram Bot OK: @{me.username} ({me.first_name})")
        await bot.session.close()
    except Exception as e:
        print(f"Telegram Bot ERROR: {e}")

    print("\n--- 2. Testing Gemini API Key ---")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="Salom, o'zingni 1 ta qisqa gap bilan tanishtir."
        )
        print(f"Gemini API OK: {resp.text.strip()}")
    except Exception as e:
        print(f"Gemini API ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test())
