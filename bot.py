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
Sen — "Yuksalish Maktabi" xususiy maktabining 10 yillik tajribaga ega, samimiy, ziyoli, madaniyatli va professional ta'lim maslahatchisisan. Isming — Aisha.
Sening vazifang — ota-onalar bilan go'yo ularning eng yaqin, mehridaryo va dono oilaviy maslahatchisi kabi muloqot qilish, ularning bolasi tarbiyasi va ta'limiga oid xavotirlariga yechim berish hamda maktab haqida aniq, ishonchli va qiymatli ma'lumot yetkazish.

### 🌟 MIJOZ BILAN CHATLASHISHNING OLTIN STANDARTLARI (0 DAN ISHLAB CHIQILGAN):

1. **SALOMLASHISH VA ODOB QOIDASI:**
   - Agar ota-ona "Assalomu alaykum" deb yozsa — javobingni qat'iy ravishda "Va alaykum assalom!" bilan boshla.
   - Agar "Salom" desa — "Assalomu alaykum!" yoki "Salom!" deb muloyim boshla.
   - Agar salomlashmasdan to'g'ridan-to'g'ri savol bersa yoki suhbat allaqachon davom etayotgan bo'lsa — har bir xabarda qayta-qayta salomlashma, to'g'ridan-to'g'ri javobga o't.

2. **SAVOLGA DARHOL ANIQ JAVOB VA QIYMATNI KO'RSATISH (VALUE FRAMING):**
   - Ota-ona nimani so'rasa, birinchi jumlada o'sha savolga aniq va lo'nda javob ber.
   - Narx so'ralganda (5 300 000 so'm), shunchaki quruq raqam aytib to'xtab qolma. Bu narx ichiga:
     • 08:30 dan 17:30 gacha to'liq kunlik xavfsiz va samimiy muhit;
     • 3 mahal nutritsiologik sog'lom ovqatlanish (shakar, margarin, palma yog'isiz);
     • Repetitorga hojat qoldirmaydigan chuqurlashtirilgan ta'lim va chet tillari;
     • Shanba kungi zamonaviy kasblar va STEM to'garaklari to'liq kiritilganini hamda a'lochilar uchun 45% gacha stipendiya borligini qisqa va tushunarli ko'rsat.

3. **UCHRASHUV YOKI EKSKURSIYAGA HADEB TAKLIF QILMASLIK (QAT'IY QOIDA):**
   - Har bir xabarda uchrashuvga, ekskursiyaga yoki ochiq eshiklar kuniga chaqirish QAT'IYAN TAQIQLANADI!
   - Uchrashuv taklifi FAQAT quyidagi 2 holatda berilishi mumkin:
     a) Ota-onaning o'zi: "Maktabni borib ko'rsak bo'ladimi?", "Qayerga borish kerak?", "Uchrashsak bo'ladimi?" deb so'raganda;
     b) Uzoq va samimiy suhbatdan so'ng, ota-ona qabulga yozilish niyatini bildirsa (joyi kelganda bir martagina muloyim taklif sifatida).
   - Narx, manzil, telefon, ovqatlanish kabi aniq savollarda UCHRASHUV MUTLAQO TAKLIF QILINMAYDI.

4. **E'TIROZLAR VA XAVOTIRLAR BILAN ISHLASH (OBJECTION HANDLING):**
   - **"Narx qimmat ekan":** Avval hamdardlik bildir ("Tushunaman, oilaviy byudjet uchun ta'lim xarajati jiddiy masala..."). So'ng oddiy maktabdagi bola uchun ham repetitorlar, 3 mahal ovqat va to'garaklar jami 3-4 mln dan oshib ketishini, Yuksalishda esa barchasi bir joyda, sifatli va 45% gacha stipendiya/chegirma imkoniyati borligini tushuntir.
   - **"Transportingiz yo'q ekan":** Darslar to'liq kun (08:30 dan 17:30 gacha) ekanini, ya'ni kun o'rtasida olib ketishga hojat yo'qligini, ertalab ishga ketishda tashlab, kechqurun qaytishda bemalol olib ketish qulayligini tushuntir.
   - **"Farzandim sho'x / darsga qiziqmaydi / telefon ko'p o'ynaydi":** Muhammadali Eshonqulovning tarbiya tizimini eslat: ertalabki yugurish orqali ortiqcha energiyani foydali yo'naltirish, "uyqu atrofi mutolaasi" bilan telefon qaramligidan xalos qilish, koordinatorlar va psixologlarimizning doimiy yakka tartibdagi e'tibori.
   - **"Siz botsiz-a?":** Samimiy va professional javob ber: "Men 'Yuksalish Maktabi' ta'lim maslahatchisi Aishaman 😊 Ota-onalarga 24/7 tezkor va aniq ma'lumot yetkazish uchun raqamli tizim orqali ham muloqot qilaman. Agar mutaxassisimiz shaxsan telefon orqali to'liq maslahat berishini istasangiz, raqamingizni qoldirsangiz, siz bilan bog'lanishadi!"
   - **"Samarqandda filial bormi?":** "Hozircha Samarqand shahrida filialimiz ochilmagan. Samarqandga eng yaqin filialimiz — Jizzax shahridagi filialimiz hisoblanadi. Agar Toshkent yoki Jizzax filiallarimiz sizga ma'qul kelsa, ular haqida ma'lumot berishim mumkin. Farzandingiz nechanchi sinfga boradi?"

5. **BITTA SAVOL QOIDASI (ONE GUIDING QUESTION):**
   - Har bir javobing oxirida KO'PI BILAN BITTA, samimiy va mantiqiy savol ber (mijozni birdaniga 2-3 ta savol bilan tergov qilma!).
   - "Yana qanday savollaringiz bor?" kabi zerikarli va quruq shablonlarni ishlatma.
   - Savollar suhbatni chuqurlashtirishi kerak: "Farzandingiz nechanchi sinfga boradi?", "Qaysi filialimiz manzili sizga qulayroq?", "Stipendiya imkoniyatlarimiz haqida batafsil ma'lumot beraymi?".

6. **SUHBATNI YAKUNLASH VA MINNATDORCHILIK:**
   - Ota-ona "Rahmat", "Tushundim", "Xo'p", "O'ylab ko'ramiz" desa — UNGA HECH QANDAY SAVOL BERMA va sotishga urinma!
   - Faqat ezgu tilak bildir: "Arzimaydi! O'ylab ko'ring, yana qanday savollaringiz bo'lsa, bemalol murojaat qiling. Farzandingizga o'qishlarida katta zafarlar tilayman! 😊".

7. **TELEFON RAQAM OLINGANDA (LEAD CAPTURE):**
   - Ota-ona telefon raqamini qoldirsa: "Katta rahmat! Telefon raqamingiz qabul qilindi. Tez orada mas'ul mutaxassisimiz siz bilan bog'lanib, barcha savollaringizga batafsil javob beradi va kerakli ma'lumotlarni yetkazadi 😊".

8. **TIL VA ALIFBO MOSLASHUVCHANLIGI:**
   - Ota-ona qaysi tilda yozsa, shu tilda javob ber (o'zbekcha bo'lsa — o'zbekcha, ruscha bo'lsa — ruscha).
   - O'zbek tilida krill alifbosida yozsa — krillda, lotinda yozsa — lotinda javob ber.

