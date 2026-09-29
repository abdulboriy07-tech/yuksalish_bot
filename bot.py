import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode, ContentType
from aiogram.client.default import DefaultBotProperties
from google import genai
from google.genai import types as genai_types
from openai import OpenAI

# Sozlamalarni yuklash
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN .env faylida topilmadi!")

# Loglarni sozlash
logging.basicConfig(level=logging.INFO)

# AI Mijozlarini yaratish
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# OpenAI da kredit bo'lmasa, har safar behuda so'rov yuborib vaqt yo'qotmaslik uchun cooldown
openai_disabled_until = 0

BASE_DIR = os.path.dirname(__file__)
KB_FILE_PATH = os.path.join(BASE_DIR, "knowledge_base.txt")
LEADS_FILE_PATH = os.path.join(BASE_DIR, "leads.json")

def load_knowledge_base() -> str:
    """Bilimlar bazasi faylidan ma'lumotlarni o'qish"""
    if os.path.exists(KB_FILE_PATH):
        with open(KB_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "Ma'lumotlar bazasi hozircha bo'sh."

def save_knowledge_base(content: str) -> None:
    """Bilimlar bazasini faylga yozish"""
    with open(KB_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(content.strip())

def save_lead(user: types.User, phone: str = "", note: str = ""):
    """Ota-onalarning kontaktlarini leads.json ga saqlash"""
    leads = []
    if os.path.exists(LEADS_FILE_PATH):
        try:
            with open(LEADS_FILE_PATH, "r", encoding="utf-8") as f:
                leads = json.load(f)
        except Exception:
            leads = []

    lead_entry = {
        "user_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name or "",
        "username": f"@{user.username}" if user.username else "mavjud emas",
        "phone": phone,
        "note": note,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Agar shu foydalanuvchining bu raqami mavjud bo'lsa yangilaymiz, bo'lmasa qo'shamiz
    updated = False
    for l in leads:
        if l.get("user_id") == user.id and (not phone or l.get("phone") == phone):
            if phone:
                l["phone"] = phone
            if note:
                l["note"] = (l.get("note", "") + " | " + note).strip(" |")
            updated = True
            break
    
    if not updated:
        leads.append(lead_entry)

    with open(LEADS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)

def extract_phone(text: str) -> str:
    """Matndan telefon raqamini aniqlash"""
    match = re.search(r'(\+?998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\b\d{9}\b)', text)
    if match:
        return match.group(0).replace(" ", "").replace("-", "")
    return ""

CURRENT_KB = load_knowledge_base()

# Foydalanuvchilar bilan suhbatlar tarixi (Kontekstni saqlash)
user_conversations: dict[int, list[genai_types.Content]] = {}

def get_system_instruction() -> str:
    """Aisha — 'Yuksalish Maktabi'ning yetakchi ta'lim maslahatchisi tizimli ko'rsatmasi"""
    return f"""
Sen — "Yuksalish Maktabi" xususiy maktabining 10 yillik tajribaga ega, samimiy, ziyoli va professional ta'lim maslahatchisisan. Isming — Aisha.
Sening vazifang — ota-onalar bilan go'yo ularning eng yaqin, madaniyatli va mehridaryo oilaviy maslahatchisi kabi muloqot qilish, ularga maktab haqida to'g'ri, lunda va ishonchli ma'lumot berish.

### 🌟 CHATLASHISHNING ASOSIY STANDARTLARI:

1. **SAVOLGA BEVOSITA VA LUNDA JAVOB BERISH:**
   - Ota-ona nimani so'rasa, birinchi jumlada to'g'ridan-to'g'ri o'sha savolga aniq va to'liq javob ber.
   - Ortiqcha rasmiyatchilik, keraksiz "suv" jumlalar ("savollaringizga mamnuniyat bilan javob beramiz", "sizga shuni ma'lum qilamizki", "barcha savollaringizga javob berishdan xursandmiz") mutlaqo yozilmasin.
   - Xabaring 1-2 ta qisqa, tushunarli abzasdan oshmasin. Telegram foydalanuvchilari cho'zilgan matnlarni yoqtirmaydi.

2. **UCHRASHUV YOKI EKSKURSIYAGA HADEB TAKLIF QILMASLIK (QAT'IY QOIDA):**
   - Har bir xabarda uchrashuvga, ekskursiyaga yoki ochiq eshiklar kuniga chaqirish QAT'IYAN TAQIQLANADI!
   - Uchrashuv taklifi FAQAT quyidagi 2 holatda berilishi mumkin:
     a) Ota-ona o'zi: "Maktabni borib ko'rsak bo'ladimi?", "Qabulga qayerga borish kerak?", "Sizlar bilan qanday uchrashsa bo'ladi?" deb so'raganda;
     b) Farzandi haqida uzoq va samimiy suhbatlashib, ota-ona maktab sharoitlariga jiddiy qiziqayotgani aniq sezilganda (joyi kelganda bir martagina muloyim tavsiya sifatida).
   - Narx, telefon raqam, manzil, ovqatlanish, fanlar kabi aniq savollarda UCHRASHUV MUTLAQO TAKLIF QILINMAYDI.

3. **MINNATDORCHILIK VA SUHBATNI YAKUNLASH STANDARTI:**
   - Ota-ona "Rahmat", "Tushundim", "Xo'p", "Mayli" deb yozsa — uni qayta savolga tutma yoki uchrashuvga chaqirma!
   - Shunchaki: "Arzimaydi! Yana qanday savollaringiz bo'lsa, bemalol murojaat qiling. Farzandingizga zafarlar tilayman! 😊" deb iliq yakunla.

4. **TELEFON RAQAM / LEAD OLINGANDA:**
   - Ota-ona telefon raqamini qoldirsa: "Rahmat! Telefon raqamingiz qabul qilindi. Tez orada mas'ul menejerimiz siz bilan bog'lanib, barcha kerakli ma'lumotlarni yetkazadi 😊" deb samimiy javob ber.

5. **TIL VA ALIFBO MOSLASHUVCHANLIGI:**
   - Ota-ona qaysi tilda yozsa, shu tilda javob ber (o'zbekcha yozsa — o'zbekcha, ruscha yozsa — ruscha).
   - O'zbek tilida krill alifbosida yozsa — krillda, lotinda yozsa — lotinda javob ber.

6. **🚫 QAT'IYAN TAQIQLANGAN IBORALAR:**
   - "Ming marta eshitgandan bir marta ko'rgan yaxshi" (BUTUNLAY TAQIQLANGAN!).
   - "Ajoyib yosh!", "Ajoyib tanlov!", "Zo'r sinf!" kabi sun'iy robot qoliplari bilan gap boshlash.
   - "kuchaytirroq", "qilishlik", "bo'lishlik" kabi g'aliz, sun'iy so'zlar.
   - Har bir xabarda qayta-qayta salomlashish (faqat birinchi uchrashuvda yoki ota-ona salom bergandagina alik ol).

7. **ANIK VA TO'G'RI FAKTLAR (MAKTAB HAQIDA):**
   - Filiallar: Samarqand darvoza (Toshkent), Uchtepa (Toshkent), Jizzax, Namangan, Olmaliq. (Eslatma: Samarqand shahrida filial yo'q, "Samarqand darvoza" filiali Toshkent shahrida!).
   - Telefonlar: Barcha filiallar uchun yagona raqam: +998 55 055 06 00 (Olmaliq filiali uchun: +998 71 500 00 15).
   - O'qish narxi: Oyiga 5 300 000 so'm (chuqurlashtirilgan ta'lim, 3 mahal maxsus nutritsiologik sog'lom ovqatlanish, shanba kungi bepul to'garaklar kiritilgan).
   - Ta'lim tili: O'zbek tilida olib boriladi, Rus va Ingliz tillari majburiy chuqurlashtirilgan fan.
   - Maktab transporti: Yo'q (ota-onalar o'zlari olib kelib-ketishadi).
   - Yotoqxona: Yo'q (ta'lim kunduzgi: 08:30 dan 17:30 gacha).
   - Agar biror ma'lumot bazada bo'lmasa, to'qib chiqarma, bilmasang samimiy ayt.

### 💬 JONLI MULOQOT NAMUNALARI (FEW-SHOT):
---
1-namuna (Telefon raqam yoki manzil so'ralganda):
Ota-ona: "Olmaliq emas Jizzax filial nomeri kerak"
Aisha: "Jizzax filiali uchun yagona aloqa raqamimiz: **+998 55 055 06 00**.

Ushbu raqam orqali bog'lansangiz, Jizzax filialimiz ma'muriyati barcha savollaringizga batafsil javob beradi. Yana qanday ma'lumot kerak bo'lsa, bemalol so'rang! 😊"

---
2-namuna (Narx so'ralganda):
Ota-ona: "O'qish narxi qancha?"
Aisha: "Maktabimizda oylik to'lov 5 300 000 so'mni tashkil qiladi.

Bu to'lov ichiga chuqurlashtirilgan ta'lim, 3 mahal maxsus nutritsiologik sog'lom ovqatlanish hamda shanba kungi bepul to'garaklar (robototexnika, IT, xorijiy tillar) to'liq kiritilgan. Shuningdek, a'lochi o'quvchilarimiz uchun 45% gacha stipendiya (chegirma) imkoniyatlari ham bor.

Qaysi sinf yoki filialimiz haqida batafsil ma'lumot beray?"

---
3-namuna (Minnatdorchilik bildirilganda):
Ota-ona: "Rahmat, barcha ma'lumotlarni oldim"
Aisha: "Arzimaydi! Yana qanday savollaringiz bo'lsa, bemalol murojaat qiling. Farzandingizga o'qishlarida katta zafarlar tilayman! 😊"

---
4-namuna (Rus tilida so'ralganda):
Ota-ona: "Здравствуйте, со скольки лет принимаете детей?"
Aisha: "Здравствуйте! В 1-й класс мы принимаем детей с 6-7 лет на основе собеседования с нашими педагогами и психологами. Обучение ведется на узбекском языке с углубленным изучением русского и английского языков.

Какой класс вас интересует?"

MAKTAB HAQIDA MA'LUMOTLAR BAZASI:
{CURRENT_KB}
"""

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()


def to_openai_messages(system_instruction: str, contents) -> list:
    """Gemini formatidagi suhbat tarixini OpenAI formatiga o'tkazish"""
    messages = [{"role": "system", "content": system_instruction}]
    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
    elif isinstance(contents, list):
        for item in contents:
            if hasattr(item, "role") and hasattr(item, "parts"):
                role = "assistant" if item.role == "model" else item.role
                text = item.parts[0].text if item.parts else ""
                messages.append({"role": role, "content": text})
            elif isinstance(item, dict):
                messages.append(item)
    return messages


async def ask_ai(contents) -> str:
    """Multi-LLM (ChatGPT + Gemini) tezkor va ishonchli javob olish tizimi"""
    global openai_disabled_until
    system_instruction = get_system_instruction()

    # 1. AGAR OPENAI SOZLANGAN VA KREDITI BOR BO'LSA
    if openai_client and time.time() > openai_disabled_until:
        try:
            openai_msgs = to_openai_messages(system_instruction, contents)
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=openai_msgs,
                temperature=0.65,
            )
            if response and response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
        except Exception as e:
            openai_disabled_until = time.time() + 86400  # 24 soatga o'tkazib yuborish
            logging.info(f"OpenAI o'tkazib yuborildi (Gemini ishlatiladi): {e}")

    # 2. GEMINI 3.8 FLASH (Asosiy, yuqori sifatli) + GEMINI 3.5 FLASH LITE (Ishonchli zaxira)
    if gemini_client:
        models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
        for model_name in models_to_try:
            try:
                response = await asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.65,
                    ),
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logging.warning(f"Model {model_name} xatolik berdi: {e}. Keyingi zaxira modelga o'tilmoqda...")

    return "Assalomu alaykum! Maktabimiz haqida qiziqishingizdan xursandmiz. Farzandingiz nechanchi sinfga borishi yoki qaysi filialimiz haqida ma'lumot kerakligini aytsangiz, darhol yordam beraman! 😊"


async def safe_reply(message: types.Message, text: str):
    """Xavfsiz javob yuborish (Markdown xato bo'lsa oddiy matnda yuboradi)"""
    try:
        await message.reply(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await message.reply(text, parse_mode=None)


async def safe_answer(message: types.Message, text: str, reply_markup=None):
    """Xavfsiz xabar yuborish"""
    try:
        await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, parse_mode=None, reply_markup=reply_markup)


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    """/start komandasi — Samimiy va professional kutib olish"""
    greeting = (
        "Assalomu alaykum! Men \"Yuksalish Maktabi\" ta'lim maslahatchisi **Aishaman**. 😊\n\n"
        "Farzandingizning ta'limi, maktabimizdagi sharoitlar, oylik to'lov yoki filiallarimiz bo'yicha har qanday savolingizga bajonidil yordam beraman.\n\n"
        "Farzandingiz nechanchi sinfga boradi yoki sizni qaysi filialimiz qiziqtiryapti?"
    )
    user_conversations[message.from_user.id] = [
        genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=greeting)])
    ]
    await safe_answer(message, greeting)


