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

# Admin ID lari (vergul bilan ajratilgan, masalan: ADMIN_IDS=1234567,9876543)
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set(int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit())

def is_admin(user_id: int) -> bool:
    """Admin huquqini tekshirish (agar ADMIN_IDS bo'sh bo'lsa, ochiq turadi)"""
    if not ADMIN_IDS:
        return True
    return user_id in ADMIN_IDS

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN .env faylida topilmadi!")

# Loglarni sozlash
logging.basicConfig(level=logging.INFO)

# AI Mijozlarini yaratish
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY, max_retries=0) if OPENAI_API_KEY else None

# OpenAI da kredit bo'lmasa, har safar behuda so'rov yuborib vaqt yo'qotmaslik uchun cooldown
openai_disabled_until = 0

BASE_DIR = os.path.dirname(__file__)
KB_FILE_PATH = os.path.join(BASE_DIR, "knowledge_base.txt")
LEADS_FILE_PATH = os.path.join(BASE_DIR, "leads.json")

leads_lock = asyncio.Lock()

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

async def save_lead(user: types.User, phone: str = "", note: str = "") -> dict:
    """Ota-onalarning kontaktlarini leads.json ga asinxron va xavfsiz saqlash"""
    async with leads_lock:
        leads = []
        if os.path.exists(LEADS_FILE_PATH):
            try:
                with open(LEADS_FILE_PATH, "r", encoding="utf-8") as f:
                    leads = json.load(f)
            except Exception:
                leads = []

        lead_entry = {
            "user_id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": f"@{user.username}" if user.username else "mavjud emas",
            "phone": phone,
            "note": note,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
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

        return lead_entry

async def notify_admins(text: str):
    """Adminlarga Telegram orqali bildirishnoma yuborish"""
    if not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.warning(f"Adminga bildirishnoma yuborishda xatolik ({admin_id}): {e}")

def extract_phone(text: str) -> str:
    """Matndan O'zbekiston telefon raqamini har qanday formatda aniqlash va normallashtirish"""
    if not text:
        return ""
    # 1. +998 bilan yozilgan formatlar: +998 90 123 45 67, +998(90)123-45-67 va h.k.
    m_full = re.search(r'(?:\+?998)[\s\(\)\-\.]*(\d{2})[\s\(\)\-\.]*(\d{3})[\s\(\)\-\.]*(\d{2})[\s\(\)\-\.]*(\d{2})', text)
    if m_full:
        return f'+998{m_full.group(1)}{m_full.group(2)}{m_full.group(3)}{m_full.group(4)}'
    
    # 2. 9 talik mahalliy format: 90 123 45 67, (90) 123-45-67, 93-123-45-67, 33 111 22 33
    m_local = re.search(r'(?:\b|\()([389]\d|20|50|55|71|77|78)[\s\)\-\.]*(\d{3})[\s\-\.]*(\d{2})[\s\-\.]*(\d{2})\b', text)
    if m_local:
        return f'+998{m_local.group(1)}{m_local.group(2)}{m_local.group(3)}{m_local.group(4)}'
    
    # 3. 9 ta ketma-ket raqam: 901234567
    m_plain = re.search(r'\b([389]\d|20|50|55|71|77|78)\d{7}\b', text)
    if m_plain:
        return f'+998{m_plain.group(0)}'
        
    return ""

CURRENT_KB = load_knowledge_base()

# Foydalanuvchilar suhbat tarixi va xotirani boshqarish
user_conversations: dict[int, list[genai_types.Content]] = {}
user_last_active: dict[int, float] = {}

def update_user_history(user_id: int, content: genai_types.Content):
    """Foydalanuvchi suhbat tarixini yangilash va xotirani avtomatik tozalash"""
    now = time.time()
    user_last_active[user_id] = now
    
    if user_id not in user_conversations:
        user_conversations[user_id] = []
        
    user_conversations[user_id].append(content)
    
    # Kontekst uzunligini me'yorda ushlash (oxirgi 12 ta xabar)
    if len(user_conversations[user_id]) > 12:
        user_conversations[user_id] = user_conversations[user_id][-12:]
        
    # Xotirada 200 dan ortiq foydalanuvchi yig'ilsa, 3 soatdan ortiq kirmaganlarni tozalash
    if len(user_conversations) > 200:
        cutoff = now - 10800  # 3 soat
        inactive = [uid for uid, t in user_last_active.items() if t < cutoff]
        for uid in inactive:
            user_conversations.pop(uid, None)
            user_last_active.pop(uid, None)

        # Agar hali ham 200 dan ortiq bo'lsa, eng eski faol foydalanuvchilarni o'chirish (LRU cap)
        if len(user_conversations) > 200:
            sorted_users = sorted(user_last_active.items(), key=lambda x: x[1])
            excess = len(user_conversations) - 200
            for uid, _ in sorted_users[:excess]:
                user_conversations.pop(uid, None)
                user_last_active.pop(uid, None)

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


# Gemini modellari uchun 503/429 cooldown monitoring
model_cooldowns: dict[str, float] = {}

async def ask_ai(contents) -> str:
    """Multi-LLM (ChatGPT + Gemini) ultra-tezkor, aqlli fallback va xatoliklarda avtomatik zaxiraga o'tish tizimi"""
    global openai_disabled_until
    now = time.time()
    system_instruction = get_system_instruction()

    # 1. AGAR OPENAI SOZLANGAN VA KREDITI BOR BO'LSA
    if openai_client and now > openai_disabled_until:
        try:
            openai_msgs = to_openai_messages(system_instruction, contents)
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    openai_client.chat.completions.create,
                    model="gpt-4o-mini",
                    messages=openai_msgs,
                    temperature=0.65,
                ),
                timeout=3.5
            )
            if response and response.choices and response.choices[0].message.content:
                return response.choices[0].message.content.strip()
        except Exception as e:
            openai_disabled_until = now + 86400  # 24 soatga o'tkazib yuborish
            logging.info(f"OpenAI o'tkazib yuborildi (Gemini ishlatiladi): {e}")

    # 2. GEMINI 3.8 FLASH (Asosiy) + GEMINI 3.5 FLASH LITE (Zaxira) + FLASH LITE LATEST
    if gemini_client:
        models_to_try = [
            ("gemini-3.8-flash", 4.0),
            ("gemini-3.5-flash-lite", 8.0),
            ("gemini-flash-lite-latest", 8.0)
        ]
        for model_name, tm in models_to_try:
            # Agar model 503/429 sababli cooldown da bo'lsa, uni o'tkazib yuboramiz
            if now < model_cooldowns.get(model_name, 0):
                continue

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        gemini_client.models.generate_content,
                        model=model_name,
                        contents=contents,
                        config=genai_types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.65,
                            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                        ),
                    ),
                    timeout=tm
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                err_msg = str(e)
                if "503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg:
                    model_cooldowns[model_name] = now + 300  # 5 daqiqa cooldown
                    logging.warning(f"Model {model_name} band yoki kvotasi tugagan (503/429). 5 daqiqa zaxira model ishlatiladi.")
                else:
                    logging.warning(f"Model {model_name} xatolik berdi: {e}. Keyingi zaxira modelga o'tilmoqda...")

    return "Assalomu alaykum! Maktabimiz haqida qiziqishingizdan xursandmiz. Farzandingiz nechanchi sinfga borishi yoki qaysi filialimiz haqida ma'lumot kerakligini aytsangiz, darhol yordam beraman! 😊"


