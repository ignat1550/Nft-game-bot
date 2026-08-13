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
# NFT / CASE CHANCES
# ============================================================

# Пул NFT для Лакшери. Сумма = 100%.
LUXURY_NFTS = [
    ("💎 Котел", 10),
    ("👑 Рюкз", 10),
    ("💐 Букет", 30),
    ("📖 Книга", 50),
]

# Пул NFT для золотого билета. Глазика здесь нет.
BILETER_NFTS = [
    ("💎 Котел", 10),
    ("👑 Рюкз", 15),
    ("🌌 Календарь", 25),
    ("💐 Букет", 25),
    ("📖 Книга", 25),
]


# ============================================================
# CHECK ENV
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )


# ============================================================
# TELEGRAM API
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
# STATES
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

            cur.execute("""
                CREATE TABLE IF NOT EXISTS nft_withdrawals (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    nft_id BIGINT NOT NULL,
                    nft_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    admin_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_nft_withdrawals_status
                ON nft_withdrawals(status)
            """)

        conn.commit()


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

def random_luxury_nft():

    number = random.uniform(0, 100)

    current = 0

    for name, chance in LUXURY_NFTS:

        current += chance

        if number <= current:
            return name

    return LUXURY_NFTS[-1][0]


def random_bileter_nft():

    number = random.uniform(0, 100)

    current = 0

    for name, chance in BILETER_NFTS:

        current += chance

        if number <= current:
            return name

    return BILETER_NFTS[-1][0]


def create_withdrawal(user_id: int, nft_id: int):
    """Создаёт заявку только если NFT принадлежит пользователю и ещё не выведена."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name
                FROM nfts
                WHERE id = %s AND user_id = %s
                FOR UPDATE
            """, (nft_id, user_id))
            nft = cur.fetchone()

            if not nft:
                return None, "not_found"

            cur.execute("""
                SELECT id
                FROM nft_withdrawals
                WHERE nft_id = %s
                  AND status = 'pending'
                LIMIT 1
            """, (nft_id,))

            if cur.fetchone():
                return None, "pending"

            cur.execute("""
                INSERT INTO nft_withdrawals
                    (user_id, nft_id, nft_name, status)
                VALUES
                    (%s, %s, %s, 'pending')
                RETURNING id
            """, (user_id, nft_id, nft["name"]))

            request = cur.fetchone()

        conn.commit()

    return {
        "id": int(request["id"]),
        "nft_id": int(nft["id"]),
        "name": nft["name"],
    }, "created"