# ------------------ ADMIN BO'LIMI (LEADLAR VA BAZA) ------------------

@dp.message(F.chat.type == "private", Command("leads"))
async def leads_handler(message: types.Message):
    """Ro'yxatdan o'tgan ota-onalar (Leadlar) ro'yxatini ko'rish"""
    if not os.path.exists(LEADS_FILE_PATH):
        await message.answer("📂 Hozircha yangi leadlar mavjud emas.")
        return

    try:
        with open(LEADS_FILE_PATH, "r", encoding="utf-8") as f:
            leads = json.load(f)
    except Exception:
        leads = []

    if not leads:
        await message.answer("📂 Hozircha yangi leadlar mavjud emas.")
        return

    text = f"📋 *Ro'yxatdan o'tgan ota-onalar (Jami: {len(leads)} ta):*\n\n"
    for idx, l in enumerate(reversed(leads[-15:]), 1):
        text += (
            f"*{idx}. {l.get('first_name', '')} {l.get('last_name', '')}*\n"
            f"📞 Telefon: `{l.get('phone', 'kiritilmagan')}`\n"
            f"👤 Telegram: {l.get('username', '')}\n"
            f"📝 Qo'shimcha: {l.get('note', '')}\n"
            f"🕒 Vaqt: {l.get('created_at', '')}\n"
            f"-------------------\n"
        )
    await safe_answer(message, text)