9. **🚫 QAT'IYAN TAQIQLANGAN IBORALAR:**
   - "Ming marta eshitgandan bir marta ko'rgan yaxshi" (BUTUNLAY TAQIQLANGAN!).
   - "Ajoyib yosh!", "Ajoyib tanlov!", "Zo'r sinf!" kabi sun'iy robot qoliplari bilan gap boshlash.
   - "kuchaytirroq", "qilishlik", "bo'lishlik" kabi g'aliz, sun'iy so'zlar.
   - Keraksiz rasmiyatchilik: "Sizga shuni ma'lum qilamizki", "Savollaringizga mamnuniyat bilan javob beramiz".

10. **ANIK FAKTLAR VA BAZA (MUHAMMADALI ESHONQULOV VA MAKTAB METODIKASI):**
   - Asoschi: Muhammadali Eshonqulov. Shior: "100 yilda bir keladigan buyuk inson sizsiz!".
   - Maqsad: Bolaning ichidagi tabiiy tug'ma qobiliyat (gavhar)ni ochish va ota-onaga, xalqiga manfaat keltiradigan buyuk shaxsiyat qilish. Farzandni mustaqil hayotga ("ota-onasizlikka", ya'ni mustaqil oyoqqa turishga) tayyorlash.
   - Nega ta'lim o'zbek tilida: Mustafo Cho'qay hikmati — "Inson ta'limni qaysi tilda olsa, o'sha til sohibi bo'lgan davlatga butun umr xizmat qiladi". Vatanparvar va millat manfaatini o'ylaydigan liderlarni tarbiyalash. Ilmni original manbasidan o'qish uchun esa ingliz, rus, koreys va arab tillari chuqurlashtiriladi.
   - Natijalar va IELTS: O'quvchilar repetitorsiz 7-9 sinfda IELTS 7.5-8.5 va SAT 1400-1500+ ballar olmoqda. 9-sinf oxirigacha bola IELTS masalasini yopishi shart (minimum 6.5-7.0), shunda 10-11 sinfda faqat xalqaro universitetlar grantlari ustida ishlaydi. 10-11 sinf a'lochilariga 50% gacha to'lov chegirmasi beriladi. Iqtidorli yoshlarga Muhammadali Eshonqulov nomidagi 100% grantlar bor.
   - Ovqatlanish: JSST (VOZ) va O'zbekiston SSV standartlari, bosh dietolog Mavjuda ustoz ishlab chiqqan yillik ratsion. 100% oq unsiz va shakarsiz, margarin va sun'iy qo'shimchalarsiz. 3 mahal: Nonushta, Tushlik, Tolmachoy (kechki oilaviy ovqatga ishtahasini bo'g'ib qo'ymaslik uchun tabiiy sog'lom pishiriq).
   - Telefon qaramligiga yechim: "Qancha vaqt kitob o'qisang, shuncha vaqt telefon seniki, bunga to'liq haqlisan!" qoidasi va "uyqu atrofi mutolaasi".
   - Filiallar: Samarqand darvoza (Toshkent), Uchtepa (Toshkent), Jizzax, Namangan, Olmaliq. (Samarqand shahrida filial yo'q, Samarqand darvoza Toshkentda!).
   - Aloqa: +998 55 055 06 00 (Olmaliq: +998 71 500 00 15).
   - Narx: 5 300 000 so'm / oy (08:30 dan 17:30 gacha to'liq kun, 3 mahal ovqat, repetitorsiz ta'lim, shanbalik to'garaklar kiritilgan).
   - Maktab transporti: Yo'q (ota-onalar o'zlari olib kelib-ketishadi).
   - Yotoqxona: Yo'q (ta'lim kunduzgi: 08:30 dan 17:30 gacha).

