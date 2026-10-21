import html
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_IDS, PROJECT_NAME, PRIVATE_CHAT_ID
from database import (
    get_stats,
    get_pending_orders,
    get_order,
    update_order_status,
    get_all_users,
    get_setting,
    set_setting
)
from keyboards import get_admin_panel_keyboard, get_admin_order_keyboard

router = Router()

MENU_BUTTON_TEXTS = {
    "ℹ️ Loyiha haqida",
    "👨‍💻 Admin bilan bog'lanish",
    "📞 Bog'lanish",
    "⚙️ Admin Panel"
}


class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_group_id = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def get_active_private_chat_id() -> str:
    saved_id = await get_setting("private_chat_id", "")
    return (saved_id or PRIVATE_CHAT_ID or "").strip()


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        f"Sizning Telegram ID raqamingiz: <code>{message.from_user.id}</code>\n"
        f"Ushbu ID ni <code>.env</code> faylidagi <code>ADMIN_IDS</code> qatoriga yozishingiz mumkin.",
        parse_mode="HTML"
    )


@router.message(Command("admin"))
@router.message(F.text == "⚙️ Admin Panel")
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        if not ADMIN_IDS:
            await message.answer(
                f"⚠️ Hozircha birorta ham Admin ID ko'rsatilmagan!\n"
                f"Sizning Telegram ID: <code>{message.from_user.id}</code>\n"
                f"Iltimos, <code>.env</code> faylidagi <code>ADMIN_IDS</code> ga ushbu raqamni kiriting.",
                parse_mode="HTML"
            )
            return
        await message.answer("Sizda admin huquqi mavjud emas.")
        return

    text = f"👨‍💻 <b>{html.escape(PROJECT_NAME)} — Boshqaruv paneli (Admin)</b>\n\nQuyidagi bo'limlardan birini tanlang:"
    await message.answer(text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_stats")
async def show_admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer()
    stats = await get_stats()
    revenue_str = f"{stats['total_revenue']:,}".replace(",", " ")

    text = (
        f"📊 <b>Loyiha statistikasi:</b>\n\n"
        f"👥 Foydalanuvchilar soni: <b>{stats['total_users']} ta</b>\n"
        f"📦 Jami buyurtmalar: <b>{stats['total_orders']} ta</b>\n"
        f"⏳ Kutilayotgan to'lovlar: <b>{stats['pending_orders']} ta</b>\n"
        f"✅ Tasdiqlangan to'lovlar: <b>{stats['approved_orders']} ta</b>\n"
        f"💵 Jami tushum: <b>{revenue_str} so'm</b>\n"
    )
    try:
        await call.message.edit_text(text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "admin_pending_orders")
async def show_pending_orders(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer()
    orders = await get_pending_orders()

    if not orders:
        try:
            await call.message.edit_text(
                "⏳ Hozirda kutilayotgan yangi to'lovlar yo'q.",
                reply_markup=get_admin_panel_keyboard()
            )
        except Exception:
            await call.message.answer("⏳ Hozirda kutilayotgan yangi to'lovlar yo'q.")
        return

    await call.message.answer(f"⏳ Jami <b>{len(orders)}</b> ta kutilayotgan to'lov topildi:", parse_mode="HTML")

    for order in orders:
        price_str = f"{order['amount']:,}".replace(",", " ")
        cust_name = html.escape(order["customer_name"] or order["full_name"] or "Foydalanuvchi")
        phone = html.escape(order["phone"] or order["user_phone"] or "")
        user_mention = f"@{html.escape(order['username'])}" if order["username"] else f"{cust_name} (ID: <code>{order['user_id']}</code>)"
        phone_line = f"📞 Telefon: <code>{phone}</code>\n" if phone else ""
        tariff_name = html.escape(order["tariff_name"] or "🎯 FOCUS 21")

        caption = (
            f"🆔 Buyurtma: <b>#{order['id']}</b>\n"
            f"👤 Mijoz: {user_mention} ({cust_name})\n"
            f"{phone_line}"
            f"📦 Dastur: <b>{tariff_name}</b>\n"
            f"💰 Summa: <b>{price_str} so'm</b>\n"
            f"🗓 Sana: {order['created_at']}"
        )
        r_type = order["receipt_type"] if "receipt_type" in order.keys() else "photo"
        try:
            if r_type == "document":
                await bot.send_document(
                    chat_id=call.from_user.id,
                    document=order["receipt_file_id"],
                    caption=caption,
                    reply_markup=get_admin_order_keyboard(order["id"]),
                    parse_mode="HTML"
                )
            else:
                await bot.send_photo(
                    chat_id=call.from_user.id,
                    photo=order["receipt_file_id"],
                    caption=caption,
                    reply_markup=get_admin_order_keyboard(order["id"]),
                    parse_mode="HTML"
                )
        except Exception:
            try:
                await bot.send_document(
                    chat_id=call.from_user.id,
                    document=order["receipt_file_id"],
                    caption=caption,
                    reply_markup=get_admin_order_keyboard(order["id"]),
                    parse_mode="HTML"
                )
            except Exception:
                await call.message.answer(
                    caption,
                    reply_markup=get_admin_order_keyboard(order["id"]),
                    parse_mode="HTML"
                )


async def _update_admin_order_message(call: CallbackQuery, status_badge: str):
    base_html = call.message.html_text or html.escape(call.message.caption or call.message.text or "")
    new_content = f"{base_html}\n\n{status_badge}"
    try:
        if call.message.caption is not None:
            await call.message.edit_caption(caption=new_content, reply_markup=None, parse_mode="HTML")
        else:
            await call.message.edit_text(text=new_content, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        print(f"Error updating admin message: {e}")


@router.callback_query(F.data.startswith("admin_approve:"))
async def approve_order(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    order_id = int(call.data.split(":")[1])
    order = await get_order(order_id)

    if not order:
        await call.answer("Buyurtma topilmadi!", show_alert=True)
        return

    if order["status"] != "pending":
        await call.answer(f"Ushbu buyurtma allaqachon '{order['status']}' holatida!", show_alert=True)
        return

    await update_order_status(order_id, "approved")
    await call.answer("✅ To'lov tasdiqlandi!")

    user_id = order["user_id"]
    cust_name = order["customer_name"] or order["full_name"] or f"User {user_id}"
    tariff_name = html.escape(order["tariff_name"] or "🎯 FOCUS 21")
    admin_name = html.escape(call.from_user.full_name or "Admin")

    # Generate a 1-time (single-use, member_limit=1) invite link for the linked private group/channel
    private_chat_id = await get_active_private_chat_id()
    invite_url = None
    invite_error = None

    if private_chat_id:
        try:
            target_chat = int(private_chat_id) if private_chat_id.lstrip("-").isdigit() else private_chat_id
            invite_obj = await bot.create_chat_invite_link(
                chat_id=target_chat,
                name=f"#{order_id} {cust_name}"[:32],
                member_limit=1
            )
            invite_url = invite_obj.invite_link
        except Exception as e:
            invite_error = str(e)
            print(f"Error creating 1-time invite link for order #{order_id}: {e}")

    if invite_url:
        await _update_admin_order_message(
            call,
            f"✅ <b>TASDIQLANDI</b> (Admin: {admin_name})\n"
            f"🔗 <b>1 martalik link yuborildi:</b> {html.escape(invite_url)}"
        )
        user_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔒 Yopiq guruhga qo'shilish (1 martalik)", url=invite_url)]
            ]
        )
        user_text = (
            f"🎉 <b>Tabriklaymiz! Sizning to'lovingiz tasdiqlandi!</b>\n\n"
            f"🆔 Buyurtma: <b>#{order_id}</b>\n"
            f"📦 Dastur: <b>{tariff_name}</b>\n\n"
            f"👇 Quyidagi maxsus havola orqali <b>{html.escape(PROJECT_NAME)}</b> yopiq guruhiga/kanaliga qo'shiling:\n"
            f"🔗 {html.escape(invite_url)}\n\n"
            f"⚠️ <b>Diqqat:</b> Ushbu havola faqat siz uchun yaratilgan va <b>faqat 1 marta</b> kirish uchun ishlaydi. "
            f"Guruhga qo'shilganingizdan so'ng havola avtomatik tarzda o'z kuchini yo'qotadi (tugaydi)!"
        )
        try:
            await bot.send_message(
                chat_id=user_id,
                text=user_text,
                reply_markup=user_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Error notifying user {user_id}: {e}")
    else:
        warn_note = (
            "\n⚠️ <i>Yopiq guruh/kanal hali ulanmagan yoki botda 'Invite Users via Link' admin huquqi yo'q. "
            "⚙️ Admin Panel → 🔗 Yopiq guruh/kanalni ulash bo'limidan sozlang.</i>"
        )
        await _update_admin_order_message(
            call,
            f"✅ <b>TASDIQLANDI</b> (Admin: {admin_name}){warn_note}"
        )
        user_text = (
            f"🎉 <b>Xushxabar! Sizning to'lovingiz tasdiqlandi!</b>\n\n"
            f"🆔 Buyurtma: <b>#{order_id}</b>\n"
            f"📦 Dastur: <b>{tariff_name}</b>\n\n"
            f"<b>{html.escape(PROJECT_NAME)}</b> a'zoligingiz muvaffaqiyatli faollashtirildi! 🚀\n"
            f"Tez orada sizga yopiq guruhga kirish havolasi yuboriladi."
        )
        try:
            await bot.send_message(chat_id=user_id, text=user_text, parse_mode="HTML")
        except Exception as e:
            print(f"Error notifying user {user_id}: {e}")


@router.my_chat_member()
async def on_bot_chat_member_updated(event: ChatMemberUpdated, bot: Bot):
    """Automatically link the private group/channel when the bot is promoted to Administrator."""
    chat = event.chat
    new_status = event.new_chat_member.status
    if chat.type in {"group", "supergroup", "channel"} and new_status == "administrator":
        chat_id_str = str(chat.id)
        chat_title = chat.title or "Yopiq guruh/kanal"
        await set_setting("private_chat_id", chat_id_str)
        await set_setting("private_chat_title", chat_title)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"✅ <b>Yopiq guruh/kanal avtomatik ulandi!</b>\n\n"
                        f"📌 <b>Nomi:</b> {html.escape(chat_title)}\n"
                        f"🆔 <b>Chat ID:</b> <code>{chat_id_str}</code>\n\n"
                        f"Endi har bir to'lovi tasdiqlangan ishtirokchiga faqat <b>1 marta ishlaydigan (member_limit=1)</b> "
                        f"maxsus kirish linki avtomatik yaratib yuboriladi!"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass


@router.message(Command("setgroup"))
@router.channel_post(Command("setgroup"))
async def cmd_set_group_here(message: Message, bot: Bot):
    """Allow linking any group or channel by sending /setgroup inside it."""
    if message.chat.type not in {"group", "supergroup", "channel"}:
        if is_admin(message.from_user.id):
            await message.answer(
                "ℹ️ Ushbu buyruqni bot Admin qilingan <b>yopiq guruh yoki kanal ichida</b> yozing, "
                "yoki <b>⚙️ Admin Panel → 🔗 Yopiq guruh/kanalni ulash</b> tugmasidan foydalaning.",
                parse_mode="HTML"
            )
        return

    chat_id_str = str(message.chat.id)
    chat_title = message.chat.title or "Yopiq guruh/kanal"
    await set_setting("private_chat_id", chat_id_str)
    await set_setting("private_chat_title", chat_title)

    try:
        await message.delete()
    except Exception:
        pass

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"✅ <b>Yopiq guruh/kanal muvaffaqiyatli saqlandi!</b>\n\n"
                    f"📌 <b>Nomi:</b> {html.escape(chat_title)}\n"
                    f"🆔 <b>Chat ID:</b> <code>{chat_id_str}</code>\n\n"
                    f"Endi to'lov tasdiqlanganda har bir ishtirokchiga <b>1 martalik</b> kirish havolasi avtomatik yuboriladi."
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass


@router.callback_query(F.data == "admin_set_group")
async def start_set_group(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer()
    current_id = await get_active_private_chat_id()
    current_title = await get_setting("private_chat_title", "Noma'lum")

    status_line = (
        f"✅ <b>Hozir ulangan:</b> {html.escape(current_title)} (<code>{html.escape(current_id)}</code>)\n\n"
        if current_id
        else "⚠️ <b>Hozircha yopiq guruh yoki kanal ulanmagan!</b>\n\n"
    )

    await state.set_state(AdminStates.waiting_for_group_id)
    await call.message.answer(
        f"🔗 <b>1 martalik link uchun Yopiq Guruh / Kanalni ulash:</b>\n\n"
        f"{status_line}"
        f"<b>Ulashning eng oson 3 ta usuli:</b>\n"
        f"1️⃣ <b>Avtomatik:</b> @focus_club1_bot ni yopiq guruh yoki kanalingizga <b>Admin</b> qilib qo'shing (<i>Invite Users via Link</i> huquqi bilan) — bot o'zi avtomatik ulab oladi!\n"
        f"2️⃣ <b>Forward qilish:</b> Yopiq kanalingizdan birorta xabarni shu yerga <b>Forward</b> qiling.\n"
        f"3️⃣ <b>ID yuborish:</b> Yopiq guruh/kanal ID raqamini (masalan: <code>-1001234567890</code>) shu yerga yozib yuboring yoki guruh ichida <code>/setgroup</code> deb yozing.\n\n"
        f"Bekor qilish uchun /cancel deb yozing.",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_group_id, Command("cancel"))
async def cancel_set_group(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Guruh/kanalni sozlash bekor qilindi.")


@router.message(
    AdminStates.waiting_for_group_id,
    ~F.text.in_(MENU_BUTTON_TEXTS),
    ~F.text.startswith("/")
)
async def process_set_group(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    target_id = None
    target_title = "Yopiq guruh/kanal"

    if message.forward_from_chat and message.forward_from_chat.type in {"group", "supergroup", "channel"}:
        target_id = str(message.forward_from_chat.id)
        target_title = message.forward_from_chat.title or target_title
    elif message.text:
        raw = message.text.strip()
        if raw.lstrip("-").isdigit():
            target_id = raw if raw.startswith("-") else f"-{raw}"
        elif raw.startswith("@"):
            target_id = raw

    if not target_id:
        await message.answer(
            "⚠️ Iltimos, yopiq kanaldan xabar <b>Forward</b> qiling yoki guruh/kanal ID raqamini "
            "(masalan: <code>-1001234567890</code>) yuboring. Bekor qilish: /cancel",
            parse_mode="HTML"
        )
        return

    # Verify bot can create a 1-time invite link in that chat
    try:
        chat_ref = int(target_id) if target_id.lstrip("-").isdigit() else target_id
        chat_info = await bot.get_chat(chat_ref)
        target_title = chat_info.title or target_title
        target_id = str(chat_info.id)
    except Exception as e:
        await message.answer(
            f"❌ Bot ushbu guruh/kanalni topa olmadi yoki unda Admin emas!\n"
            f"Avval @focus_club1_bot ni guruh/kanalga <b>Admin</b> qilib qo'shing.\n"
            f"Xatolik: <code>{html.escape(str(e))}</code>",
            parse_mode="HTML"
        )
        return

    await set_setting("private_chat_id", target_id)
    await set_setting("private_chat_title", target_title)
    await state.clear()

    await message.answer(
        f"✅ <b>Yopiq guruh/kanal muvaffaqiyatli ulandi!</b>\n\n"
        f"📌 <b>Nomi:</b> {html.escape(target_title)}\n"
        f"🆔 <b>Chat ID:</b> <code>{html.escape(target_id)}</code>\n\n"
        f"Endi to'lovni tasdiqlaganingizda (<b>✅ Tasdiqlash</b>), bot har bir ishtirokchi uchun "
        f"faqat <b>1 marta ishlaydigan (member_limit=1)</b> maxsus link yaratib yuboradi!",
        reply_markup=get_admin_panel_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("admin_reject:"))
async def reject_order(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    order_id = int(call.data.split(":")[1])
    order = await get_order(order_id)

    if not order:
        await call.answer("Buyurtma topilmadi!", show_alert=True)
        return

    if order["status"] != "pending":
        await call.answer(f"Ushbu buyurtma allaqachon '{order['status']}' holatida!", show_alert=True)
        return

    await update_order_status(order_id, "rejected")
    await call.answer("❌ To'lov rad etildi.")

    admin_name = html.escape(call.from_user.full_name or "Admin")
    await _update_admin_order_message(call, f"❌ <b>RAD ETILDI</b> (Admin: {admin_name})")

    user_id = order["user_id"]
    user_text = (
        f"❌ <b>To'lov tasdiqlanmadi</b>\n\n"
        f"🆔 Buyurtma raqamingiz: <b>#{order_id}</b>\n\n"
        f"Afsuski, yuborilgan to'lov chekingiz tasdiqlanmadi (mablag' hisobga tushmagan yoki noto'g'ri chek yuborilgan bo'lishi mumkin).\n\n"
        f"Iltimos, qaytadan urinib ko'ring yoki tushunmovchilik bo'lsa ma'muriyat bilan bog'laning."
    )
    try:
        await bot.send_message(chat_id=user_id, text=user_text, parse_mode="HTML")
    except Exception as e:
        print(f"Error notifying user {user_id}: {e}")


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True)
        return

    await call.answer()
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.message.answer(
        "📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring (matn, rasm yoki post):\n\n"
        "Bekor qilish uchun /cancel deb yozing."
    )


@router.message(AdminStates.waiting_for_broadcast, Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Xabar tarqatish bekor qilindi.")


@router.message(
    AdminStates.waiting_for_broadcast,
    ~F.text.in_(MENU_BUTTON_TEXTS),
    ~F.text.startswith("/")
)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    users = await get_all_users()

    sent_count = 0
    fail_count = 0

    progress_msg = await message.answer(f"⏳ Xabar tarqatilmoqda (0/{len(users)})...")

    for u in users:
        try:
            await message.copy_to(chat_id=u["user_id"])
            sent_count += 1
        except Exception:
            fail_count += 1

    await progress_msg.edit_text(
        f"📢 <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"✅ Yuborildi: <b>{sent_count} ta</b>\n"
        f"❌ Yetib bormadi (bloklangan): <b>{fail_count} ta</b>",
        parse_mode="HTML"
    )