async def safe_reply(message: types.Message, text: str):
    """Xavfsiz javob yuborish (uzun matnlarni avtomatik bo'laklab yuboradi)"""
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)] if len(text) > 4000 else [text]
    for chunk in chunks:
        try:
            await message.reply(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.reply(chunk, parse_mode=None)


async def safe_answer(message: types.Message, text: str, reply_markup=None):
    """Xavfsiz xabar yuborish (uzun matnlarni avtomatik bo'laklab yuboradi)"""
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)] if len(text) > 4000 else [text]
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        except Exception:
            await message.answer(chunk, parse_mode=None, reply_markup=markup)


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    """/start komandasi — Samimiy va professional kutib olish"""
    greeting = (
        "Assalomu alaykum! Men \"Yuksalish Maktabi\" ta'lim maslahatchisi **Aishaman**. 😊\n\n"
        "Farzandingizning ta'limi, maktabimizdagi sharoitlar, oylik to'lov yoki filiallarimiz bo'yicha har qanday savolingizga bajonidil yordam beraman.\n\n"
        "Farzandingiz nechanchi sinfga boradi yoki sizni qaysi filialimiz qiziqtiryapti?"
    )
    user_conversations[message.from_user.id] = []
    update_user_history(message.from_user.id, genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=greeting)]))
    await safe_answer(message, greeting)


