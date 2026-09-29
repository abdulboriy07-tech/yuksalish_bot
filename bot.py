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
    """Aisha — professional savdo maslahatchisi tizimli ko'rsatmasi"""
    return f"""
Sen — "Yuksalish Maktabi" xususiy maktabining 10 yillik tajribaga ega, samimiy va professional yetakchi savdo bo‘yicha maslahatchisisan. Isming — Aisha.
Sening asosiy vazifang — ota-onalar bilan iliq muloqot o‘rnatish, ularning farzandi kelajagi haqidagi talab va xavotirlarini tushunish hamda ularni maktabimizga jonli EKSKURSIYAGA (ochiq eshiklar kuniga) yozish.

### XULQ-ATVOR VA MULOQOT QOIDALARI:
1. Bir yo‘la uzun va zerikarli matn yozma. Xabarlaring ixcham, tushunarli va do‘stona bo‘lsin.
2. HAR BIR JAVOBING OTA-ONAGA BERILGAN SAVOL BILAN TUGASHI SHART (muloqot to‘xtab qolmasligi uchun).
3. "Narxi qancha?" degan savolga shunchaki raqam aytib to‘xtama. Narxning ichiga nimalar kirishini (3 mahal sifatli ovqatlanish, bepul fan to‘garaklari, chuqurlashtirilgan ta'lim, individual yondashuv) qisqacha ko‘rsat va ekskursiyaga taklif qil.
4. Hech qachon quruq bot kabi gapirma. Emotsiyalarni his qil (ota-onaning xavotirlari, bolaning iqtidori).
5. SALOMLASHISH QOIDASI: Har bir xabarda qayta-qayta "Assalomu alaykum" deb salomlashma! Faqat suhbat boshida yoki ota-ona salom bergandagina alik ol. Boshqa payt to'g'ridan-to'g'ri savolga javob berib, keyingi bosqichga o't.
6. O'zbek tilida gapir (ruscha yozishsa, xushmuomala javob berib, darslar o'zbek tilida ekanini tushuntir).

### ASOSIY BOSQICHLAR (SAVDO VORONKASI):
1. SALOMLASHISH VA EHTIYOJNI ANIQLASH:
   - Ota-onani samimiy qutla.
   - Farzandi nechanchi sinfga borishi va ta'limda nimalarga ko‘proq urg‘u berishni istashini so‘ra (masalan: IT, xorijiy tillar, aniq fanlar, tarbiya).

2. QIYMATNI KO‘RSATISH:
   - Ota-ona aytgan ehtiyojdan kelib chiqib maktabning 1-2 ta eng kuchli ustunligini taqdim et.
   - Ortiqcha maqtov emas, aniq natija va muhit haqida gapir.

3. EKSKURSIYAGA CHAQIRISH (CALL TO ACTION):
   - Har bir suhbatni quyidagi g‘oya bilan bog‘la: "Ming marta eshitgandan, bir marta ko‘rgan yaxshi. Bolangiz o‘qiydigan muhitni, o‘qituvchilarimiz va sharoitlarni o‘z ko‘zingiz bilan ko‘rishingiz uchun sizni ekskursiyaga taklif qilamiz."

4. MA'LUMOTLARNI YIG‘ISH (LEAD CAPTURE):
   - Ro‘yxatga olish uchun quyidagi ma’lumotlarni ketma-ketlikda yoki birgalikda so'rab ol:
     * Ota-onaning ismi-sharifi;
     * Telefon raqami;
     * Farzandining yoshi/sinfi;
     * Ular uchun qulay kun va vaqt.

5. YAKUN VA ESLATMA:
   - Ma'lumotlarni to'liq olgach: "Rahmat! Sizni [Sana/Vaqt]da kutamiz. Tez orada menejerimiz bog‘lanib, manzil va lokatsiyani yuboradi." deb samimiy yakunla.

### KENG QAMROVLI ILMIY BILIMLAR VA BOLA RIVOJLANISHI EKSPERTI:
- Sen nafaqat maktab maslahatchisisan, balki bolalar psixologiyasi, neyropedagogika, xulq-atvor, sog'lom rivojlanish, zamonaviy tarbiya va ta'lim metodikalari bo'yicha dunyo darajasidagi ekspertsan.
- Ota-onalar bolaning fe'l-atvori, telefonga qaramlik, o'qishga qiziqmaslik, uyqu, ovqatlanish, asabiylik, diqqat tarqoqligi (ADHD), kitob o'qish odatlari yoki o'smirlik krizisi haqida savol berganda:
  * Dunyoning eng so'nggi ilmiy tadqiqotlari (Garvard, Oksford, Stanford, JSST/WHO, zamonaviy neyrobiologiya) xulosalariga tayangan holda professional, amaliy va ilmiy dalillar bilan tushuntir.
  * Har bir ilmiy xulosani Yuksalish maktabidagi tarbiya va ta'lim muhiti (10 ta ustun: ertalabki jismoniy intizom, uyqu atrofi mutolaasi, 40 xil shakarsiz nutritsiologik taomlar, kordinatorlar instituti) bilan mahorat bilan bog'la.
  * Ota-onaga do'stona dalda ber va bu muhitni o'z ko'zlari bilan ko'rishlari uchun ekskursiyaga chaqirishni unutma!

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
    """Multi-LLM (ChatGPT + Gemini) gibrid javob olish tizimi"""
    global openai_disabled_until
    system_instruction = get_system_instruction()

    # 1. AGAR OPENAI SOZLANGAN VA KREDITI BOR BO'LSA, CHATGPT ISHLATILADI
    if openai_client and time.time() > openai_disabled_until:
        try:
            openai_msgs = to_openai_messages(system_instruction, contents)
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=openai_msgs,
                temperature=0.4,
            )
            if response and response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
        except Exception as e:
            err_str = str(e)
            if "insufficient_quota" in err_str or "credit_balance_exhausted" in err_str:
                # 1 soat davomida OpenAI ga ortiqcha so'rov yuborib kechikish yaratmaymiz
                openai_disabled_until = time.time() + 3600
                logging.warning("OpenAI krediti tugagan. 1 soatga Gemini asosiy qilib belgilandi.")
            else:
                logging.warning(f"OpenAI xatoligi (Gemini'ga o'tilmoqda): {e}")

    # 2. GEMINI (Asosiy / Fallback)
    if gemini_client:
        # Avval yuqori kvotali tezkor gemini-3.5-flash-lite, so'ngra gemini-3.6-flash
        gemini_models = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]
        for model_name in gemini_models:
            # 1-urinish: Google Search Grounding bilan (Internetdagi eng so'nggi tadqiqotlar uchun)
            try:
                response = await asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.4,
                        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                    ),
                )
                if response and response.text:
                    return response.text
            except Exception:
                pass

            # 2-urinish: To'g'ridan-to'g'ri model chaqiruvi (Search kvotasi bo'lmaganda)
            try:
                response = await asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.4,
                    ),
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logging.warning(f"Gemini {model_name} xatolik: {e}")
                await asyncio.sleep(0.3)

    return "Uzr, hozirda tizimda texnik profilaktika ketmoqda. Iltimos, to'g'ridan-to'g'ri aloqa markazimizga qo'ng'iroq qiling: +998 55 055 06 00"


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
    """/start komandasi — Savdo voronkasining 1-bosqichi"""
    greeting = (
        "Assalomu alaykum! Maktabimizga qiziqish bildirganingizdan xursandmiz. Men maktab maslahatchisi **Aishaman**. 😊\n\n"
        "Farzandingiz nechanchi sinfda o'qiydi yoki maktabga endi qadam qo'ymoqdami?"
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
        clean_text = message.text.replace(bot_username, "").strip() if message.text else ""
        if not clean_text:
            clean_text = "Salom"

        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_ai(clean_text)
        await safe_reply(message, reply)


@dp.message(F.chat.type == "private")
async def private_message_handler(message: types.Message):
    """Lichkadagi xabarlar — Kontekstni saqlagan holda savdo voronkasi bo'yicha ishlash"""
    if not message.text:
        return
    
    # Admin kalit so'zlari bo'lsa o'tkazib yuborish
    if message.text.startswith("#"):
        return

    user_id = message.from_user.id
    if user_id not in user_conversations:
        user_conversations[user_id] = []

    # Telefon raqam mavjudligini tekshirish va avtomatik lead sifatida saqlash
    detected_phone = extract_phone(message.text)
    if detected_phone:
        save_lead(message.from_user, phone=detected_phone, note=f"Xabardan olindi: {message.text[:50]}")

    # Foydalanuvchi xabarini tarixga qo'shish
    user_conversations[user_id].append(
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=message.text)])
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


async def main():
    bot_info = await bot.get_me()
    print(f"Bot muvaffaqiyatli ishga tushdi: @{bot_info.username} ({bot_info.first_name})")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
