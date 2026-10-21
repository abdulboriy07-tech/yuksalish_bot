# Focus Club — Telegram Bot (Mijozlarga ma'lumot berish va To'lov qabul qilish)

Ushbu bot mijozlarga loyiha haqida ma'lumot berish, tariflar bilan tanishtirish, to'lov cheklarini qabul qilish va ularni administrator orqali tasdiqlash uchun yaratilgan.

## 🚀 Texnologiyalar
- **Python 3.14+**
- **aiogram 3.x** (Asinxron Telegram bot kutubxonasi)
- **aiosqlite** (Asinxron SQLite ma'lumotlar bazasi)
- **python-dotenv** (Konfiguratsiya fayli)

---

## 📂 Loyiha tuzilishi

```
telegram-bot/
├── .env                  # Bot tokeni, Admin ID va karta ma'lumotlari
├── .venv/                # Virtual muhit (kutubxonalar)
├── config.py             # Konfiguratsiya yuklovchi fayl
├── database.py           # SQLite ma'lumotlar bazasi bilan ishlash
├── keyboards.py          # Menyular va tugmalar
├── handlers/             # Bot buyruqlari va xabar ishlovchilari
│   ├── __init__.py
│   ├── user.py           # /start, Loyiha haqida, Bog'lanish, Buyurtmalarim
│   ├── payment.py        # Tariflar, Karta to'lovi, Chek qabul qilish
│   └── admin.py          # /admin, Buyurtmani tasdiqlash/rad etish, Statistika, Broadcast
├── main.py               # Botni ishga tushiruvchi asosiy fayl
└── requirements.txt      # Kutubxonalar ro'yxati
```

---

## ⚙️ Sozlash va Ishga tushirish

### 1. `.env` faylini sozlash
`.env` faylida quyidagi parametrlarni o'zingizga moslashtiring:
- `BOT_TOKEN`: Telegram bot tokeni (BotFather bergan token)
- `ADMIN_IDS`: Admin Telegram ID raqamlari (vergul bilan ajratilgan, masalan: `12345678,87654321`). O'z ID raqamingizni bilish uchun botga `/id` buyrug'ini yuboring.
- `CARD_NUMBER`: To'lov qabul qilinadigan karta raqami (masalan: `8600 1234 5678 9012`)
- `CARD_HOLDER`: Karta egasining ismi-familiyasi
- `SUPPORT_USERNAME`: Qo'llab-quvvatlash xizmati yoki menejer username'i

### 2. Botni ishga tushirish

Terminalda quyidagi buyruqni bering:
```bash
cd /Users/macbook/.gemini/antigravity/scratch/telegram-bot
source .venv/bin/activate
python main.py
```

---

## 💡 Bot qanday ishlaydi?

### Mijoz uchun:
1. **/start**: Bot xush kelibsiz xabari va asosiy menyuni chiqaradi.
2. **ℹ️ Loyiha haqida**: Loyiha maqsadi, afzalliklari va imkoniyatlari haqida batafsil ma'lumot.
3. **💳 Tariflar va To'lov**:
   - Mavjud tariflar ro'yxati chiqadi.
   - Mijoz tarifni tanlaganda to'lov kartasi, summa va ko'rsatmalar beriladi.
   - Mijoz to'lov chekining rasmini (skrinshot) yuboradi.
   - Bot chek qabul qilinganini xabar qiladi va administratorga ko'rib chiqish uchun yuboradi.
4. **👤 Mening buyurtmalarim**: Mijoz o'zining to'lovlari tarixi va holatini (Tasdiqlangan/Kutilmoqda/Rad etilgan) ko'rishi mumkin.
5. **📞 Bog'lanish**: Menejer bilan bog'lanish havolasi.

### Administrator uchun:
1. Yangi to'lov cheki yuborilganda adminga skrinshot va buyurtma ma'lumotlari bilan xabar keladi:
   - `[✅ Tasdiqlash]` tugmasi: bosilsa mijozga xushxabar boradi, bazada to'lov holati yangilanadi.
   - `[❌ Rad etish]` tugmasi: bosilsa mijozga rad etilganligi haqida xabar yuboriladi.
2. **/admin**:
   - 📊 **Statistika**: Jami foydalanuvchilar, buyurtmalar soni va jami tushum.
   - ⏳ **Kutilayotgan to'lovlar**: Ko'rib chiqilmagan cheklar ro'yxati.
   - 📢 **Broadcast**: Barcha foydalanuvchilarga ommaviy xabar yuborish.
