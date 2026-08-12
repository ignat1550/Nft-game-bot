import asyncio
import os
import random
from html import escape

import psycopg
from psycopg.rows import dict_row

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Приватный Telegram Bot API
API_SERVER = os.getenv(
    "API_SERVER",
    "http://31.76.20.193:8081"
)

# Railway PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Администраторы
ADMINS = {
    1780243345,
    1780243308,
    1780243378,
}

# Кому писать для пополнения
PAYMENT_ADMINS = [
    "@doxme",
    "@modeevil",
    "@bogkm",
]


# ============================================================
# CHECK CONFIG
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN в переменных окружения Railway."
    )

if not DATABASE_URL:
    raise RuntimeError(
        "Не задан DATABASE_URL. "
        "Добавь PostgreSQL в Railway."
    )

# Иногда DATABASE_URL может иметь postgres://
# вместо postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )


# ============================================================
# TELEGRAM
# ============================================================

api_server = TelegramAPIServer.from_base(
    API_SERVER
)

session = AiohttpSession(
    api=api_server
)

bot = Bot(
    token=BOT_TOKEN,
    session=session
)

dp = Dispatcher()


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init_database():

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    stars BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS nfts (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    amount BIGINT NOT NULL DEFAULT 0,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()

    print("✅ PostgreSQL подключена")
    print("✅ Таблицы проверены")


def ensure_user(
    user_id: int,
    username: str = ""
):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO users
                    (user_id, username)
                VALUES
                    (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    username = EXCLUDED.username
            """, (
                user_id,
                username
            ))

        conn.commit()


def get_balance(user_id: int) -> int:

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT stars
                FROM users
                WHERE user_id = %s
            """, (
                user_id,
            ))

            row = cur.fetchone()

            if row:
                return int(row["stars"])

    return 0


def change_stars(
    user_id: int,
    amount: int,
    description: str
):

    ensure_user(user_id)

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET stars = stars + %s
                WHERE user_id = %s
            """, (
                amount,
                user_id
            ))

            cur.execute("""
                INSERT INTO history
                    (user_id, action, amount, description)
                VALUES
                    (%s, %s, %s, %s)
            """, (
                user_id,
                "stars",
                amount,
                description
            ))

        conn.commit()


def remove_stars(
    user_id: int,
    amount: int,
    description: str
) -> bool:

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET stars = stars - %s
                WHERE user_id = %s
                  AND stars >= %s
                RETURNING stars
            """, (
                amount,
                user_id,
                amount
            ))

            row = cur.fetchone()

            if not row:
                conn.rollback()
                return False

            cur.execute("""
                INSERT INTO history
                    (user_id, action, amount, description)
                VALUES
                    (%s, %s, %s, %s)
            """, (
                user_id,
                "stars",
                -amount,
                description
            ))

        conn.commit()

    return True


def add_nft(
    user_id: int,
    name: str,
    source: str
):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO nfts
                    (user_id, name, source)
                VALUES
                    (%s, %s, %s)
            """, (
                user_id,
                name,
                source
            ))

        conn.commit()


def get_nfts(user_id: int):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT id, name, source, created_at
                FROM nfts
                WHERE user_id = %s
                ORDER BY id DESC
            """, (
                user_id,
            ))

            return cur.fetchall()


