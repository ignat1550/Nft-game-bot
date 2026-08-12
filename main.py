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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

API_SERVER = os.getenv(
    "API_SERVER",
    "http://31.76.20.193:8081"
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

ADMINS = {
    1780243345,
    1780243308,
    1780243378,
}

PAYMENT_ADMINS = [
    "@doxme",
    "@modeevil",
    "@bogkm",
]


# ============================================================
# NFT
# ============================================================

LUXURY_NFTS = [
    {
        "name": "💎 Котел",
        "chance": 10,
    },
    {
        "name": "👑 Рюкз",
        "chance": 15,
    },
    {
        "name": "🌌 Календарь",
        "chance": 15,
    },
    {
        "name": "🔥 Глазик",
        "chance": 20,
    },
    {
        "name": "🦋 Торт за 50 зв",
        "chance": 40,
    },
]


# ============================================================
# CONFIG CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN в Railway Variables."
    )

if not DATABASE_URL:
    raise RuntimeError(
        "Не задан DATABASE_URL. "
        "Подключи PostgreSQL к Railway."
    )

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
# FSM
# ============================================================

class BroadcastState(StatesGroup):
    waiting_message = State()


class AddStarsState(StatesGroup):
    waiting_data = State()


class RemoveStarsState(StatesGroup):
    waiting_data = State()


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

    print("✅ Database initialized")


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

    ensure_user(user_id)

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


