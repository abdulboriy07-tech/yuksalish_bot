import asyncio
import html
from aiohttp import ClientSession, TCPConnector
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS, CARD_NUMBER, CARD_HOLDER, BARK_KEY
from database import get_tariff, get_user, create_order
from keyboards import (
    get_cancel_keyboard,
    get_admin_order_keyboard,
    get_main_keyboard
)

router = Router()


async def send_bark_alert(title: str, body: str, url: str = "https://t.me/focus_club1_bot"):
    if not BARK_KEY:
        return
    endpoint = f"https://api.day.app/{BARK_KEY}"
    payload = {
        "title": title,
        "body": body,
        "sound": "minuet",
        "group": "FOCUS21_Tolovlar",
        "url": url,
        "isArchive": 1
    }
    try:
        async with ClientSession(connector=TCPConnector(ssl=False)) as session:
            async with session.post(endpoint, json=payload, timeout=8):
                pass
    except Exception as e:
        print(f"Error sending Bark alert: {e}")


class PaymentStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_receipt = State()


async def send_booking_instructions(message: Message, state: FSMContext, user_id: int, full_name: str, phone: str):
    await state.update_data(
        tariff_id=3,
        amount=100000,
        web_name=full_name,
        phone=phone
    )
    await state.set_state(PaymentStates.waiting_for_receipt)

    display_name = html.escape(full_name)
    display_phone = html.escape(phone or "Kiritilmagan")

    payment_text = (
        f"📌 <b>FOCUS 21 — Joyni band qilish (100 000 so'm)</b>\n\n"
        f"1-noyabr kungi startgacha joyingiz va narxni band qilib qo'yishingiz mumkin. "
        f"Qolgan to'lovni kurs startigacha amalga oshirasiz.\n\n"
        f"📋 <b>Sizning ma'lumotlaringiz:</b>\n"
        f"👤 <b>Ism-familiya:</b> {display_name}\n"
        f"📞 <b>Telefon raqam:</b> <code>{display_phone}</code>\n"
        f"📦 <b>Dastur:</b> 📌 Joyni band qilish (100 000 so'm)\n"
        f"💰 <b>To'lov summasi:</b> <b>100 000 so'm</b>\n\n"
        f"────────────────────\n"
        f"💳 <b>To'lov uchun karta raqami:</b>\n"
        f"<code>{CARD_NUMBER}</code>\n"
        f"👤 <b>Karta egasi:</b> {html.escape(CARD_HOLDER)}\n"
        f"────────────────────\n\n"
        f"📸 <b>100 000 so'm to'lovni amalga oshirib, to'lov chekini (skrinshot yoki PDF) shu yerga yuboring!</b>\n\n"
        f"<i>(Karta raqami ustiga bossangiz nusxalanadi)</i>"
    )
    await message.answer(
        payment_text,
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment_handler(call: CallbackQuery, state: FSMContext):
    await call.answer("To'lov bekor qilindi.")
    await state.clear()
    is_admin = call.from_user.id in ADMIN_IDS
    await call.message.answer(
        "❌ Jarayon bekor qilindi. Bosh menyudasiz.",
        reply_markup=get_main_keyboard(is_admin=is_admin)
    )


@router.message(F.text.in_({"❌ Bekor qilish", "Bekor qilish"}))
async def cancel_text_handler(message: Message, state: FSMContext):
    await state.clear()
    is_admin = message.from_user.id in ADMIN_IDS
    await message.answer(
        "❌ Jarayon bekor qilindi. Bosh menyudasiz.",
        reply_markup=get_main_keyboard(is_admin=is_admin)
    )


@router.message(PaymentStates.waiting_for_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Foydalanuvchi"
    username = message.from_user.username
    from database import add_user
    await add_user(user_id=user_id, full_name=full_name, username=username, phone=phone, pending_tariff_id=3, pending_amount=100000)
    await send_booking_instructions(message, state, user_id, full_name, phone)


@router.message(PaymentStates.waiting_for_phone, F.text)
async def process_phone_text(message: Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Foydalanuvchi"
    username = message.from_user.username
    from database import add_user
    await add_user(user_id=user_id, full_name=full_name, username=username, phone=phone, pending_tariff_id=3, pending_amount=100000)
    await send_booking_instructions(message, state, user_id, full_name, phone)


async def _handle_receipt_submission(message: Message, state: FSMContext, bot: Bot, file_id: str, receipt_type: str):
    data = await state.get_data()
    db_user = await get_user(message.from_user.id)

    db_tariff_id = db_user["pending_tariff_id"] if db_user and "pending_tariff_id" in db_user.keys() and db_user["pending_tariff_id"] else 1
    db_amount = db_user["pending_amount"] if db_user and "pending_amount" in db_user.keys() and db_user["pending_amount"] else None

    tariff_id = data.get("tariff_id") or db_tariff_id or 1
    tariff = await get_tariff(tariff_id)
    amount = data.get("amount") or db_amount or (tariff["price"] if tariff else 790000)

    web_name = data.get("web_name") or (db_user["full_name"] if db_user and db_user["full_name"] else message.from_user.full_name) or "Foydalanuvchi"
    phone = data.get("phone") or (db_user["phone"] if db_user and "phone" in db_user.keys() else "")

    user_id = message.from_user.id
    order_id = await create_order(
        user_id=user_id,
        tariff_id=tariff_id,
        amount=amount,
        receipt_file_id=file_id,
        receipt_type=receipt_type,
        customer_name=web_name,
        phone=phone
    )

    await state.clear()

    tariff_name = html.escape(tariff["name"] if tariff else "🎯 FOCUS 21 — 21 kunlik to'liq dastur")
    safe_name = html.escape(web_name)
    safe_phone = html.escape(phone)
    price_str = f"{amount:,}".replace(",", " ")
    phone_line = f"📞 Telefon: <code>{safe_phone}</code>\n" if safe_phone else ""

    is_booking = (tariff_id == 3 or amount == 100000)

    # Send instant push notification to admin's iPhone via Bark
    bark_title = "📌 Joy band qilindi (100 000 so'm)!" if is_booking else "🔔 Yangi to'lov cheki keldi!"
    bark_body = f"👤 {web_name} ({phone or 'Tel yo‘q'})\n💰 Summa: {price_str} so‘m\n🆔 Buyurtma #{order_id}"
    asyncio.create_task(send_bark_alert(bark_title, bark_body))

    if is_booking:
        user_confirmation = (
            f"✅ <b>Chekingiz qabul qilindi!</b>\n\n"
            f"🆔 Buyurtma raqamingiz: <b>#{order_id}</b>\n"
            f"👤 Ism-familiya: <b>{safe_name}</b>\n"
            f"{phone_line}"
            f"📦 Dastur: <b>📌 FOCUS 21 — Joyni band qilish (100 000 so'm)</b>\n"
            f"💰 Summa: <b>{price_str} so'm</b>\n\n"
            f"⏳ Administrator chekni tez orada tekshirib tasdiqlaydi. "
            f"Tasdiqlangach, joyingiz kafolatlanadi va sizga yopiq guruhga (Kutuv zali) kirish xabari yuboriladi!"
        )
        admin_caption = (
            f"📌 <b>Yangi to'lov: JOYNI BAND QILISH (100 000 so'm)!</b>\n\n"
            f"🆔 Buyurtma: <b>#{order_id}</b>\n"
            f"👤 Foydalanuvchi: {message.from_user.mention_html()} ({safe_name})\n"
            f"{phone_line}"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"📦 Dastur: <b>📌 Joyni band qilish (100 000 so'm)</b>\n"
            f"💰 To'lov summasi: <b>{price_str} so'm</b>\n"
            f"🗓 Sana: {message.date.strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        user_confirmation = (
            f"✅ <b>Chekingiz qabul qilindi!</b>\n\n"
            f"🆔 Buyurtma raqamingiz: <b>#{order_id}</b>\n"
            f"👤 Ism-familiya: <b>{safe_name}</b>\n"
            f"{phone_line}"
            f"📦 Dastur: <b>{tariff_name}</b>\n"
            f"💰 Summa: <b>{price_str} so'm</b>\n\n"
            f"⏳ Administrator chekni tez orada tekshirib tasdiqlaydi. "
            f"Tasdiqlangach, sizga ushbu bot orqali yopiq guruhga kirish xabari yuboriladi."
        )
        admin_caption = (
            f"🔔 <b>Yangi to'lov cheki keldi!</b>\n\n"
            f"🆔 Buyurtma: <b>#{order_id}</b>\n"
            f"👤 Foydalanuvchi: {message.from_user.mention_html()} ({safe_name})\n"
            f"{phone_line}"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"📦 Dastur: <b>{tariff_name}</b>\n"
            f"💰 To'lov summasi: <b>{price_str} so'm</b>\n"
            f"🗓 Sana: {message.date.strftime('%Y-%m-%d %H:%M')}"
        )

    for admin_id in ADMIN_IDS:
        try:
            if receipt_type == "photo":
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=admin_caption,
                    reply_markup=get_admin_order_keyboard(order_id, user_id),
                    parse_mode="HTML"
                )
            else:
                await bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=admin_caption,
                    reply_markup=get_admin_order_keyboard(order_id, user_id),
                    parse_mode="HTML"
                )
        except Exception as e:
            print(f"Error sending receipt to admin {admin_id}: {e}")


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def process_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    photo = message.photo[-1]
    await _handle_receipt_submission(message, state, bot, photo.file_id, "photo")


@router.message(PaymentStates.waiting_for_receipt, F.document)
async def process_receipt_document(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    await _handle_receipt_submission(message, state, bot, doc.file_id, "document")


@router.message(F.photo)
async def process_direct_photo_receipt(message: Message, state: FSMContext, bot: Bot):
    """Fallback if user sends payment screenshot after state reset or without active state."""
    current_state = await state.get_state()
    if current_state and current_state.startswith("AdminStates"):
        return
    photo = message.photo[-1]
    await _handle_receipt_submission(message, state, bot, photo.file_id, "photo")


@router.message(F.document)
async def process_direct_document_receipt(message: Message, state: FSMContext, bot: Bot):
    """Fallback if user sends PDF/document receipt after state reset or without active state."""
    current_state = await state.get_state()
    if current_state and current_state.startswith("AdminStates"):
        return
    doc = message.document
    await _handle_receipt_submission(message, state, bot, doc.file_id, "document")


@router.message(PaymentStates.waiting_for_receipt)
async def invalid_receipt(message: Message, state: FSMContext):
    if message.text and message.text.strip().upper() == "SUPER40":
        await state.update_data(tariff_id=2, amount=483000, promo_code="SUPER40")
        await message.answer(
            "🎉 <b>SUPER40 promokodi qo'llandi! (30% chegirma)</b>\n\n"
            "💰 Yangilangan to'lov summasi: <s>790 000</s> <b>483 000 so'm</b>\n"
            f"💳 Karta: <code>{CARD_NUMBER}</code> ({html.escape(CARD_HOLDER)})\n\n"
            "📸 Endi to'lov chekini (skrinshot yoki PDF) shu yerga yuboring:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    await message.answer(
        "📸 Iltimos, to'lov chekini <b>rasm (skrinshot)</b> yoki <b>PDF fayl</b> ko'rinishida yuboring.\n"
        "Jarayonni bekor qilish uchun '❌ Bekor qilish' tugmasini bosing.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