def get_statistics():

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT COUNT(*) AS count
                FROM users
            """)

            users = cur.fetchone()["count"]

            cur.execute("""
                SELECT COALESCE(SUM(stars), 0) AS stars
                FROM users
            """)

            stars = cur.fetchone()["stars"]

            cur.execute("""
                SELECT COUNT(*) AS count
                FROM nfts
            """)

            nfts = cur.fetchone()["count"]

    return users, stars, nfts


# ============================================================
# KEYBOARDS
# ============================================================

def main_menu():

    kb = InlineKeyboardBuilder()

    kb.button(
        text="⭐ Мои звёзды",
        callback_data="balance"
    )

    kb.button(
        text="🎁 Кейсы",
        callback_data="cases"
    )

    kb.button(
        text="🎒 Мои NFT",
        callback_data="nfts"
    )

    kb.button(
        text="💳 Пополнить ⭐",
        callback_data="topup"
    )

    kb.button(
        text="👤 Профиль",
        callback_data="profile"
    )

    kb.adjust(2)

    return kb.as_markup()


def back_menu():

    kb = InlineKeyboardBuilder()

    kb.button(
        text="🔙 Назад",
        callback_data="home"
    )

    return kb.as_markup()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):

    user_id = message.from_user.id
    username = message.from_user.username or ""

    ensure_user(
        user_id,
        username
    )

    await message.answer(
        "🌊 <b>Wavegram Cases</b>\n\n"
        "Добро пожаловать!\n\n"
        "⭐ Звёзды\n"
        "🎁 Кейсы\n"
        "🖼 NFT\n\n"
        "Выбери действие:",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ============================================================
# HOME
# ============================================================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await callback.answer()


# ============================================================
# BALANCE
# ============================================================

@dp.callback_query(F.data == "balance")
async def balance(callback: CallbackQuery):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username or ""
    )

    stars = get_balance(user_id)

    await callback.message.edit_text(
        "⭐ <b>Мои звёзды</b>\n\n"
        f"Баланс: <b>{stars} ⭐</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# TOP UP
# ============================================================

@dp.callback_query(F.data == "topup")
async def topup(callback: CallbackQuery):

    admins_text = "\n".join(
        f"• {admin}"
        for admin in PAYMENT_ADMINS
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Для пополнения ⭐ напиши одному "
        "из наших администраторов:\n\n"
        f"{admins_text}\n\n"
        "После оплаты обязательно отправь админу "
        "свой Telegram ID.\n\n"
        "🆔 Твой Telegram ID:\n"
        f"<code>{callback.from_user.id}</code>\n\n"
        "После проверки оплаты администратор "
        "зачислит звёзды на твой баланс.",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username or ""
    )

    nfts = get_nfts(user_id)

    await callback.message.edit_text(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: @{callback.from_user.username or 'нет'}\n"
        f"⭐ Баланс: <b>{get_balance(user_id)} ⭐</b>\n"
        f"🖼 NFT: <b>{len(nfts)}</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# NFT
# ============================================================

@dp.callback_query(F.data == "nfts")
async def nfts(callback: CallbackQuery):

    items = get_nfts(
        callback.from_user.id
    )

    if not items:

        text = (
            "🎒 <b>Мои NFT</b>\n\n"
            "У тебя пока нет NFT."
        )

    else:

        text = "🎒 <b>Мои NFT</b>\n\n"

        for nft in items:

            text += (
                f"🖼 <b>{escape(nft['name'])}</b>\n"
                f"Источник: {escape(nft['source'])}\n"
                f"ID: <code>{nft['id']}</code>\n\n"
            )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# CASES
# ============================================================

@dp.callback_query(F.data == "cases")
async def cases(callback: CallbackQuery):

    kb = InlineKeyboardBuilder()

    kb.button(
        text="🎫 Билитер — 100 ⭐",
        callback_data="case_bileter"
    )

    kb.button(
        text="💎 Лакшери — 2000 ⭐",
        callback_data="case_luxury"
    )

    kb.button(
        text="😵 Наркоман — 100 ⭐",
        callback_data="case_nark"
    )

    kb.button(
        text="🔙 Назад",
        callback_data="home"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        "🎁 <b>Кейсы</b>\n\n"
        "Выбери кейс:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# BILETER
# ============================================================

@dp.callback_query(F.data == "case_bileter")
async def bileter(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not remove_stars(
        user_id,
        100,
        "Открытие кейса Билитер"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 100 ⭐",
            show_alert=True
        )

        return

    # Обычный билет — 94%
    # Золотой — 6%
    roll = random.random()

    if roll < 0.94:

        change_stars(
            user_id,
            50,
            "Награда Билитера: обычный билет"
        )

        text = (
            "🎫 <b>Билитер</b>\n\n"
            "Выпал обычный билет!\n\n"
            "⭐ Награда: <b>50 ⭐</b>"
        )

    else:

        nft = random.choice([
            "🏆 Золотой NFT",
            "💎 Редкий NFT",
            "🌟 Golden Gift"
        ])

        add_nft(
            user_id,
            nft,
            "Билитер"
        )

        await notify_admins(
            user_id,
            callback.from_user.username,
            nft,
            "Билитер"
        )

        text = (
            "🎫 <b>ЗОЛОТОЙ БИЛЕТ!</b> 🔥\n\n"
            f"🖼 NFT:\n"
            f"<b>{escape(nft)}</b>\n\n"
            "🔥 Тебе очень повезло!"
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# LUXURY
# ============================================================

@dp.callback_query(F.data == "case_luxury")
async def luxury(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not remove_stars(
        user_id,
        2000,
        "Открытие кейса Лакшери"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 2000 ⭐",
            show_alert=True
        )

        return

    # Пока используются внутренние NFT.
    # Сюда можно подключить API Wavegram,
    # когда будет известна его документация.

    nft = random.choice([
        "💎 Luxury Gift",
        "👑 Diamond Gift",
        "🌌 Galaxy Gift",
        "🔥 Legendary Gift",
        "🦋 Rare Gift",
    ])

    add_nft(
        user_id,
        nft,
        "Лакшери"
    )

    await notify_admins(
        user_id,
        callback.from_user.username,
        nft,
        "Лакшери"
    )

    await callback.message.edit_text(
        "💎 <b>ЛАКШЕРИ</b>\n\n"
        f"🎁 Тебе выпал:\n"
        f"<b>{escape(nft)}</b>\n\n"
        "🔥 Поздравляем!",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# NARKOMAN
# ============================================================

@dp.callback_query(F.data == "case_nark")
async def nark(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not remove_stars(
        user_id,
        100,
        "Открытие кейса Наркоман"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 100 ⭐",
            show_alert=True
        )

        return

    # 90% — 50 звёзд
    # 10% — Глазик

    if random.random() < 0.90:

        change_stars(
            user_id,
            50,
            "Награда Наркомана: 50 звёзд"
        )

        text = (
            "😵 <b>Наркоман</b>\n\n"
            "Выпало:\n\n"
            "⭐ <b>50 звёзд</b>"
        )

    else:

        nft = "👁 Глазик"

        add_nft(
            user_id,
            nft,
            "Наркоман"
        )

        await notify_admins(
            user_id,
            callback.from_user.username,
            nft,
            "Наркоман"
        )

        text = (
            "😵 <b>НАРКОМАН!</b> 🔥\n\n"
            "Тебе выпал редкий NFT:\n\n"
            "👁 <b>Глазик</b>"
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# ADMIN NOTIFICATION
# ============================================================

async def notify_admins(
    user_id: int,
    username: str | None,
    nft_name: str,
    case_name: str
):

    if username:
        user_text = f"@{username}"
    else:
        user_text = "без username"

    text = (
        "🚨 <b>ВЫПАЛ NFT!</b>\n\n"
        f"👤 Пользователь: {escape(user_text)}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🎁 Кейс: <b>{escape(case_name)}</b>\n"
        f"🖼 NFT: <b>{escape(nft_name)}</b>"
    )

    for admin_id in ADMINS:

        try:

            await bot.send_message(
                admin_id,
                text,
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                f"Ошибка уведомления "
                f"{admin_id}: {error}"
            )


# ============================================================
# /ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):

    if message.from_user.id not in ADMINS:

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    kb = InlineKeyboardBuilder()

    kb.button(
        text="⭐ Выдать звёзды",
        callback_data="admin_add"
    )

    kb.button(
        text="➖ Забрать звёзды",
        callback_data="admin_remove"
    )

    kb.button(
        text="📊 Статистика",
        callback_data="admin_stats"
    )

    kb.adjust(1)

    await message.answer(
        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выбери действие:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


# ============================================================
# ADMIN ADD
# ============================================================

@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    await callback.message.answer(
        "⭐ <b>Выдать звёзды</b>\n\n"
        "Отправь:\n\n"
        "<code>ID количество</code>\n\n"
        "Пример:\n"
        "<code>123456789 1000</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# ADMIN REMOVE
# ============================================================

@dp.callback_query(F.data == "admin_remove")
async def admin_remove(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    await callback.message.answer(
        "➖ <b>Забрать звёзды</b>\n\n"
        "Отправь:\n\n"
        "<code>ID количество</code>\n\n"
        "Пример:\n"
        "<code>123456789 500</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# ADMIN STATS
# ============================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    users, stars, nfts = get_statistics()

    await callback.message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👤 Пользователей: <b>{users}</b>\n"
        f"⭐ Звёзд на балансах: <b>{stars}</b>\n"
        f"🖼 NFT: <b>{nfts}</b>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# ADMIN COMMANDS
# ============================================================

@dp.message()
async def admin_text(message: Message):

    if message.from_user.id not in ADMINS:
        return

    text = (message.text or "").strip()

    parts = text.split()

    if len(parts) != 2:
        return

    try:
        user_id = int(parts[0])
        amount = int(parts[1])

    except ValueError:

        await message.answer(
            "❌ Формат:\n"
            "<code>ID количество</code>",
            parse_mode="HTML"
        )

        return

    if amount <= 0:

        await message.answer(
            "❌ Количество должно быть больше 0."
        )

        return

    # По умолчанию сообщение вида:
    # 123456789 1000
    # = выдача 1000 звёзд

    change_stars(
        user_id,
        amount,
        f"Выдано администратором {message.from_user.id}"
    )

    await message.answer(
        "✅ <b>Звёзды выданы</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ +{amount}\n"
        f"💰 Баланс: <b>{get_balance(user_id)} ⭐</b>",
        parse_mode="HTML"
    )


# ============================================================
# START BOT
# ============================================================

async def main():

    init_database()

    print("----------------------------------------")
    print("🌊 Wavegram Cases Bot")
    print("----------------------------------------")
    print("API:", API_SERVER)
    print("Database: PostgreSQL")
    print("Admins:", ADMINS)
    print("----------------------------------------")

    try:

        me = await bot.get_me()

        print(
            f"✅ Бот подключён: "
            f"@{me.username}"
        )

    except Exception as error:

        print("❌ Ошибка подключения к Telegram API:")
        print(type(error).__name__)
        print(error)

        await bot.session.close()

        return

    try:

        await dp.start_polling(bot)

    finally:

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())