def get_all_user_ids():

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT user_id
                FROM users
            """)

            rows = cur.fetchall()

    return [
        int(row["user_id"])
        for row in rows
    ]


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
# RANDOM NFT
# ============================================================

def get_random_luxury_nft():

    roll = random.uniform(
        0,
        100
    )

    current = 0

    for item in LUXURY_NFTS:

        current += item["chance"]

        if roll <= current:
            return item["name"]

    return LUXURY_NFTS[-1]["name"]


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
# START
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):

    ensure_user(
        message.from_user.id,
        message.from_user.username or ""
    )

    await message.answer(
        "🌊 <b>Wavegram Cases</b>\n\n"
        "Добро пожаловать!\n\n"
        "⭐ Звёзды\n"
        "🎁 Кейсы\n"
        "🎒 NFT\n\n"
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

    ensure_user(
        callback.from_user.id,
        callback.from_user.username or ""
    )

    stars = get_balance(
        callback.from_user.id
    )

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

    admins = "\n".join(
        f"• {x}"
        for x in PAYMENT_ADMINS
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Для пополнения ⭐ напиши одному "
        "из администраторов:\n\n"
        f"{admins}\n\n"
        "После оплаты отправь админу "
        "свой Telegram ID.\n\n"
        "🆔 Твой ID:\n"
        f"<code>{callback.from_user.id}</code>\n\n"
        "После проверки оплаты администратор "
        "зачислит звёзды.",
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
        f"🎁 NFT: <b>{len(nfts)}</b>",
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
                f"🎁 <b>{escape(nft['name'])}</b>\n"
                f"📦 Получен: {escape(nft['source'])}\n"
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

        "🎫 <b>Билитер</b> — 100 ⭐\n"
        "97% → 50 ⭐\n"
        "3% → золотой билет → NFT\n\n"

        "💎 <b>Лакшери</b> — 2000 ⭐\n"
        "💎 Котел — 10%\n"
        "👑 Рюкз — 15%\n"
        "🌌 Календарь — 15%\n"
        "🔥 Глазик — 20%\n"
        "🦋 Торт — 40%\n\n"

        "😵 <b>Наркоман</b> — 100 ⭐\n"
        "95% → 50 ⭐\n"
        "5% → Глазик",
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
        "Кейс Билитер"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 100 ⭐",
            show_alert=True
        )

        return

    # 97% обычный билет
    # 3% золотой билет

    if random.random() < 0.97:

        change_stars(
            user_id,
            50,
            "Билитер: обычный билет"
        )

        text = (
            "🎫 <b>Билитер</b>\n\n"
            "🎫 Выпал обычный билет!\n\n"
            "⭐ Награда: <b>50 ⭐</b>"
        )

    else:

        nft = get_random_luxury_nft()

        add_nft(
            user_id,
            nft,
            "Золотой билет"
        )

        await notify_admins(
            user_id,
            callback.from_user.username,
            nft,
            "Билитер — золотой билет"
        )

        text = (
            "🏆 <b>ЗОЛОТОЙ БИЛЕТ!</b> 🔥\n\n"
            "Тебе выпал случайный NFT:\n\n"
            f"🎁 <b>{escape(nft)}</b>"
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
        "Кейс Лакшери"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 2000 ⭐",
            show_alert=True
        )

        return

    nft = get_random_luxury_nft()

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
        "🎁 Тебе выпал:\n\n"
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
        "Кейс Наркоман"
    ):

        await callback.answer(
            "❌ Недостаточно звёзд. Нужно 100 ⭐",
            show_alert=True
        )

        return

    if random.random() < 0.95:

        change_stars(
            user_id,
            50,
            "Наркоман: 50 звёзд"
        )

        text = (
            "😵 <b>Наркоман</b>\n\n"
            "⭐ Выпало: <b>50 ⭐</b>"
        )

    else:

        nft = "🔥 Глазик"

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
            "👁 Выпал NFT:\n\n"
            f"<b>{escape(nft)}</b>"
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# NOTIFY ADMINS ABOUT NFT
# ============================================================

async def notify_admins(
    user_id: int,
    username: str | None,
    nft_name: str,
    case_name: str
):

    user_text = (
        f"@{username}"
        if username
        else "без username"
    )

    text = (
        "🚨 <b>ВЫПАЛ NFT!</b>\n\n"
        f"👤 Игрок: {escape(user_text)}\n"
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
                f"NFT notification error "
                f"{admin_id}: {error}"
            )


# ============================================================
# ADMIN PANEL
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
        text="🎁 Выдать NFT",
        callback_data="admin_giving"
    )

    kb.button(
        text="📊 Статистика",
        callback_data="admin_stats"
    )

    kb.button(
        text="📢 Рассылка",
        callback_data="admin_broadcast"
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
        "Отправь:\n"
        "<code>ID количество</code>\n\n"
        "Например:\n"
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
        "Отправь:\n"
        "<code>ID количество</code>\n\n"
        "Например:\n"
        "<code>123456789 500</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# ADMIN GIVING
# ============================================================

@dp.callback_query(F.data == "admin_giving")
async def admin_giving(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        return

    await callback.message.answer(
        "🎁 <b>Выдать NFT</b>\n\n"
        "Используй команду:\n\n"
        "<code>/giving ID название_NFT</code>\n\n"
        "Например:\n"
        "<code>/giving 123456789 Котел</code>\n\n"
        "Или:\n"
        "<code>/giving 123456789 Глазик</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# /GIVING
# ============================================================

@dp.message(Command("giving"))
async def giving(message: Message):

    if message.from_user.id not in ADMINS:

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 3:

        await message.answer(
            "❌ Неверный формат.\n\n"
            "Используй:\n"
            "<code>/giving ID NFT</code>\n\n"
            "Например:\n"
            "<code>/giving 123456789 Котел</code>",
            parse_mode="HTML"
        )

        return

    try:

        user_id = int(parts[1])

    except ValueError:

        await message.answer(
            "❌ ID должен быть числом."
        )

        return

    nft_name = parts[2].strip()

    if not nft_name:

        await message.answer(
            "❌ Укажи название NFT."
        )

        return

    # Проверяем существование пользователя
    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT user_id
                FROM users
                WHERE user_id = %s
            """, (
                user_id,
            ))

            user = cur.fetchone()

    if not user:

        await message.answer(
            "❌ Этот пользователь ещё "
            "не запускал бота."
        )

        return

    # Выдаём NFT
    add_nft(
        user_id,
        nft_name,
        f"Выдан администратором {message.from_user.id}"
    )

    # Имя администратора
    admin_username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"ID {message.from_user.id}"
    )

    # Сообщение ВСЕМ администраторам
    notify_text = (
        "🎁 <b>ВЫДАН NFT</b>\n\n"
        f"👑 Админ: <b>{escape(admin_username)}</b>\n"
        f"🆔 ID админа: "
        f"<code>{message.from_user.id}</code>\n\n"
        f"👤 Получатель ID: "
        f"<code>{user_id}</code>\n"
        f"🎁 NFT: <b>{escape(nft_name)}</b>"
    )

    for admin_id in ADMINS:

        try:

            await bot.send_message(
                admin_id,
                notify_text,
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                f"Giving notification error "
                f"{admin_id}: {error}"
            )

    # Уведомляем получателя
    try:

        await bot.send_message(
            user_id,
            "🎁 <b>Тебе выдали NFT!</b>\n\n"
            f"🖼 <b>{escape(nft_name)}</b>",
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            f"User notification error "
            f"{user_id}: {error}"
        )

    await message.answer(
        "✅ <b>NFT выдан</b>\n\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"🎁 NFT: <b>{escape(nft_name)}</b>\n"
        f"👑 Выдал: "
        f"<b>{escape(admin_username)}</b>",
        parse_mode="HTML"
    )


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
        f"⭐ Звёзд: <b>{stars}</b>\n"
        f"🎁 NFT: <b>{nfts}</b>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# BROADCAST