def get_withdrawal(request_id: int):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, nft_id, nft_name, status, admin_id,
                       created_at, processed_at
                FROM nft_withdrawals
                WHERE id = %s
            """, (request_id,))
            return cur.fetchone()


def approve_withdrawal(request_id: int, admin_id: int):
    """Атомарно подтверждает заявку и удаляет NFT. Повторное нажатие не удалит NFT снова."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, nft_id, nft_name, status
                FROM nft_withdrawals
                WHERE id = %s
                FOR UPDATE
            """, (request_id,))
            request = cur.fetchone()

            if not request:
                conn.rollback()
                return None, "not_found"

            if request["status"] != "pending":
                conn.rollback()
                return request, "already_processed"

            cur.execute("""
                DELETE FROM nfts
                WHERE id = %s AND user_id = %s
                RETURNING id
            """, (request["nft_id"], request["user_id"]))
            deleted = cur.fetchone()

            if not deleted:
                cur.execute("""
                    UPDATE nft_withdrawals
                    SET status = 'rejected',
                        admin_id = %s,
                        processed_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'pending'
                """, (admin_id, request_id))
                conn.commit()
                request["status"] = "rejected"
                return request, "nft_missing"

            cur.execute("""
                UPDATE nft_withdrawals
                SET status = 'approved',
                    admin_id = %s,
                    processed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'pending'
                RETURNING status
            """, (admin_id, request_id))

            updated = cur.fetchone()
            if not updated:
                conn.rollback()
                return request, "already_processed"

        conn.commit()

    return request, "approved"


def reject_withdrawal(request_id: int, admin_id: int):
    """Отклоняет заявку. NFT при этом остаётся в инвентаре пользователя."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, nft_id, nft_name, status
                FROM nft_withdrawals
                WHERE id = %s
                FOR UPDATE
            """, (request_id,))
            request = cur.fetchone()

            if not request:
                conn.rollback()
                return None, "not_found"

            if request["status"] != "pending":
                conn.rollback()
                return request, "already_processed"

            cur.execute("""
                UPDATE nft_withdrawals
                SET status = 'rejected',
                    admin_id = %s,
                    processed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'pending'
                RETURNING status
            """, (admin_id, request_id))

            if not cur.fetchone():
                conn.rollback()
                return request, "already_processed"

        conn.commit()

    return request, "rejected"


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

    admins_text = "\n".join(
        f"• {admin}"
        for admin in PAYMENT_ADMINS
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Чтобы пополнить баланс ⭐, "
        "напиши одному из администраторов:\n\n"
        f"{admins_text}\n\n"
        "После оплаты отправь админу "
        "свой Telegram ID.\n\n"
        "🆔 Твой ID:\n"
        f"<code>{callback.from_user.id}</code>",
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

    user_nfts = get_nfts(user_id)

    await callback.message.edit_text(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: @{callback.from_user.username or 'нет'}\n"
        f"⭐ Баланс: <b>{get_balance(user_id)} ⭐</b>\n"
        f"🎁 NFT: <b>{len(user_nfts)}</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# NFT INVENTORY
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

        kb = InlineKeyboardBuilder()

        for nft in items:

            text += (
                f"🎁 <b>{escape(nft['name'])}</b>\n"
                f"📦 Источник: "
                f"{escape(nft['source'])}\n"
                f"ID: <code>{nft['id']}</code>\n\n"
            )

            kb.button(
                text=f"📤 Вывести #{nft['id']}",
                callback_data=f"withdraw_{nft['id']}"
            )

        kb.button(
            text="🔙 Назад",
            callback_data="home"
        )
        kb.adjust(1)

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
        await callback.answer()
        return

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
        "⭐ 50 звёзд — 90%\n"
        "🏆 Золотой билет — 10%\n\n"

        "💎 <b>Лакшери</b> — 2000 ⭐\n"
        "💎 Котел — 10%\n"
        "👑 Рюкз — 15%\n"
        "🌌 Букет — 25%\n"
        "🔥 Книга — 50%\n\n"

        "😵 <b>Наркоман</b> — 100 ⭐\n"
        "⭐ 50 звёзд — 95%\n"
        "🔥 Глазик — 5%",
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

    if random.random() < 0.97:

        change_stars(
            user_id,
            50,
            "Билитер: обычный билет"
        )

        result = (
            "🎫 <b>Билитер</b>\n\n"
            "🎫 Выпал обычный билет!\n\n"
            "⭐ Награда: <b>50 ⭐</b>"
        )

    else:

        nft = random_bileter_nft()

        add_nft(
            user_id,
            nft,
            "Золотой билет"
        )

        await notify_nft(
            user_id,
            callback.from_user.username,
            nft,
            "Билитер — золотой билет"
        )

        result = (
            "🏆 <b>ЗОЛОТОЙ БИЛЕТ!</b>\n\n"
            "🎁 Тебе выпал NFT:\n\n"
            f"<b>{escape(nft)}</b>"
        )

    await callback.message.edit_text(
        result,
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

    nft = random_luxury_nft()

    add_nft(
        user_id,
        nft,
        "Лакшери"
    )

    await notify_nft(
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

    if random.random() < 0.80:

        change_stars(
            user_id,
            50,
            "Наркоман: 50 звёзд"
        )

        result = (
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

        await notify_nft(
            user_id,
            callback.from_user.username,
            nft,
            "Наркоман"
        )

        result = (
            "😵 <b>НАРКОМАН!</b>\n\n"
            "👁 Выпал NFT:\n\n"
            f"<b>{escape(nft)}</b>"
        )

    await callback.message.edit_text(
        result,
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# NFT ADMIN NOTIFICATION
# ============================================================

async def notify_nft(
    user_id: int,
    username: str | None,
    nft_name: str,
    case_name: str
):

    user = (
        f"@{username}"
        if username
        else "без username"
    )

    text = (
        "🚨 <b>ВЫПАЛ NFT!</b>\n\n"
        f"👤 Игрок: {escape(user)}\n"
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

        except Exception:
            pass


# ============================================================
# NFT WITHDRAWAL
# ============================================================

@dp.callback_query(F.data.regexp(r"^withdraw_[0-9]+$"))
async def withdraw_nft(callback: CallbackQuery):

    try:
        nft_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный NFT.", show_alert=True)
        return

    request, status = create_withdrawal(
        callback.from_user.id,
        nft_id
    )

    if status == "not_found":
        await callback.answer(
            "❌ NFT не найден в твоём инвентаре.",
            show_alert=True
        )
        return

    if status == "pending":
        await callback.answer(
            "⏳ Заявка на этот NFT уже ожидает решения админа.",
            show_alert=True
        )
        return

    username = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else "без username"
    )

    admin_text = (
        "📤 <b>НОВАЯ ЗАЯВКА НА ВЫВОД NFT</b>\n\n"
        f"👤 Игрок: <b>{escape(username)}</b>\n"
        f"🆔 ID игрока: <code>{callback.from_user.id}</code>\n"
        f"🎁 NFT: <b>{escape(request['name'])}</b>\n"
        f"🆔 ID NFT: <code>{request['nft_id']}</code>\n"
        f"📋 Заявка № <code>{request['id']}</code>\n\n"
        "⏳ Статус: <b>Ожидает подтверждения</b>"
    )

    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Подтвердить вывод",
        callback_data=f"withdraw_approve_{request['id']}"
    )
    kb.button(
        text="❌ Отклонить",
        callback_data=f"withdraw_reject_{request['id']}"
    )
    kb.adjust(1)

    sent = 0
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
            sent += 1
        except Exception:
            pass

    await callback.message.edit_text(
        "📤 <b>Заявка на вывод создана!</b>\n\n"
        f"🎁 NFT: <b>{escape(request['name'])}</b>\n"
        f"📋 Заявка № <code>{request['id']}</code>\n\n"
        "⏳ Заявка отправлена администраторам.\n"
        "После подтверждения NFT будет удалён из твоего инвентаря.",
        parse_mode="HTML",
        reply_markup=back_menu()
    )

    await callback.answer("✅ Заявка отправлена!")


@dp.callback_query(F.data.startswith("withdraw_approve_"))
async def approve_nft_withdrawal(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    try:
        request_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверная заявка.", show_alert=True)
        return

    request, status = approve_withdrawal(
        request_id,
        callback.from_user.id
    )

    if status == "not_found":
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        return

    if status == "already_processed":
        await callback.answer(
            "⚠️ Эта заявка уже обработана другим админом.",
            show_alert=True
        )
        return

    if status == "nft_missing":
        await callback.answer(
            "⚠️ NFT уже отсутствует в инвентаре. Заявка закрыта.",
            show_alert=True
        )
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>NFT уже отсутствовал в инвентаре.</b>",
            parse_mode="HTML"
        )
        return

    admin_name = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else f"ID {callback.from_user.id}"
    )

    await callback.message.edit_text(
        (callback.message.text or "")
        + "\n\n"
        + f"✅ <b>Вывод подтверждён</b>\n"
        + f"👑 Админ: <b>{escape(admin_name)}</b>",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            request["user_id"],
            "✅ <b>Вывод NFT подтверждён!</b>\n\n"
            f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
            "NFT удалён из твоего инвентаря.\n\n"
            "📦 Ожидай дальнейшей информации от администрации.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("✅ Вывод подтверждён.")


@dp.callback_query(F.data.startswith("withdraw_reject_"))
async def reject_nft_withdrawal(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    try:
        request_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверная заявка.", show_alert=True)
        return

    request, status = reject_withdrawal(
        request_id,
        callback.from_user.id
    )

    if status == "not_found":
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        return

    if status == "already_processed":
        await callback.answer(
            "⚠️ Эта заявка уже обработана другим админом.",
            show_alert=True
        )
        return

    admin_name = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else f"ID {callback.from_user.id}"
    )

    await callback.message.edit_text(
        (callback.message.text or "")
        + "\n\n"
        + f"❌ <b>Вывод отклонён</b>\n"
        + f"👑 Админ: <b>{escape(admin_name)}</b>\n"
        + "🎁 NFT остался в инвентаре игрока.",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            request["user_id"],
            "❌ <b>Заявка на вывод NFT отклонена.</b>\n\n"
            f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
            "NFT остался в твоём инвентаре.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("❌ Заявка отклонена.")


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():

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

    return kb.as_markup()


@dp.message(Command("admin"))
async def admin_command(message: Message):

    if message.from_user.id not in ADMINS:

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    await message.answer(
        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выбери действие:",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN ADD STARS
# ============================================================

@dp.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )
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
        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )
        return

    await callback.message.answer(
        "➖ <b>Забрать звёзды</b>\n\n"
        "Отправь:\n"
        "<code>/remove ID количество</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# ADMIN NFT
# ============================================================

@dp.callback_query(F.data == "admin_giving")
async def admin_giving(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )
        return

    await callback.message.answer(
        "🎁 <b>Выдать NFT</b>\n\n"
        "Используй:\n"
        "<code>/giving ID NFT</code>\n\n"
        "Пример:\n"
        "<code>/giving 123456789 Котел</code>",
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
            "❌ Формат:\n"
            "<code>/giving ID NFT</code>\n\n"
            "Пример:\n"
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
            "❌ Укажи NFT."
        )
        return

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
            "❌ Пользователь ещё "
            "не запускал бота."
        )

        return

    add_nft(
        user_id,
        nft_name,
        f"Выдан администратором {message.from_user.id}"
    )

    admin_name = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"ID {message.from_user.id}"
    )

    notify_text = (
        "🎁 <b>NFT ВЫДАН</b>\n\n"
        f"👑 Админ: <b>{escape(admin_name)}</b>\n"
        f"🆔 ID админа: "
        f"<code>{message.from_user.id}</code>\n\n"
        f"👤 Получатель: "
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

        except Exception:
            pass

    try:

        await bot.send_message(
            user_id,
            "🎁 <b>Тебе выдали NFT!</b>\n\n"
            f"🖼 <b>{escape(nft_name)}</b>",
            parse_mode="HTML"
        )

    except Exception:
        pass

    await message.answer(
        "✅ <b>NFT выдан</b>\n\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"🎁 NFT: <b>{escape(nft_name)}</b>\n"
        f"👑 Выдал: <b>{escape(admin_name)}</b>",
        parse_mode="HTML"
    )


# ============================================================
# ADMIN STATS
# ============================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )
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
# BROADCAST BUTTON
# ============================================================

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_button(
    callback: CallbackQuery,
    state: FSMContext
):

    if callback.from_user.id not in ADMINS:

        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )

        return

    await state.set_state(
        BroadcastState.waiting_message
    )

    await callback.message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст сообщения, "
        "которое нужно отправить всем "
        "пользователям.\n\n"
        "Для отмены напиши:\n"
        "<code>отмена</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# /BROADCAST
# ============================================================

@dp.message(Command("broadcast"))
async def broadcast_command(
    message: Message,
    state: FSMContext
):

    if message.from_user.id not in ADMINS:

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    await state.set_state(
        BroadcastState.waiting_message
    )

    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст сообщения.\n\n"
        "Для отмены напиши:\n"
        "<code>отмена</code>",
        parse_mode="HTML"
    )


# ============================================================
# BROADCAST PROCESS
# ============================================================

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
            "❌ Отправь текстовое сообщение."
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

        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    await status.edit_text(
        "📢 <b>Рассылка завершена!</b>\n\n"
        f"👥 Всего: <b>{total}</b>\n"
        f"✅ Отправлено: <b>{success}</b>\n"
        f"❌ Ошибок: <b>{failed}</b>",
        parse_mode="HTML"
    )


# ============================================================
# GIVE STARS BY MESSAGE
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

    change_stars(
        user_id,
        amount,
        f"Выдано администратором {message.from_user.id}"
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
# MAIN
# ============================================================

async def main():

    init_database()

    try:

        await bot.get_me()

    except Exception:

        await bot.session.close()
        return

    try:

        await dp.start_polling(bot)

    finally:

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
