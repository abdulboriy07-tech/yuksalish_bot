import base64
import html
from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS, CARD_NUMBER, CARD_HOLDER, PROJECT_NAME, SUPPORT_USERNAME, SITE_URL
from database import add_user, get_tariffs, get_tariff
from keyboards import (
    get_main_keyboard,
    get_welcome_inline_keyboard,
    get_site_inline_keyboard,
    get_admin_contact_inline_keyboard,
    get_cancel_keyboard
)
from handlers.payment import PaymentStates

router = Router()


def decode_start_payload(args: str):
    """Decode p_<base64url> payload: either phone|name or tariff_id|phone|name"""
    try:
        raw_b64 = args[2:]
        padding = '=' * (-len(raw_b64) % 4)
        decoded = base64.urlsafe_b64decode(raw_b64 + padding).decode('utf-8', errors='ignore')
        parts = decoded.split('|')
        if len(parts) >= 3:
            tariff_id = int(parts[0]) if parts[0].isdigit() else 1
            phone = parts[1].strip()
            name = "|".join(parts[2:]).strip()
            return tariff_id, phone, name
        elif len(parts) == 2:
            phone = parts[0].strip()
            name = parts[1].strip()
            return 1, phone, name
    except Exception as e:
        print(f"Error decoding start payload: {e}")
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject = None):
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Foydalanuvchi"
    username = message.from_user.username

    is_admin = user_id in ADMIN_IDS
    args = (command.args or "").strip() if command else ""

    # 1. If user came from website "To'lov qilish" button with encoded info (?start=p_...)
    if args.startswith("p_"):
        parsed = decode_start_payload(args)
        if parsed:
            tariff_id, phone, web_name = parsed
            final_name = web_name or full_name
            await add_user(user_id=user_id, full_name=final_name, username=username, phone=phone)

            tariff = await get_tariff(tariff_id)
            if not tariff:
                tariffs = await get_tariffs()
                tariff = tariffs[0] if tariffs else None

            is_promo = (tariff_id == 2)
            t_id = tariff["id"] if tariff else (2 if is_promo else 1)
            amount = 483000 if is_promo else (tariff["price"] if tariff else 690000)
            price_str = f"{amount:,}".replace(",", " ")

            await add_user(
                user_id=user_id,
                full_name=final_name,
                username=username,
                phone=phone,
                pending_tariff_id=t_id,
                pending_amount=amount
            )

            display_name = html.escape(final_name)
            display_phone = html.escape(phone or "Kiritilmagan")

            # Put user directly into waiting_for_receipt state!
            await state.update_data(
                tariff_id=t_id,
                amount=amount,
                web_name=final_name,
                phone=phone,
                promo_code="SUPER40" if is_promo else ""
            )
            await state.set_state(PaymentStates.waiting_for_receipt)

            await message.answer(
                f"👋 Xush kelibsiz, <b>{display_name}</b>! Saytdan kiritgan ma'lumotlaringiz qabul qilindi.",
                reply_markup=get_main_keyboard(is_admin=is_admin),
                parse_mode="HTML"
            )

            promo_line = (
                f"🎟 <b>Promokod:</b> <code>SUPER40</code> (30% chegirma qo'llandi)\n"
                if is_promo else ""
            )
            price_line = (
                f"💰 <b>To'lov summasi:</b> <s>690 000</s> <b>{price_str} so'm</b>\n\n"
                if is_promo else
                f"💰 <b>To'lov summasi:</b> <b>{price_str} so'm</b>\n\n"
            )

            payment_text = (
                f"📋 <b>Sizning ma'lumotlaringiz:</b>\n"
                f"👤 <b>Ism-familiya:</b> {display_name}\n"
                f"📞 <b>Telefon raqam:</b> <code>{display_phone}</code>\n"
                f"📦 <b>Dastur:</b> FOCUS 21 (21 kunlik to'liq dastur)\n"
                f"{promo_line}"
                f"📅 <b>Start:</b> 1-noyabr, 2026\n"
                f"{price_line}"
                f"────────────────────\n"
                f"💳 <b>To'lov uchun karta raqami:</b>\n"
                f"<code>{CARD_NUMBER}</code>\n"
                f"👤 <b>Karta egasi:</b> {html.escape(CARD_HOLDER)}\n"
                f"────────────────────\n\n"
                f"📸 <b>To'lovni amalga oshirib, to'lov chekini (skrinshot yoki rasm) shu yerga yuboring!</b>\n\n"
                f"<i>(Karta raqami ustiga bossangiz avtomatik nusxalanadi)</i>"
            )
            await message.answer(
                payment_text,
                reply_markup=get_cancel_keyboard(),
                parse_mode="HTML"
            )
            return

    await add_user(user_id=user_id, full_name=full_name, username=username)

    # 2. Standard /start: refresh bottom reply keyboard (removes old WebApp icon) and send welcome text + inline browser link button
    await message.answer(
        "🎯 <b>FOCUS 21</b>",
        reply_markup=get_main_keyboard(is_admin=is_admin),
        parse_mode="HTML"
    )
    await message.answer(
        WELCOME_TEXT,
        reply_markup=get_welcome_inline_keyboard(),
        parse_mode="HTML"
    )


WELCOME_TEXT = (
    "\"Tez bormoqchi bo'lsang — yolg'iz yur, uzoqqa bormoqchi bo'lsang — jamoa bilan harakatlan\" degan maqol bor edi.\n\n"
    "Ustozlarimizdan odatlarni shakllantirish foydalarini eshitib, ishtiyoq bilan harakatni boshlaymiz-u, "
    "lekin ko'p hollarda 2–3 kundan u yog'iga o'ta olmaymiz.\n\n"
    "O'zgarishni istaganlar ko'p, ammo insonni ushlab turadigan to'g'ri va qulay muhitni topish hozirgi kunda biroz masala.\n\n"
    "Aynan shu muammoga yechim sifatida \"FOCUS-21\" loyihasini boshlayapmiz.\n\n"
    "Loyiha doirasida 21 kun davomida ERKAKLAR uchun eng muhim bo'lgan 8 ta odatni birgalikda shakllantiramiz "
    "hamda 11 ta dolzarb mavzuda maxsus darslar o'tamiz."
)


@router.message(F.text.in_({"ℹ️ Loyiha haqida", "Loyiha haqida"}))
async def about_project(message: Message):
    await message.answer(
        WELCOME_TEXT,
        reply_markup=get_welcome_inline_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text.in_({"👨‍💻 Admin bilan bog'lanish", "📞 Bog'lanish"}))
async def contact_us(message: Message):
    text = (
        f"👨‍💻 <b>Admin bilan bog'lanish:</b>\n\n"
        f"Savollaringiz yoki to'lov bo'yicha murojaatingiz bo'lsa, quyidagi tugma orqali to'g'ridan-to'g'ri adminga yozishingiz mumkin:\n\n"
        f"👤 Admin: {html.escape(SUPPORT_USERNAME)}"
    )
    await message.answer(text, reply_markup=get_admin_contact_inline_keyboard(), parse_mode="HTML")