# ============================================================

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext
):

    if callback.from_user.id not in ADMINS:
        return

    await state.set_state(
        BroadcastState.waiting_message
    )

    await callback.message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст, который нужно "
        "разослать всем пользователям.\n\n"
        "Для отмены:\n"
        "<code>отмена</code>",
        parse_mode="HTML"
    )

    await callback.answer()


@dp.message(BroadcastState.waiting_message)
async def process_broadcast(
    message: Message,
    state: FSMContext
):

    if message.from_user.id not in ADMINS:
        return

    text = (message.text or "").strip()

    if text.lower() == "отмена":

        await state.clear()

        await message.answer(
            "❌ Рассылка отменена."
        )

        return

    if not text:

        await message.answer(
            "❌ Отправь текст."
        )

        return

    await state.clear()

    users = get_all_user_ids()

    total = len(users)
    success = 0
    failed = 0

    status = await message.answer(
        "📢 <b>Рассылка началась...</b>\n\n"
        f"👥 Получателей: <b>{total}</b>",
        parse_mode="HTML"
    )

    for user_id in users:

        try:

            await bot.send_message(
                user_id,
                text
            )

            success += 1

        except Exception as error:

            failed += 1

            print(
                f"Broadcast error "
                f"{user_id}: {error}"
            )

        await asyncio.sleep(0.05)

    await status.edit_text(
        "📢 <b>Рассылка завершена!</b>\n\n"
        f"👥 Всего: <b>{total}</b>\n"
        f"✅ Отправлено: <b>{success}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
        parse_mode="HTML"
    )


# ============================================================
# ADMIN TEXT
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

        return

    if amount <= 0:
        return

    # Этот обработчик используется
    # после кнопки "Выдать звёзды".
    #
    # Для безопасности ниже выдаём звёзды.
    # Снятие делается через отдельную команду /remove.

    change_stars(
        user_id,
        amount,
        f"Выдано админом {message.from_user.id}"
    )

    await message.answer(
        "✅ <b>Звёзды выданы</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ +{amount}\n"
        f"💰 Баланс: "
        f"<b>{get_balance(user_id)} ⭐</b>",
        parse_mode="HTML"
    )


# ============================================================
# /REMOVE
# ============================================================

@dp.message(Command("remove"))
async def remove_command(message: Message):

    if message.from_user.id not in ADMINS:
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.answer(
            "Используй:\n"
            "<code>/remove ID количество</code>",
            parse_mode="HTML"
        )

        return

    try:

        user_id = int(parts[1])
        amount = int(parts[2])

    except ValueError:

        await message.answer(
            "❌ ID и количество должны быть числами."
        )

        return

    if amount <= 0:

        await message.answer(
            "❌ Количество должно быть больше 0."
        )

        return

    success = remove_stars(
        user_id,
        amount,
        f"Снято администратором {message.from_user.id}"
    )

    if not success:

        await message.answer(
            "❌ Недостаточно звёзд "
            "или пользователь не найден."
        )

        return

    await message.answer(
        "✅ <b>Звёзды сняты</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ -{amount}\n"
        f"💰 Баланс: "
        f"<b>{get_balance(user_id)} ⭐</b>",
        parse_mode="HTML"
    )


# ============================================================
# RUN
# ============================================================

async def main():

    print("----------------------------------------")
    print("🌊 Wavegram Cases Bot")
    print("----------------------------------------")
    print("API:", API_SERVER)
    print("Admins:", ADMINS)
    print("----------------------------------------")

    init_database()

    try:

        me = await bot.get_me()

        print(
            f"✅ Бот подключён: @{me.username}"
        )

    except Exception as error:

        print("❌ Ошибка подключения:")
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
