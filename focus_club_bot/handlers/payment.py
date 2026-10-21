import html
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS, CARD_NUMBER, CARD_HOLDER
from database import get_tariff, get_user, create_order
from keyboards import (
    get_cancel_keyboard,
    get_admin_order_keyboard
)

router = Router()


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment_handler(call: CallbackQuery, state: FSMContext):
    await call.answer("To'lov bekor qilindi.")
    await state.clear()
    await call.message.answer("❌ To'lov jarayoni bekor qilindi. Bosh menyudasiz.")


async def _handle_receipt_submission(message: Message, state: FSMContext, bot: Bot, file_id: str, receipt_type: str):
    data = await state.get_data()
    db_user = await get_user(message.from_user.id)

    tariff_id = data.get("tariff_id") or 1
    tariff = await get_tariff(tariff_id)
    amount = data.get("amount") or (tariff["price"] if tariff else 690000)

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
    await message.answer(user_confirmation, parse_mode="HTML")

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
                    reply_markup=get_admin_order_keyboard(order_id),
                    parse_mode="HTML"
                )
            else:
                await bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=admin_caption,
                    reply_markup=get_admin_order_keyboard(order_id),
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


@router.message(PaymentStates.waiting_for_receipt)
async def invalid_receipt(message: Message, state: FSMContext):
    if message.text and message.text.strip().upper() == "SUPER40":
        await state.update_data(tariff_id=2, amount=483000, promo_code="SUPER40")
        await message.answer(
            "🎉 <b>SUPER40 promokodi qo'llandi! (30% chegirma)</b>\n\n"
            "💰 Yangilangan to'lov summasi: <s>690 000</s> <b>483 000 so'm</b>\n"
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