# ------------------ ADMIN BO'LIMI (LEADLAR VA BAZA) ------------------

@dp.message(F.chat.type == "private", Command("leads"))
async def leads_handler(message: types.Message):
    """Ro'yxatdan o'tgan ota-onalar (Leadlar) ro'yxatini ko'rish (Faqat admin uchun)"""
    if not is_admin(message.from_user.id):
        await message.answer("⚠️ Bu buyruq faqat maktab ma'muriyati uchun ruxsat etilgan!")
        return

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
    """Bilimlar bazasini boshqarish bo'yicha yo'riqnoma (Faqat admin uchun)"""
    if not is_admin(message.from_user.id):
        await message.answer("⚠️ Bu buyruq faqat maktab ma'muriyati uchun ruxsat etilgan!")
        return

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
    """Bazani to'liq yangilash (Faqat admin uchun)"""
    if not is_admin(message.from_user.id):
        return

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
    """Mavjud bazaga yangi ma'lumot qo'shish (Faqat admin uchun)"""
    if not is_admin(message.from_user.id):
        return

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
    """Mavjud bazani ko'rish (Faqat admin uchun)"""
    if not is_admin(message.from_user.id):
        return

    text = f"📋 *Hozirgi bilimlar bazasi:*\n\n{CURRENT_KB}"
    await safe_answer(message, text)


# ------------------ TELEFON KONTAKTI YUBORILGANDA ------------------

