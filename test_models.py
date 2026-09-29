import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

candidates = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.6-flash",
]

for m in candidates:
    try:
        resp = client.models.generate_content(
            model=m,
            contents="test"
        )
        print(f"SUCCESS: {m}")
    except Exception as e:
        print(f"FAILED {m}: {e}")