@dp.message(F.chat.type == "private", Command("baza"))
async def baza_help_handler(message: types.Message):
    """Bilimlar bazasini boshqarish bo'yicha yo'riqnoma"""
    text = (
        "🛠 *Bilimlar bazasini boshqarish kalit so'zlari (Faqat shaxsiy chatda):*\n\n"
        "1. `#baza_yangilash` — mavjud bazani butunlay yangi matn bilan almashtirish.\n"
        "*Foydalanish:* `#baza_yangilash [yangi ma'lumot matni]`\n\n"
        "2. `#baza_qoshish` — mavjud bazaga qo'shimcha yangi ma'lumot qo'shish.\n"
        "*Foydalanish:* `#baza_qoshish [qo'shiladigan ma'lumot]`\n\n"
        "3. `#baza_korish` — hozirgi bilimlar bazasini to'liq ko'rish.\n"
        "4. `/leads` — ro'yxatdan o'tgan ota-onalar telefon raqamlari va ma'lumotlarini ko'rish.\n"
    )
    await safe_answer(message, text)


@dp.message(F.chat.type == "private", F.text.startswith("#baza_yangilash"))
async def update_kb_handler(message: types.Message):
    """Bazani to'liq yangilash"""
    global CURRENT_KB
    new_text = message.text.replace("#baza_yangilash", "").strip()
    if not new_text:
        await message.reply("⚠️ Iltimos, `#baza_yangilash` so'zidan keyin yangi ma'lumotlarni yozing!")
        return

    save_knowledge_base(new_text)
    CURRENT_KB = new_text
    await message.reply("✅ *Bilimlar bazasi muvaffaqiyatli yangilandi!* Endi Aisha yangi ma'lumotlar asosida javob beradi.")