@dp.message(F.chat.type == "private", F.contact)
async def contact_handler(message: types.Message):
    """Ota-ona kontakt tugmasini bosib raqam ulashganda"""
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    await save_lead(message.from_user, phone=phone, note="Telegram contact tugmasi orqali")
    asyncio.create_task(notify_admins(
        f"🔔 *Yangi ota-ona kontaktdan ro'yxatdan o'tdi!*\n\n"
        f"👤 Ism: {message.from_user.full_name}\n"
        f"📞 Tel: `{phone}`\n"
        f"💬 Telegram: @{message.from_user.username or 'mavjud emas'}\n"
        f"🕒 Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ))

    user_id = message.from_user.id
    update_user_history(user_id, genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=f"Telefon raqamim: {phone}")]))

    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_ai(user_conversations[user_id])
    update_user_history(user_id, genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=reply)]))

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
    is_mentioned = (message.text or message.caption) and bot_username.lower() in (message.text or message.caption or "").lower()

    if is_reply_to_bot or is_mentioned:
        raw_text = message.text or message.caption or ""
        clean_text = raw_text.replace(bot_username, "").strip()
        if not clean_text:
            clean_text = "Salom"

        # Agar guruhda ota-ona telefon raqam qoldirgan bo'lsa, uni ham lead sifatida saqlash va adminga bildirish
        detected_group_phone = extract_phone(clean_text)
        if detected_group_phone and message.from_user:
            await save_lead(message.from_user, phone=detected_group_phone, note=f"Guruhdan olindi ({message.chat.title or 'Guruh'}): {clean_text[:50]}")
            asyncio.create_task(notify_admins(
                f"🔔 *Yangi ota-ona ma'lumoti olindi (Guruhdan)!*\n\n"
                f"👤 Ism: {message.from_user.full_name}\n"
                f"📞 Tel: `{detected_group_phone}`\n"
                f"💬 Telegram: @{message.from_user.username or 'mavjud emas'}\n"
                f"👥 Guruh: {message.chat.title or message.chat.id}\n"
                f"🕒 Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ))

        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_ai(clean_text)
        await safe_reply(message, reply)


@dp.message(F.chat.type == "private")
async def private_message_handler(message: types.Message):
    """Lichkadagi xabarlar — Kontekstni saqlagan holda professional maslahat berish"""
    user_text = message.text or message.caption
    
    if not user_text or not user_text.strip():
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
        elif message.photo or message.document or message.video or message.audio:
            await safe_answer(
                message,
                "Faylingiz qabul qilindi! Maktabimiz yoki farzandingiz ta'limi bo'yicha qanday savollaringiz bor? Yozsangiz, bajonidil javob beraman! 😊"
            )
            return
        elif message.location:
            await safe_answer(
                message,
                "Lokatsiyangiz uchun rahmat! Sizga eng yaqin filialimizni aniqlash uchun: Samarqand darvoza, Uchtepa, Jizzax, Namangan yoki Olmaliq filiallarimizdan qaysi biri sizga qulayroq? 😊"
            )
            return
        else:
            await safe_answer(
                message,
                "Maktabimiz yoki farzandingiz ta'limi bo'yicha qanday savollaringiz bor? Bemalol yozib yuborishingiz mumkin! 😊"
            )
            return

    # /help yoki yordam so'ralganda
    if user_text.strip().lower() in ["/help", "help", "yordam"]:
        await safe_answer(
            message,
            "Assalomu alaykum! Men \"Yuksalish Maktabi\" ta'lim maslahatchisi **Aishaman**. 😊\n\n"
            "Sizga quyidagi masalalarda to'liq ma'lumot bera olaman:\n"
            "• Oylik to'lov (5.3 mln so'm) va stipendiyalar\n"
            "• Filiallar manzili (Toshkent, Jizzax, Namangan, Olmaliq)\n"
            "• 1-11 sinflarga qabul tartibi va imtihonlar\n"
            "• 40 xil taomli sog'lom ovqatlanish\n"
            "• STEM, to'garaklar va Muhammadali Eshonqulov tarbiya metodikasi\n\n"
            "Savolingizni shunchaki xabar sifatida yozsangiz kifoya!"
        )
        return

    # Admin kalit so'zlari bo'lsa o'tkazib yuborish
    if user_text.startswith("#"):
        return

    user_id = message.from_user.id

    # Telefon raqam mavjudligini tekshirish va avtomatik lead sifatida saqlash hamda adminga bildirish
    detected_phone = extract_phone(user_text)
    if detected_phone:
        await save_lead(message.from_user, phone=detected_phone, note=f"Xabardan olindi: {user_text[:50]}")
        asyncio.create_task(notify_admins(
            f"🔔 *Yangi ota-ona ma'lumoti olindi!*\n\n"
            f"👤 Ism: {message.from_user.full_name}\n"
            f"📞 Tel: `{detected_phone}`\n"
            f"💬 Telegram: @{message.from_user.username or 'mavjud emas'}\n"
            f"📝 Xabar: _{user_text[:100]}_\n"
            f"🕒 Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ))

    update_user_history(user_id, genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_text)]))

    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_ai(user_conversations[user_id])
    update_user_history(user_id, genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=reply)]))

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