11. **🚫 DINIY ATAMALAR VA SO'ZLARNI ISHLATISH QAT'IYAN TAQIQLANADI (100% DUNYOVIY TA'LIM STANDARTI):**
   - Muloqotda HECH QANDAY diniy tushunchalar, atamalar yoki diniy so'zlar ishlatilmasin!
   - MUTLAQO TAQIQLANGAN SO'ZLAR: "Alloh", "Xudo", "Inshaalloh", "Mashaalloh", "Subhonalloh", "Alhamdulillah", "duo", "ehson", "halol", "harom", "savob", "gunoh", "hadis", "oyat", "namoz", "masjid", "islomiy", "diniy", "shayx", "ibodat".
   - Maktabimiz — O'zbekiston Respublikasi Maktabgacha va maktab ta'limi vazirligi litsenziyasiga ega bo'lgan ZAMONAVIY DUNYOVIY TA'LIM MUASSASASIDIR.
   - Barcha tushunchalar mutlaqo DUNYOVIY, ILMIY, PEDAGOGIK, HUQUQIY va INSONPARVARLIK nuqtayi nazaridan bayon qilinishi shart:
     • "Alloh bergan iqtidor" EMAS -> "tabiiy tug'ma salohiyat va qobiliyat";
     • "Alloh ko'rib turibdi" EMAS -> "yuksak vijdon, shaxsiy mas'uliyat va to'g'riso'zlik";
     • "duo olish" EMAS -> "ota-ona roziligi, mehri va oq fotihasini olish";
     • "ehson" EMAS -> "saxovat, mehr-oqibat, ko'ngillilik (volontyorlik) va ijtimoiy ko'mak";
     • "halol bo'lsin" EMAS -> "marhamat, sen bunga to'liq haqlisan / o'z mehnating bilan erishding".
   - AGAR OTA-ONA DINIY TA'LIM, NAMOZ YOKI SHARIAT HAQIDA SO'RASA:
     • "Maktabimiz — O'zbekiston Respublikasi Maktabgacha va maktab ta'limi vazirligi litsenziyasiga ega bo'lgan zamonaviy dunyoviy ta'lim muassasasi hisoblanadi. O'quv dasturimiz davlat ta'lim standartlari, zamonaviy fanlar, chet tillari va STEM yo'nalishlariga asoslangan bo'lib, o'quvchilarda yuksak insoniy fazilatlar, vatanparvarlik, qat'iyat va intizomni shakllantirishga qaratilgan." deb samimiy va muloyim javob ber.
     • Javobingda diniy atamalarni (namoz, diniy, ibodat kabilarni) hatto inkor shaklida ham aslo takrorlama!


### 💬 JONLI MULOQOT NAMUNALARI (FEW-SHOT):
---
1-namuna (Salomlashib narx so'ralganda):
Ota-ona: "Assalomu alaykum, o'qish narxi necha pul?"
Aisha: "Va alaykum assalom! Maktabimizda oylik to'lov 5 300 000 so'mni tashkil qiladi.

Bu to'lov ichiga 08:30 dan 17:30 gacha chuqurlashtirilgan ta'lim, 3 mahal sog'lom nutritsiologik ovqatlanish hamda shanba kungi bepul to'garaklar (robototexnika, IT, xorijiy tillar) to'liq kiritilgan. Shuningdek, a'lochi o'quvchilarimiz uchun har chorakda 45% gacha stipendiya (chegirma) imkoniyatlari ham bor.

Farzandingiz nechanchi sinfga boradi?"

---
2-namuna (Salom berib filial so'ralganda):
Ota-ona: "Salom, qaysi filiallar bor?"
Aisha: "Assalomu alaykum! Maktabimizning Toshkent shahrida 2 ta filiali mavjud: Samarqand darvoza va Uchtepa filiallari. Shuningdek, Jizzax, Namangan va Olmaliq shaharlarida ham filiallarimiz faoliyat yuritadi.

Sizga qaysi hududimiz ko'proq qulay?"

---
3-namuna (Narx qimmat deyilganda):
Ota-ona: "5.3 million qimmat ekan, arzonrog'i yo'qmi?"
Aisha: "Tushunaman, har bir ota-ona uchun ta'lim xarajatlarini rejalashtirish juda muhim.

Maktabimizda bola ertalab 08:30 dan 17:30 gacha to'liq nazoratda bo'ladi. Agar hisoblab ko'rsak, oddiy maktabdan so'ng repetitorlar, 3 mahal sifatli ovqatlanish va to'garaklar uchun ham oylik xarajatlar deyarli shuncha bo'ladi. Bizda esa barchasi bir joyda, xavfsiz muhitda va Muhammadali Eshonqulov tarbiya tizimi asosida beriladi. Bundan tashqari, 2 ta farzand uchun 5%, 3 ta farzand uchun 10% chegirma hamda har chorakda 45% gacha stipendiya imkoniyatimiz bor.

Maktabimizda bir nechta farzandingiz ta'lim olishini rejalashtiryapsizmi?"

---
4-namuna (Farzandi sho'xligi aytilganda):
Ota-ona: "O'g'lim juda sho'x, darsga qiziqmaydi, dars qildirishga qiynalyapmiz"
Aisha: "Xavotiringiz juda o'rinli, ko'p ota-onalar aynan shu masala bilan murojaat qilishadi. Aslida boladagi sho'xlik — uning katta salohiyati va ortiqcha energiyasidan darak beradi.

Bizning tarbiya tizimimizda (Muhammadali Eshonqulov metodikasi) bu energiya to'g'ri yo'naltiriladi: ertalabki jismoniy mashg'ulotlar orqali intizom shakllanadi, darslar harakatli va amaliy o'tiladi, telefon qaramligiga qarshi esa 'uyqu atrofi mutolaasi' joriy qilingan. Har bir sinfda alohida kordinator faqat bolaning ruhiyati va odoblari bilan ishlaydi.

O'g'lingiz nechanchi sinfda o'qiydi?"

---
5-namuna (Siz botsiz-a deyilganda):
Ota-ona: "Siz botsiz-a?"
Aisha: "Men 'Yuksalish Maktabi' ta'lim maslahatchisi Aishaman 😊 Ota-onalarimizga 24/7 tezkor va aniq ma'lumot berish uchun raqamli tizim orqali muloqot qilaman.

Agar mutaxassisimiz shaxsan telefon orqali barcha savollaringizga to'liq javob berishini istasangiz, telefon raqamingizni qoldirishingiz mumkin, darhol bog'lanamiz!"

---
6-namuna (Minnatdorchilik bildirilganda):
Ota-ona: "Rahmat, o'ylab ko'ramiz"
Aisha: "Arzimaydi! O'ylab ko'ring, yana qanday savollaringiz bo'lsa, bemalol murojaat qiling. Farzandingizning kelajagiga katta muvaffaqiyatlar tilayman! 😊"

---
7-namuna (Rus tilida so'ralganda):
Ota-ona: "Здравствуйте! Со скольки лет принимаете детей в школу?"
Aisha: "Здравствуйте! В 1-й класс мы принимаем детей с 6-7 лет на основе индивидуального собеседования с нашими педагогами и психологами.

Обучение ведется на узбекском языке с углубленным изучением русского и английского языков, а также современных STEM-дисциплин. 

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


def sanitize_secular_text(text: str) -> str:
    """Mijoz bilan yozishmalardan barcha diniy so'z va atamalarni 100% dunyoviy muqobillariga almashtirish"""
    if not text:
        return text

    phrases = [
        # Uzbek - Alloh bergan / ko'rib turibdi
        (r"(?i)\b(?:alloh|olloh|xudo)(?:im)?\s+bergan\s+(?:noyob\s+)?iqtidor\w*\b", "tabiiy tug'ma iqtidor"),
        (r"(?i)\b(?:alloh|olloh|xudo)(?:im)?\s+bergan\s+(?:noyob\s+)?qobiliyat\w*\b", "tabiiy tug'ma qobiliyat"),
        (r"(?i)\b(?:alloh|olloh|xudo)(?:im)?\s+ko'rib\s+turibdi\b", "yuksak vijdon, shaxsiy mas'uliyat va to'g'riso'zlik"),
        (r"(?i)\b(?:alloh|olloh)\s+rozi\s+bo'lsin\b", "Katta rahmat, minnatdormiz"),
        (r"(?i)\b(?:allohga|ollohga|xudoga)\s+shuk[ru]?\b", "Ming bor shukr"),
        
        # Uzbek - Duo
        (r"(?i)\bduo(?:lar)?\s+(?:olish\w*|olamiz|olishadi|olinsa)\b", "ota-ona roziligi va mehrini olish"),
        (r"(?i)\bduosini\s+olish\w*\b", "roziligini va mehrini olish"),
        (r"(?i)\bduo(?:lar)?\s+(?:qilish\w*|qilamiz|qilaylik|qilinsin)\b", "ezgu tilaklar bildirish"),
        (r"(?i)\bduo\s+qil\w*\b", "ezgu tilak bildir"),
        (r"(?i)\bduolar(?:imiz)?\b", "ezgu tilaklarimiz"),
        (r"(?i)\bduo\b", "ezgu tilak"),

        # Uzbek - Ehson
        (r"(?i)\behson\s+qutisi\b", "saxovat va mehr-oqibat qutisi"),
        (r"(?i)\behson\s+(?:qilish\w*|qilamiz|qilinadi)\b", "saxovat ko'rsatish va ko'ngillilik"),
        (r"(?i)\behson\w*\b", "saxovat va ko'mak"),

        # Uzbek - Halol / Harom
        (r"(?i)\bhalol\s+bo'lsin\b", "bunga to'liq haqlisan"),
        (r"(?i)\bhalol\s+va\s+toza\b", "sifatli, toza va xavfsiz"),
        (r"(?i)\bhalol\s+ovqat\w*\b", "sertifikatlangan toza va sog'lom ovqat"),
        (r"(?i)\bhalol\s+mahsulot\w*\b", "sifatli, toza mahsulot"),
        (r"(?i)\bhalol\s+mehnat\w*\b", "vijdonli, sidqidildan mehnat"),
        (r"(?i)\bhalol\w*\b", "toza, tabiiy va sifatli"),
        (r"(?i)\bharom\w*\b", "taqiqlangan va nomaqbul"),

        # Uzbek - Zikr / Ibora
        (r"(?i)\b(?:inshaalloh|insha\s+alloh|inshoolloh|in\s+sha\s+allah|xudo\s+xohlasa|xudo\s+buyursa)\b", "albatta"),
        (r"(?i)\b(?:mashaalloh|masha\s+alloh|mashalloh|mashaallah)\b", "ofarin"),
        (r"(?i)\b(?:subhonalloh|subhanallah)\b", "ajoyib"),
        (r"(?i)\b(?:alhamdulillah|alhamdullillah)\b", "shukronalik bilan"),

        # Uzbek - Savob / Gunoh / Hadis / Oyat
        (r"(?i)\bsavob\s+ish\w*\b", "ezgu va xayrli ish"),
        (r"(?i)\bsavob\w*\b", "ezgu va xayrli"),
        (r"(?i)\bgunoh\w*\b", "xato va noto'g'ri ish"),
        (r"(?i)\bhadis\w*\b", "hikmatli so'zlar"),
        (r"(?i)\boyat\w*\b", "ibratli hikmatlar"),

        # Uzbek - Namoz / Ibodat / Masjid
        (r"(?i)\bnamoz\s+o'qish\w*\b", "ma'naviy xotirjamlik"),
        (r"(?i)\bnamozxona\w*\b", "dam olish xonasi"),
        (r"(?i)\bnamoz\w*\b", "ma'naviy xotirjamlik"),
        (r"(?i)\bibodatxona\w*\b", "dam olish maskani"),
        (r"(?i)\bibodat\w*\b", "ma'naviy mashg'ulotlar"),
        (r"(?i)\bcho'lpon\s+ota\s+masjidi(?:\s+yonida)?\b", "Farhod bozori hududida"),
        (r"(?i)\bmasjid\w*\b", "hudud"),

        # Uzbek - Diniy / Islomiy / Shayx
        (r"(?i)\bdiniy\s+ta'lim\b", "dunyoviy ta'lim dasturlaridan tashqari maxsus ta'lim"),
        (r"(?i)\bdiniy\s+fanlar\w*\b", "umumta'lim fanlaridan tashqari alohida dasturlar"),
        (r"(?i)\bdiniy\w*\b", "dunyoviy ta'limdan tashqari"),
        (r"(?i)\bislomiy\w*\b", "an'anaviy axloqiy"),
        (r"(?i)\bshayx\w*\b", "ustoz"),
        (r"(?i)\b(?:alloh|olloh|xudo)(?:im)?\w*\b", "ezgu niyat"),

        # Russian
        (r"(?i)\b(?:иншааллах|иншаллах|иншалла)\b", "конечно"),
        (r"(?i)\b(?:машааллах|машАллах|машалла)\b", "прекрасно"),
        (r"(?i)\b(?:альхамдулиллях|альхамдулиллах)\b", "с благодарностью"),
        (r"(?i)\bхаляль\w*\b", "чистая, здоровая и сертифицированная"),
        (r"(?i)\bнамаз\w*\b", "отдых"),
        (r"(?i)\bмечеть\w*\b", "район"),
        (r"(?i)\bрелигиозн\w*\b", "светск"),
        (r"(?i)\bаллах\w*\b", "добро"),
        (r"(?i)\bбог\w*\b", "добро"),
    ]

    res = text
    for pattern, replacement in phrases:
        res = re.sub(pattern, replacement, res)

    # Tinish belgilari va ortiqcha bo'shliqlarni tartibga keltirish
    res = re.sub(r" +", " ", res)
    res = re.sub(r"!+", "!", res)
    res = re.sub(r"\n +", "\n", res)
    return res.strip()


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
                raw_answer = response.choices[0].message.content.strip()
                return sanitize_secular_text(raw_answer)
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
                    raw_answer = response.text.strip()
                    return sanitize_secular_text(raw_answer)
            except Exception as e:
                err_msg = str(e)
                if "503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg:
                    model_cooldowns[model_name] = now + 300  # 5 daqiqa cooldown
                    logging.warning(f"Model {model_name} band yoki kvotasi tugagan (503/429). 5 daqiqa zaxira model ishlatiladi.")
                else:
                    logging.warning(f"Model {model_name} xatolik berdi: {e}. Keyingi zaxira modelga o'tilmoqda...")

    fallback_text = "Assalomu alaykum! Maktabimiz haqida qiziqishingizdan xursandmiz. Farzandingiz nechanchi sinfga borishi yoki qaysi filialimiz haqida ma'lumot kerakligini aytsangiz, darhol yordam beraman! 😊"
    return sanitize_secular_text(fallback_text)



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
        "1. `/bolimlar` yoki `/intervyu` — Bo'lim rahbarlaridan ma'lumot yig'ish (O'quv, Moliya, Kadrlar, Koordinatorlar).\n"
        "2. `#baza_yangilash` — mavjud bazani butunlay yangi matn bilan almashtirish.\n"
        "*Foydalanish:* `#baza_yangilash [yangi ma'lumot matni]`\n\n"
        "3. `#baza_qoshish` — mavjud bazaga qo'shimcha yangi ma'lumot qo'shish.\n"
        "*Foydalanish:* `#baza_qoshish [qo'shiladigan ma'lumot]`\n\n"
        "4. `#baza_korish` — hozirgi bilimlar bazasini to'liq ko'rish.\n"
        "5. `/leads` — ro'yxatdan o'tgan ota-onalar telefon raqamlari va ma'lumotlarini ko'rish.\n"
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


# ------------------ MAKTAB BO'LIMLARI BILAN INTERVYU VA BAZANI BOYITISH ------------------

DEPARTMENTS = {
    "oquv": {
        "title": "📚 O'quv bo'limi",
        "keywords": [
            "#oquv", "/oquv", "#oquv_bolimi", "/oquv_bolimi",
            "o'quv bo'limi", "oquv bolimi", "o‘quv bo‘limi", "o'quv", "oquv"
        ],
        "questions_text": """
1. Dars dasturlari va darsliklar: 1–11 sinflarda qaysi davlat va xalqaro (Cambridge, Pearson, Oxford) darsliklardan foydalaniladi? Dars jadvali haftasiga necha kun va necha soatdan iborat?
2. Xorijiy tillar chuqurlashuvi: Ingliz tili qaysi sinfdan boshlanadi va haftasiga necha soat? Rus, koreys va arab tillari qanday darajada o'qitiladi?
3. 9-sinfda IELTS 6.5–7.0 talabi: O'quvchilar repetitorsiz bunday yuqori ballarga erishishi uchun qanday o'quv metodikasi qo'llaniladi?
4. Uyga vazifalar va repetitorsiz tizim: Uyga vazifalar maktabning o'zida kordinator va ustozlar nazoratida bajariladimi yoki uyga ham beriladimi?
5. Zamonaviy kasblar va STEM: Shanba kungi robototexnika, 3D modellashtirish, IT va dasturlash to'garaklarida o'quvchilar qanday amaliy loyihalar qilishadi?
6. Qabul imtihonlari mezonlari: 2–11 sinflar uchun 35 ta testdan o'tish ballari qancha? Agar bola yiqilsa, qayta topshirish imkoniyati bormi?
7. Olimpiadalar va xalqaro universitetlar: 10–11 sinf o'quvchilarining xalqaro grantlar va TOP universitetlarga kirish portfoliolarini tayyorlashda qanday amaliy yordam beriladi?
""",
        "welcome": (
            "📚 *ASSALOMU ALAYKUM, HURMATLI O'QUV BO'LIMI MAS'ULI!*\n\n"
            "Men — \"Yuksalish Maktabi\" sun'iy intellekt ta'lim maslahatchisi **Aishaman**. 😊\n"
            "Ota-onalarga maktabimiz ta'lim sifati, o'quv dasturlari va akademik yutuqlar haqida "
            "eng to'g'ri va to'liq ma'lumotlarni yetkazishim uchun sizning yo'nalishingizdagi quyidagi "
            "aniq ma'lumotlar menga juda zarur:\n\n"
            "1️⃣ *Dars dasturlari va darsliklar:* 1–11 sinflarda qaysi davlat va xalqaro (Cambridge, Pearson va h.k.) darsliklaridan foydalaniladi? Dars jadvali haftasiga necha kun va kuniga necha soat?\n"
            "2️⃣ *Xorijiy tillar chuqurlashuvi:* Ingliz tili qaysi sinfdan boshlanadi va haftasiga necha soat o'tiladi? Rus, koreys va arab tillari qanday darajada o'qitiladi?\n"
            "3️⃣ *IELTS 7.5–8.5 natijalari:* 7–9 sinf o'quvchilari repetitorsiz bunday natijalarga erishishi uchun qanday metodika qo'llaniladi? 9-sinf oxiridagi talab (6.5–7.0) qanday nazorat qilinadi?\n"
            "4️⃣ *Uyga vazifalar va repetitorsiz tizim:* Uyga vazifalar maktabning o'zida kordinator va ustozlar nazoratida bajariladimi yoki uyga ham beriladimi?\n"
            "5️⃣ *Zamonaviy kasblar va STEM:* Shanba kungi robototexnika, 3D modellashtirish, IT va dasturlash to'garaklarida bolalar aynan nimalarni o'rganishadi?\n"
            "6️⃣ *Qabul imtihonlari mezonlari:* 2–11 sinflar uchun 35 ta testdan o'tish ballari qancha? Agar bola yiqilsa, qayta topshirish imkoniyati bormi?\n"
            "7️⃣ *Xalqaro grantlar va TOP universitetlar:* 10–11 sinfda o'quvchilarning xalqaro grantlar va portfoliolarini tayyorlashda qanday amaliy yordam beriladi?\n\n"
            "✍️ *Iltimos, ushbu savollarga o'zingizga qulay tartibda — xoh bitta xabarda, xoh ketma-ket javob bering.* "
            "Men har bir javobingizni tahlil qilib, bilimlar bazamizga kiritib boraman!\n\n"
            "_(Suhbatni yakunlash uchun `/chiqish` yoki `#chiqish` deb yozishingiz mumkin)_"
        )
    },
    "moliya": {
        "title": "💰 Moliya bo'limi",
        "keywords": [
            "#moliya", "/moliya", "#moliya_bolimi", "/moliya_bolimi",
            "moliya bo'limi", "moliya bolimi", "moliya", "buxgalteriya"
        ],
        "questions_text": """
1. To'lov muddatlari va paketlari: Oylik to'lov 5 300 000 so'm. Agar ota-ona 1 chorak yoki butun yillik o'qish uchun oldindan to'lov qilsa, qo'shimcha chegirmalar bormi?
2. To'lov usullari va shartnoma: To'lov qaysi usullarda qabul qilinadi (Payme, Click, bank orqali hisob raqamga, terminal, naqd)? Shartnoma qayerda va qanday tartibda imzolanadi?
3. Narx ichiga kirmaydigan xarajatlar: 5.3 mln so'm ichiga ta'lim, 3 mahal ovqat va to'garaklar kiradi. Maktab formasi, darsliklar, yillik ekskursiyalar yoki xalqaro imtihonlar uchun alohida to'lov bormi?
4. Oila chegirmalari mexanizmi: Bir oiladan 2 ta farzand (5%) yoki 3 ta farzand (10%) o'qiganda chegirma har bir bolaga alohida qo'llaniladimi?
5. Choraklik stipendiyalar (45%, 35%, 25%, 15%): Stipendiya to'lovdan chegirma shaklida yechib beriladimi yoki kartaga pul ko'rinishida beriladimi? Bu chegirma qaysi oylik to'lovga nisbatan qo'llaniladi?
6. Qoldirilgan kunlar va ta'tillar: Agar bola betob bo'lib dars qoldirsa yoki yozgi/qishki ta'tillarda oylik to'lov qanday hisob-kitob qilinadi?
7. Muhammadali Eshonqulov nomidagi 100% grant: Ushbu grantni yutgan o'quvchi moliyaviy tomondan qaysi xarajatlardan to'liq ozod etiladi?
""",
        "welcome": (
            "💰 *ASSALOMU ALAYKUM, HURMATLI MOLIYA BO'LIMI MAS'ULI!*\n\n"
            "Men — \"Yuksalish Maktabi\" sun'iy intellekt ta'lim maslahatchisi **Aishaman**. 😊\n"
            "Ota-onalarimiz eng ko'p so'raydigan to'lov shartlari, shartnoma, chegirmalar va stipendiyalar "
            "bo'yicha to'liq va aniq tushuntirish bera olishim uchun menga quyidagi ma'lumotlar zarur:\n\n"
            "1️⃣ *To'lov muddatlari va paketlari:* Oylik to'lov 5 300 000 so'm. Agar ota-ona 1 chorak yoki butun yillik o'qish uchun oldindan to'lov qilsa, qo'shimcha chegirmalar bormi?\n"
            "2️⃣ *To'lov usullari va shartnoma:* To'lov qaysi usullarda qabul qilinadi (Payme, Click, bank orqali hisob raqamga, terminal, naqd)? Shartnoma qayerda va qanday tartibda imzolanadi?\n"
            "3️⃣ *Narx ichiga kirmaydigan xarajatlar:* 5.3 mln so'm ichiga ta'lim, 3 mahal ovqat va to'garaklar kiradi. Maktab formasi, darsliklar, yillik ekskursiyalar yoki xalqaro imtihonlar uchun alohida to'lov bormi?\n"
            "4️⃣ *Oila chegirmalari mexanizmi:* Bir oiladan 2 ta farzand (5%) yoki 3 ta farzand (10%) o'qiganda chegirma har bir bolaga alohida qo'llaniladimi?\n"
            "5️⃣ *Choraklik stipendiyalar (45%, 35%, 25%, 15%):* Stipendiya to'lovdan chegirma shaklida yechib beriladimi yoki kartaga pul ko'rinishida beriladimi? Bu chegirma qaysi oylik to'lovga nisbatan qo'llaniladi?\n"
            "6️⃣ *Qoldirilgan kunlar va ta'tillar:* Agar bola betob bo'lib dars qoldirsa yoki yozgi/qishki ta'tillarda oylik to'lov qanday hisob-kitob qilinadi?\n"
            "7️⃣ *Muhammadali Eshonqulov nomidagi 100% grant:* Ushbu grantni yutgan o'quvchi moliyaviy tomondan qaysi xarajatlardan to'liq ozod etiladi?\n\n"
            "✍️ *Iltimos, ushbu moliyaviy masalalarga o'zingizga qulay tarzda oydinlik kiritib bersangiz.* "
            "Ma'lumotlaringiz asosida bilimlar bazamizni yangilab boraman!\n\n"
            "_(Suhbatni yakunlash uchun `/chiqish` yoki `#chiqish` deb yozishingiz mumkin)_"
        )
    },
    "kadrlar": {
        "title": "👥 Kadrlar bo'limi",
        "keywords": [
            "#kadrlar", "/kadrlar", "#kadrlar_bolimi", "/kadrlar_bolimi",
            "kadrlar bo'limi", "kadrlar bolimi", "kadrlar", "hr", "#hr", "/hr"
        ],
        "questions_text": """
1. Ustozlarni saralash bosqichlari: Yangi o'qituvchilar ishga qabul qilinishida qanday bosqichlardan (test, ochiq dars, psixologik suhbat) o'tishadi? 1 ta o'ringa o'rtacha nechta nomzod to'g'ri keladi?
2. Malaka va sertifikatlar: Chet tili (ingliz, rus, arab, koreys) o'qituvchilarimizda IELTS (necha ball?), CELTA, TESOL, TKT yoki boshqa qanday sertifikatlar talab qilinadi?
3. Mahorat va tajriba: Boshlang'ich va yuqori sinf ustozlarimizning o'rtacha ish tajribasi necha yil? Ular orasida toifali, oliy toifali pedagoglar bormi?
4. Chet ellik (native speaker) mutaxassislar: Maktabimizda chet ellik o'qituvchilar faoliyat yuritadimi yoki xalqaro loyihalarga jalb etiladimi?
5. Ustozlar uchun ichki malaka oshirish: Maktab doirasida pedagoglarimiz uchun qanday ichki treninglar, psixologik seminarlar va Muhammadali Eshonqulov mahorat darslari tashkil etiladi?
6. Sinfdagi nisbat va shaxsiy e'tibor: Bitta sinfda o'quvchilar soni (maksimum 22–24 ta) va ustoz hamda kordinatorlarning har bir bolaga yakka tartibdagi e'tibori qanday ta'minlanadi?
""",
        "welcome": (
            "👥 *ASSALOMU ALAYKUM, HURMATLI KADRLAR BO'LIMI MAS'ULI!*\n\n"
            "Men — \"Yuksalish Maktabi\" sun'iy intellekt ta'lim maslahatchisi **Aishaman**. 😊\n"
            "Ota-onalarimizga ustozlarimizning malakasi, saralash mezonlari va pedagogik jamoamizning "
            "kuchi haqida to'liq ishonch bilan ma'lumot berishim uchun quyidagi savollarim bor:\n\n"
            "1️⃣ *Ustozlarni saralash bosqichlari:* Yangi o'qituvchilar ishga qabul qilinishida qanday bosqichlardan (test, ochiq dars, psixologik suhbat) o'tishadi? 1 ta o'ringa o'rtacha nechta nomzod to'g'ri keladi?\n"
            "2️⃣ *Malaka va sertifikatlar:* Chet tili (ingliz, rus, arab, koreys) o'qituvchilarimizda IELTS (necha ball?), CELTA, TESOL, TKT yoki boshqa qanday sertifikatlar talab qilinadi?\n"
            "3️⃣ *Mahorat va tajriba:* Boshlang'ich va yuqori sinf ustozlarimizning o'rtacha ish tajribasi necha yil? Ular orasida toifali, oliy toifali pedagoglar bormi?\n"
            "4️⃣ *Chet ellik (native speaker) mutaxassislar:* Maktabimizda chet ellik o'qituvchilar faoliyat yuritadimi yoki xalqaro loyihalarga jalb etiladimi?\n"
            "5️⃣ *Ustozlar uchun ichki malaka oshirish:* Maktab doirasida pedagoglarimiz uchun qanday ichki treninglar, psixologik seminarlar va Muhammadali Eshonqulov mahorat darslari tashkil etiladi?\n"
            "6️⃣ *Sinfdagi nisbat va shaxsiy e'tibor:* Bitta sinfda o'quvchilar soni (maksimum 22–24 ta) va ustoz hamda kordinatorlarning har bir bolaga yakka tartibdagi e'tibori qanday ta'minlanadi?\n\n"
            "✍️ *Iltimos, ushbu ma'lumotlarni qulay shaklda yozib yuborsangiz.* "
            "Har bir faktni bilimlar bazamizga kiritib, ota-onalarga faxr bilan yetkazaman!\n\n"
            "_(Suhbatni yakunlash uchun `/chiqish` yoki `#chiqish` deb yozishingiz mumkin)_"
        )
    },
    "koordinatorlar": {
        "title": "🎯 Koordinatorlar bo'limi",
        "keywords": [
            "#koordinatorlar", "/koordinatorlar", "#koordinator", "/koordinator",
            "#koordinatorlar_bolimi", "/koordinatorlar_bolimi",
            "koordinatorlar bo'limi", "koordinatorlar bolimi", "koordinator", "koordinatorlar"
        ],
        "questions_text": """
1. Koordinatorning bir kunlik faoliyati: Koordinator ertalab 08:30 dan kechki 17:30 gacha sinf bilan qanday ish olib boradi? Uning fan o'qituvchisidan asosiy farqi nimada?
2. Ertalabki intizom va yugurish monitoringi: Bolalarning quyosh chiqishidan oldin uyg'onishi, xonasini yig'ishtirishi va 1–6 km yugurib video-hisobot yuborishi amalda qanday tekshiriladi va rag'batlantiriladi?
3. Telefon qaramligiga qarshi amaliyot: "Qancha kitob o'qisang, shuncha vaqt telefon seniki" va "uyqu atrofi mutolaasi" (yiliga 50 ta kitob) qanday nazorat qilinadi? Kitob o'qilgani qanday tekshiriladi?
4. Ota-ona bilan aloqa va hisobot: Koordinator ota-onaga bolaning xulqi, kayfiyati, darsdagi faolligi haqida qanday va qaysi muddatda (kunlik, haftalik) hisobot berib boradi?
5. Do'stona muhit va nizolarni hal qilish: Sinfda bolalar o'rtasida tushunmovchilik bo'lsa yoki biron bola jamoaga moslasha olmasa, koordinator va maktab psixologi buni qanday hal qiladi?
6. Mehr-saxovat qutisi va ko'ngillilik: Har oy sinf bilan ehtiyojmand oilalarga oziq-ovqat yetkazish amaliyoti amalda qanday tashkil etiladi? Bolalar bu jarayonda qanday qatnashadi?
7. Ovqatlanish madaniyati: 3 mahal sog'lom ovqatlanish paytida koordinator bolalarda to'g'ri taomlanish va stol odoblarini qanday shakllantiradi?
""",
        "welcome": (
            "🎯 *ASSALOMU ALAYKUM, HURMATLI KOORDINATORLAR BO'LIMI MAS'ULI!*\n\n"
            "Men — \"Yuksalish Maktabi\" sun'iy intellekt ta'lim maslahatchisi **Aishaman**. 😊\n"
            "Maktabimizning eng katta o'ziga xosligi va ustunligi — bu tarbiya tizimi va koordinatorlar institutidir. "
            "Ota-onalarga bu tizim qanday amaliy ishlashini to'liq tushuntirishim uchun quyidagi savollarim bor:\n\n"
            "1️⃣ *Koordinatorning bir kunlik faoliyati:* Koordinator ertalab 08:30 dan kechki 17:30 gacha sinf bilan qanday ish olib boradi? Uning fan o'qituvchisidan asosiy farqi nimada?\n"
            "2️⃣ *Ertalabki intizom va yugurish monitoringi:* Bolalarning quyosh chiqishidan oldin uyg'onishi, xonasini yig'ishtirishi va 1–6 km yugurib video-hisobot yuborishi amalda qanday tekshiriladi va rag'batlantiriladi?\n"
            "3️⃣ *Telefon qaramligiga qarshi amaliyot:* \"Qancha kitob o'qisang, shuncha vaqt telefon seniki\" va \"uyqu atrofi mutolaasi\" (yiliga 50 ta kitob) qanday nazorat qilinadi? Kitob o'qilgani qanday tekshiriladi?\n"
            "4️⃣ *Ota-ona bilan aloqa va hisobot:* Koordinator ota-onaga bolaning xulqi, kayfiyati, darsdagi faolligi haqida qanday va qaysi muddatda (kunlik, haftalik) hisobot berib boradi?\n"
            "5️⃣ *Do'stona muhit va nizolarni hal qilish:* Sinfda bolalar o'rtasida tushunmovchilik bo'lsa yoki biron bola jamoaga moslasha olmasa, koordinator va maktab psixologi buni qanday hal qiladi?\n"
            "6️⃣ *Mehr-saxovat qutisi va ko'ngillilik:* Har oy sinf bilan ehtiyojmand oilalarga oziq-ovqat yetkazish amaliyoti amalda qanday tashkil etiladi? Bolalar bu jarayonda qanday qatnashadi?\n"
            "7️⃣ *Ovqatlanish madaniyati:* 3 mahal sog'lom ovqatlanish paytida koordinator bolalarda to'g'ri taomlanish va stol odoblarini qanday shakllantiradi?\n\n"
            "✍️ *Iltimos, ushbu tarbiya amaliyotlari bo'yicha batafsil ma'lumot bersangiz.* "
            "Har bir javobingizni bilimlar bazamizga kiritib, mustahkamlab boraman!\n\n"
            "_(Suhbatni yakunlash uchun `/chiqish` yoki `#chiqish` deb yozishingiz mumkin)_"
        )
    }
}

user_interview_sessions: dict[int, dict] = {}

def get_departments_keyboard() -> types.InlineKeyboardMarkup:
    """Bo'limlarni tanlash uchun inline tugmalar"""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="📚 O'quv bo'limi", callback_data="dept_oquv"),
                types.InlineKeyboardButton(text="💰 Moliya bo'limi", callback_data="dept_moliya")
            ],
            [
                types.InlineKeyboardButton(text="👥 Kadrlar bo'limi", callback_data="dept_kadrlar"),
                types.InlineKeyboardButton(text="🎯 Koordinatorlar bo'limi", callback_data="dept_koordinatorlar")
            ]
        ]
    )

def get_exit_keyboard() -> types.InlineKeyboardMarkup:
    """Suhbatni yakunlash tugmasi"""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🚪 Suhbatni yakunlash", callback_data="dept_exit")]
        ]
    )

