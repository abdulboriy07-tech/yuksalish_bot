import base64
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

_DEFAULT_TOKEN = base64.b64decode("ODk0ODYzNTI4ODpBQUhJc3pJRFBIajhTdF9raEY5a0oxYUVlUlZuWmxHQUF2RQ==").decode("utf-8")
BOT_TOKEN = os.getenv("BOT_TOKEN", _DEFAULT_TOKEN).strip()

# Admin IDs parsing
_admin_ids_raw = os.getenv("ADMIN_IDS", "7218435130").strip()
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().isdigit()]

CARD_NUMBER = os.getenv("CARD_NUMBER", "9860 3501 4081 2693").strip()
CARD_HOLDER = os.getenv("CARD_HOLDER", "Ziyotaliyev.A").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@abdulboriy_w").strip()
PROJECT_NAME = os.getenv("PROJECT_NAME", "Focus Club").strip()
SITE_URL = os.getenv("SITE_URL", "https://focus-21-ten.vercel.app").strip()
PRIVATE_CHAT_ID = os.getenv("PRIVATE_CHAT_ID", "-1004496262658").strip()

DB_PATH = BASE_DIR / "bot.db"
