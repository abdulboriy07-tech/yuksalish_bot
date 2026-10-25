from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import SITE_URL, SUPPORT_USERNAME


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(text="📌 Joyni band qilish (100 000 so'm)")
        ],
        [
            KeyboardButton(text="ℹ️ Loyiha haqida"),
            KeyboardButton(text="👨‍💻 Admin bilan bog'lanish")
        ]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📌 Joyni band qilish (100 000 so'm)", callback_data="book_spot_100k"))
    builder.row(InlineKeyboardButton(text="🌐 Loyiha haqida (Veb-sayt)", url=SITE_URL))
    return builder.as_markup()


def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamimni yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_site_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Loyiha haqida", url=SITE_URL))
    return builder.as_markup()


def get_admin_contact_inline_keyboard() -> InlineKeyboardMarkup:
    admin_url = f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"👨‍💻 Admin ({SUPPORT_USERNAME})", url=admin_url))
    return builder.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Bekor qilish", callback_data="cancel_payment")
    return builder.as_markup()


def get_admin_order_keyboard(order_id: int, user_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    approve_cb = f"admin_approve:{order_id}:{user_id}" if user_id else f"admin_approve:{order_id}"
    reject_cb = f"admin_reject:{order_id}:{user_id}" if user_id else f"admin_reject:{order_id}"
    builder.button(text="✅ Tasdiqlash", callback_data=approve_cb)
    builder.button(text="❌ Rad etish", callback_data=reject_cb)
    builder.adjust(2)
    return builder.as_markup()


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Statistika", callback_data="admin_stats")
    builder.button(text="⏳ Kutilayotgan to'lovlar", callback_data="admin_pending_orders")
    builder.button(text="🔗 Yopiq guruh/kanalni ulash", callback_data="admin_set_group")
    builder.button(text="📢 Xabar yuborish (Broadcast)", callback_data="admin_broadcast")
    builder.adjust(1)
    return builder.as_markup()
