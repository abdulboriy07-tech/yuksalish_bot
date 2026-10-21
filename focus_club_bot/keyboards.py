from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import SITE_URL, SUPPORT_USERNAME


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(text="ℹ️ Loyiha haqida", web_app=WebAppInfo(url=SITE_URL)),
            KeyboardButton(text="👨‍💻 Admin bilan bog'lanish")
        ]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    admin_url = f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="ℹ️ Loyiha haqida", url=SITE_URL))
    builder.row(InlineKeyboardButton(text="👨‍💻 Admin bilan bog'lanish", url=admin_url))
    return builder.as_markup()


def get_site_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌐 Saytga o'tish — FOCUS 21", url=SITE_URL))
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


def get_admin_order_keyboard(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data=f"admin_approve:{order_id}")
    builder.button(text="❌ Rad etish", callback_data=f"admin_reject:{order_id}")
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