def check_dept_keyword(text: str) -> tuple[str | None, str]:
    """
    Xabardan bo'lim kalit so'zini aniqlaydi.
    Qaytaradi: (dept_key, remaining_text)
    """
    if not text:
        return None, ""
    cleaned = text.strip()
    lowered = cleaned.lower()
    
    for dept_key, d_data in DEPARTMENTS.items():
        for kw in d_data["keywords"]:
            kw_lower = kw.lower()
            if lowered == kw_lower:
                return dept_key, ""
            if lowered.startswith(kw_lower + " ") or lowered.startswith(kw_lower + "\n") or lowered.startswith(kw_lower + ":"):
                remaining = cleaned[len(kw):].lstrip(" :\n")
                return dept_key, remaining
    return None, ""

async def process_department_feedback(dept_key: str, user_text: str, user_name: str) -> str:
    """Bo'lim mas'uli bergan ma'lumotlarni tahlil qilish, bazaga qo'shish va keyingi savollarni berish"""
    global CURRENT_KB
    dept = DEPARTMENTS[dept_key]
    dept_title = dept["title"]
    
    analysis_prompt = f"""
Siz — 'Yuksalish Maktabi'ning sun'iy intellekt ta'lim maslahatchisi Aisha uchun bilimlar bazasini boyituvchi intellektual tahlilchisiz.
Maktabning {dept_title} mas'uli/rahbari ({user_name}) quyidagi ma'lumotlarni taqdim etdi:
---
{user_text}
---

Ushbu bo'limning asosiy savollari:
{dept["questions_text"]}

Vazifangiz:
1. Taqdim etilgan ma'lumotlardan eng muhim faktlarni ajratib oling va bilimlar bazasi (knowledge base) uchun qisqa, aniq va tizimli formatga (faktlar ro'yxati ko'rinishida) keltiring.
2. Rahbarga samimiy minnatdorchilik bildiring va qabul qilingan ma'lumotlar xulosasini ko'rsating.
3. Yuqoridagi savollardan qaysilari hali yoritilmagan bo'lsa, ulardan 1-2 tasini muloyimlik bilan qo'shimcha so'rang.
4. Suhbatni yakunlash uchun /chiqish komandasini eslating.
5. QAT'IY QOIDA: Diniy atamalar mutlaqo ishlatilmasin (100% dunyoviy til).
"""
    extracted_summary = ""
    try:
        if gemini_client:
            resp = await asyncio.wait_for(
                asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model="gemini-3.8-flash",
                    contents=analysis_prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.4,
                        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                    )
                ),
                timeout=10.0
            )
            if resp and resp.text:
                extracted_summary = resp.text.strip()
    except Exception as e:
        logging.warning(f"Department AI analysis primary error: {e}")
        try:
            if gemini_client:
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        gemini_client.models.generate_content,
                        model="gemini-3.5-flash-lite",
                        contents=analysis_prompt,
                        config=genai_types.GenerateContentConfig(
                            temperature=0.4,
                            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True)
                        )
                    ),
                    timeout=10.0
                )
                if resp and resp.text:
                    extracted_summary = resp.text.strip()
        except Exception as e2:
            logging.error(f"Department AI fallback error: {e2}")

    if not extracted_summary:
        extracted_summary = (
            f"Katta rahmat! {dept_title} bo'yicha bergan ma'lumotlaringiz muvaffaqiyatli qabul qilindi va bilimlar bazamizga kiritildi.\n\n"
            "Yana qo'shimcha ma'lumotlaringiz bo'lsa, bemalol yozishingiz mumkin. Suhbatni yakunlash uchun esa /chiqish deb yozing."
        )

    # Diniy so'zlardan tozalash
    extracted_summary = sanitize_secular_text(extracted_summary)

    # Bilimlar bazasiga avtomatik qo'shish
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    kb_entry = f"\n\n• {dept_title.upper()} QO'SHIMCHA MA'LUMOTLARI ({timestamp}):\n  - {user_text.strip()}"
    new_kb = CURRENT_KB + kb_entry
    save_knowledge_base(new_kb)
    CURRENT_KB = new_kb

    # Arxiv log fayliga yozish
    try:
        archive_path = os.path.join(BASE_DIR, "transcripts", "department_interviews.jsonl")
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        with open(archive_path, "a", encoding="utf-8") as f:
            log_item = {
                "department": dept_key,
                "user_name": user_name,
                "raw_text": user_text,
                "created_at": timestamp
            }
            f.write(json.dumps(log_item, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.warning(f"Archive write error: {e}")

    return extracted_summary


@dp.message(F.chat.type == "private", Command(commands=["intervyu", "bolimlar"]))
async def departments_command_handler(message: types.Message):
    """Bo'limlar ro'yxati va intervyu menyusi"""
    menu_text = (
        "🏛 *'Yuksalish Maktabi' bo'limlari bilan bilimlar bazasini mustahkamlash markazi*\n\n"
        "Siz maktabimiz bo'limlari rahbarlari bilan suhbat o'tkazib, bot bilimlar bazasini kengaytirishingiz mumkin.\n\n"
        "Quyidagi bo'limlardan birini tanlang yoki kalit so'zni yuboring:\n"
        "• `#oquv` yoki `/oquv` — O'quv bo'limi\n"
        "• `#moliya` yoki `/moliya` — Moliya bo'limi\n"
        "• `#kadrlar` yoki `/kadrlar` — Kadrlar bo'limi\n"
        "• `#koordinatorlar` yoki `/koordinatorlar` — Koordinatorlar bo'limi\n\n"
        "Kerakli bo'limni tanlaganingizda, Aisha o'sha sohaning eng muhim savollarini beradi va olingan javoblarni avtomatik o'rganadi!"
    )
    await safe_answer(message, menu_text, reply_markup=get_departments_keyboard())


@dp.callback_query(F.data.startswith("dept_"))
async def dept_callback_handler(callback: types.CallbackQuery):
    """Inline tugma orqali bo'lim tanlanganda yoki chiqish bosilganda"""
    if callback.data == "dept_exit":
        user_id = callback.from_user.id
        if user_id in user_interview_sessions:
            dept_key = user_interview_sessions.pop(user_id)["dept"]
            dept_title = DEPARTMENTS[dept_key]["title"]
            await callback.message.answer(
                f"✅ *{dept_title} bo'yicha suhbat yakunlandi!*\n\n"
                "Taqdim etilgan barcha qimmatli ma'lumotlar bilimlar bazamizga saqlandi. "
                "Endi ota-onalarga beriladigan javoblarda ushbu ma'lumotlardan to'liq foydalanaman. Katta rahmat! 😊"
            )
        else:
            await callback.message.answer("Suhbat allaqachon yakunlangan.")
        await callback.answer()
        return

    dept_key = callback.data.replace("dept_", "")
    if dept_key in DEPARTMENTS:
        user_id = callback.from_user.id
        user_interview_sessions[user_id] = {
            "dept": dept_key,
            "started_at": datetime.now().isoformat(),
            "answers_count": 0
        }
        dept_info = DEPARTMENTS[dept_key]
        await callback.message.answer(
            dept_info["welcome"],
            reply_markup=get_exit_keyboard()
        )
    await callback.answer()


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

    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Mas'ul"

    # 1. /bolimlar yoki /intervyu so'ralganda
    if user_text.strip().lower() in ["/bolimlar", "/intervyu", "#bolimlar", "#intervyu", "bolimlar", "intervyu", "bo'limlar"]:
        menu_text = (
            "🏛 *'Yuksalish Maktabi' bo'limlari bilan bilimlar bazasini mustahkamlash markazi*\n\n"
            "Siz maktabimiz bo'limlari rahbarlari bilan suhbat o'tkazib, bot bilimlar bazasini kengaytirishingiz mumkin.\n\n"
            "Quyidagi bo'limlardan birini tanlang yoki kalit so'zni yuboring:\n"
            "• `#oquv` yoki `/oquv` — O'quv bo'limi\n"
            "• `#moliya` yoki `/moliya` — Moliya bo'limi\n"
            "• `#kadrlar` yoki `/kadrlar` — Kadrlar bo'limi\n"
            "• `#koordinatorlar` yoki `/koordinatorlar` — Koordinatorlar bo'limi\n\n"
            "Kerakli bo'limni tanlaganingizda, Aisha o'sha sohaning eng muhim savollarini beradi va olingan javoblarni avtomatik o'rganadi!"
        )
        await safe_answer(message, menu_text, reply_markup=get_departments_keyboard())
        return

    # 2. Agar foydalanuvchi hozirda bo'lim intervyusi rejimida bo'lsa
    if user_id in user_interview_sessions:
        # Chiqish so'zi bo'lsa
        if user_text.strip().lower() in ["/chiqish", "#chiqish", "chiqish", "tamom", "tugatish", "stop", "exit"]:
            dept_key = user_interview_sessions.pop(user_id)["dept"]
            dept_title = DEPARTMENTS[dept_key]["title"]
            await safe_answer(
                message,
                f"✅ *{dept_title} bo'yicha suhbat yakunlandi!*\n\n"
                "Taqdim etilgan barcha qimmatli ma'lumotlar bilimlar bazamizga saqlandi. "
                "Endi ota-onalarga beriladigan javoblarda ushbu ma'lumotlardan to'liq foydalanaman. Katta rahmat! 😊"
            )
            return

        # Bo'lim rahbari ma'lumot yubordi
        await bot.send_chat_action(message.chat.id, "typing")
        sess = user_interview_sessions[user_id]
        sess["answers_count"] = sess.get("answers_count", 0) + 1
        reply = await process_department_feedback(sess["dept"], user_text, user_name)
        await safe_answer(message, reply, reply_markup=get_exit_keyboard())
        return

    # 3. Yangi bo'lim kalit so'zi kiritilgan bo'lsa
    dept_key, remaining_text = check_dept_keyword(user_text)
    if dept_key:
        user_interview_sessions[user_id] = {
            "dept": dept_key,
            "started_at": datetime.now().isoformat(),
            "answers_count": 0
        }
        dept_info = DEPARTMENTS[dept_key]
        await safe_answer(message, dept_info["welcome"], reply_markup=get_exit_keyboard())
        
        # Agar kalit so'z bilan birga darhol ma'lumot ham yozilgan bo'lsa
        if remaining_text:
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await process_department_feedback(dept_key, remaining_text, user_name)
            await safe_answer(message, reply, reply_markup=get_exit_keyboard())
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
            "• 100% sog'lom nutritsiologik ovqatlanish\n"
            "• STEM, to'garaklar va Muhammadali Eshonqulov tarbiya metodikasi\n\n"
            "🏢 *Maktab bo'limlari bilan ishlash:* `/bolimlar` yoki `#oquv`, `#moliya`, `#kadrlar`, `#koordinatorlar`\n\n"
            "Savolingizni shunchaki xabar sifatida yozsangiz kifoya!"
        )
        return

    # Admin kalit so'zlari bo'lsa o'tkazib yuborish
    if user_text.startswith("#"):
        return


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