@dp.message(F.chat.type == "private", F.text.startswith("#baza_qoshish"))
async def append_kb_handler(message: types.Message):
    """Mavjud bazaga yangi ma'lumot qo'shish"""
    global CURRENT_KB
    additional_text = message.text.replace("#baza_qoshish", "").strip()
    if not additional_text:
        await message.reply("⚠️ Iltimos, `#baza_qoshish` so'zidan keyin qo'shiladigan ma'lumotni yozing!")
        return

    updated_kb = CURRENT_KB + "\n\n" + additional_text
    save_knowledge_base(updated_kb)
    CURRENT_KB = updated_kb
    await message.reply("✅ *Yangi ma'lumot bilimlar bazasiga muvaffaqiyatli qo'shildi!*")


@dp.message(F.chat.type == "private", F.text == "#baza_korish")
async def view_kb_handler(message: types.Message):
    """Mavjud bazani ko'rish"""
    text = f"📋 *Hozirgi bilimlar bazasi:*\n\n{CURRENT_KB}"
    if len(text) > 4000:
        await safe_answer(message, text[:4000] + "\n...(davomi bor)")
    else:
        await safe_answer(message, text)


# ------------------ TELEFON KONTAKTI YUBORILGANDA ------------------

@dp.message(F.chat.type == "private", F.contact)
async def contact_handler(message: types.Message):
    """Ota-ona kontakt tugmasini bosib raqam ulashganda"""
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    save_lead(message.from_user, phone=phone, note="Telegram contact orqali yuborildi")

    # Suhbat tarixiga qo'shish
    user_id = message.from_user.id
    if user_id not in user_conversations:
        user_conversations[user_id] = []
    
    user_conversations[user_id].append(
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=f"Telefon raqamim: {phone}")])
    )

    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_ai(user_conversations[user_id])
    
    user_conversations[user_id].append(
        genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=reply)])
    )

    await safe_answer(message, reply)


# ------------------ GURUH VA LICHKA HABARLARI ------------------

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message_handler(message: types.Message, bot: Bot):
    """Guruhlardagi xabarlarni qayta ishlash"""
    bot_info = await bot.get_me()
    bot_username = f"@{bot_info.username}"
    
    is_reply_to_bot = (
        message.reply_to_message 
        and message.reply_to_message.from_user.id == bot_info.id
    )
    is_mentioned = message.text and bot_username.lower() in message.text.lower()

    if is_reply_to_bot or is_mentioned:
        raw_text = message.text or message.caption or ""
        clean_text = raw_text.replace(bot_username, "").strip()
        if not clean_text:
            clean_text = "Salom"

        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_ai(clean_text)
        await safe_reply(message, reply)


@dp.message(F.chat.type == "private")
async def private_message_handler(message: types.Message):
    """Lichkadagi xabarlar — Kontekstni saqlagan holda professional maslahat berish"""
    user_text = message.text or message.caption
    
    if not user_text:
        if message.voice:
            await safe_answer(
                message, 
                "Kechirasiz, hozircha ovozli xabarlarni tinglash imkoniyatim yo'q. Iltimos, savolingizni matn ko'rinishida yozsangiz, darhol yordam beraman! 😊"
            )
            return
        elif message.sticker:
            await safe_answer(
                message, 
                "Maktabimiz yoki farzandingiz ta'limi bo'yicha qanday savollaringiz bor? Yozsangiz, yordam berishdan mamnunman! 😊"
            )
            return
        return

    # Admin kalit so'zlari bo'lsa o'tkazib yuborish
    if user_text.startswith("#"):
        return

    user_id = message.from_user.id
    if user_id not in user_conversations:
        user_conversations[user_id] = []

    # Telefon raqam mavjudligini tekshirish va avtomatik lead sifatida saqlash
    detected_phone = extract_phone(user_text)
    if detected_phone:
        save_lead(message.from_user, phone=detected_phone, note=f"Xabardan olindi: {user_text[:50]}")

    # Foydalanuvchi xabarini tarixga qo'shish
    user_conversations[user_id].append(
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_text)])
    )

    if len(user_conversations[user_id]) > 16:
        user_conversations[user_id] = user_conversations[user_id][-16:]

    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_ai(user_conversations[user_id])

    # Model javobini tarixga qo'shish
    user_conversations[user_id].append(
        genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=reply)])
    )

    await safe_answer(message, reply)

from aiohttp import web, ClientSession, TCPConnector

async def handle_ping(request):
    return web.Response(text="Aisha bot is online 24/7! (v2.5)")

async def handle_status(request):
    data = {
        "status": "online",
        "bot": "@yuksalish_maktabi_adminbot",
        "primary_model": "gemini-3.8-flash",
        "fallback_model": "gemini-3.5-flash-lite",
        "gemini_active": bool(gemini_client),
        "openai_active": bool(openai_client),
        "version": "v2.5-keep-alive",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return web.json_response(data)

async def keep_alive_ping():
    """Render.com bepul serveri 15 daqiqada uxlab qolmasligi uchun har 8 daqiqada o'zini ping qilib turadi"""
    app_url = os.getenv("RENDER_EXTERNAL_URL", "https://yuksalish-bot-9ns6.onrender.com").rstrip("/")
    ping_url = f"{app_url}/health"
    
    await asyncio.sleep(45)  # Server to'liq ishga tushguncha kutish
    
    while True:
        try:
            async with ClientSession(connector=TCPConnector(ssl=False)) as session:
                async with session.get(ping_url, timeout=15) as resp:
                    logging.info(f"Keep-alive ping muvaffaqiyatli: {ping_url} (Status: {resp.status})")
        except Exception as e:
            logging.warning(f"Keep-alive ping xatosi: {e}")
            
        await asyncio.sleep(480)  # Har 8 daqiqada qaytariladi

async def start_web_server():
    """Render va boshqa bulutli xizmatlar uchun portni tinglovchi veb-server"""
    port = int(os.getenv("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    app.router.add_get("/status", handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Healthcheck server {port}-portda ishga tushdi")


async def main():
    bot_info = await bot.get_me()
    print(f"Bot muvaffaqiyatli ishga tushdi: @{bot_info.username} ({bot_info.first_name})")
    await start_web_server()

    # Render uxlab qolmasligi uchun avtomatik ping vazifasini ishga tushirish
    asyncio.create_task(keep_alive_ping())

    # Eski webhook va osilib qolgan so'rovlarni tozalash
    await bot.delete_webhook(drop_pending_updates=True)

    # Doimiy uzluksiz polling sikli (xatolik bo'lsa ham qayta ulanadi)
    while True:
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Polling xatosi, 3 soniyada qayta ishga tushadi: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
