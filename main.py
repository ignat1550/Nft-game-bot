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
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

API_SERVER = os.getenv(
    "API_SERVER",
    "http://31.76.20.193:8081"
)

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
# ПРОВЕРКА НАСТРОЕК
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

if not DATABASE_URL:
    raise RuntimeError("Не задан DATABASE_URL")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )


# ============================================================
# TELEGRAM API
# ============================================================

api_server = TelegramAPIServer.from_base(API_SERVER)

session = AiohttpSession(
    api=api_server
)

bot = Bot(
    token=BOT_TOKEN,
    session=session
)

dp = Dispatcher()


# ============================================================
# СОСТОЯНИЯ
# ============================================================

class BroadcastState(StatesGroup):
    waiting_message = State()


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

            # ==================================================
            # ЗАЯВКИ НА ВЫВОД NFT
            # ==================================================

            cur.execute("""
                CREATE TABLE IF NOT EXISTS withdrawal_requests (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    nft_id BIGINT NOT NULL,
                    nft_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_by BIGINT,
                    processed_at TIMESTAMP DEFAULT NULL
                )
            """)

            # Индекс для быстрой проверки активной заявки
            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_withdrawal_nft_pending
                ON withdrawal_requests (nft_id, status)
            """)

        conn.commit()


# ============================================================
# USERS
# ============================================================

def ensure_user(user_id: int, username: str = ""):
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO users (user_id, username)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET username = EXCLUDED.username
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
            """, (user_id,))

            row = cur.fetchone()

            if row:
                return int(row["stars"])

    return 0


# ============================================================
# STARS
# ============================================================

def add_stars(
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


def take_stars(
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


# ============================================================
# NFT
# ============================================================

def add_nft(
    user_id: int,
    nft_name: str,
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
                nft_name,
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
            """, (user_id,))

            return cur.fetchall()


def get_nft(nft_id: int):
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    user_id,
                    name,
                    source,
                    created_at
                FROM nfts
                WHERE id = %s
            """, (nft_id,))

            return cur.fetchone()


# ============================================================
# ЗАЯВКИ НА ВЫВОД NFT
# ============================================================

def get_pending_withdrawal_for_nft(nft_id: int):
    """
    Возвращает активную заявку на NFT,
    если она существует.
    """

    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    user_id,
                    nft_id,
                    nft_name,
                    status,
                    created_at
                FROM withdrawal_requests
                WHERE nft_id = %s
                  AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
            """, (nft_id,))

            return cur.fetchone()


def get_withdrawal_request(request_id: int):
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    user_id,
                    nft_id,
                    nft_name,
                    status,
                    created_at,
                    processed_by,
                    processed_at
                FROM withdrawal_requests
                WHERE id = %s
            """, (request_id,))

            return cur.fetchone()


def create_withdrawal_request(
    user_id: int,
    nft_id: int
):
    """
    Создаёт заявку только если NFT действительно
    принадлежит пользователю и для него нет pending-заявки.
    """

    with db_connect() as conn:
        with conn.cursor() as cur:

            # Блокируем строку NFT на время операции.
            cur.execute("""
                SELECT
                    id,
                    user_id,
                    name,
                    source
                FROM nfts
                WHERE id = %s
                  AND user_id = %s
                FOR UPDATE
            """, (
                nft_id,
                user_id
            ))

            nft = cur.fetchone()

            if not nft:
                conn.rollback()
                return None, "nft_not_found"

            # Проверяем существующую заявку.
            cur.execute("""
                SELECT id
                FROM withdrawal_requests
                WHERE nft_id = %s
                  AND status = 'pending'
                LIMIT 1
            """, (nft_id,))

            existing = cur.fetchone()

            if existing:
                conn.rollback()
                return int(existing["id"]), "already_pending"

            cur.execute("""
                INSERT INTO withdrawal_requests
                    (
                        user_id,
                        nft_id,
                        nft_name,
                        status
                    )
                VALUES
                    (
                        %s,
                        %s,
                        %s,
                        'pending'
                    )
                RETURNING id
            """, (
                user_id,
                nft_id,
                nft["name"]
            ))

            request = cur.fetchone()

        conn.commit()

    return int(request["id"]), "created"


def approve_withdrawal(
    request_id: int,
    admin_id: int
):
    """
    Подтверждает вывод.
    Всё выполняется в одной транзакции.

    Благодаря FOR UPDATE два админа не смогут
    одновременно удалить один и тот же NFT.
    """

    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    user_id,
                    nft_id,
                    nft_name,
                    status
                FROM withdrawal_requests
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

            # Проверяем, что NFT ещё существует.
            cur.execute("""
                SELECT id
                FROM nfts
                WHERE id = %s
                  AND user_id = %s
                FOR UPDATE
            """, (
                request["nft_id"],
                request["user_id"]
            ))

            nft = cur.fetchone()

            if not nft:
                conn.rollback()
                return request, "nft_not_found"

            # Удаляем NFT.
            cur.execute("""
                DELETE FROM nfts
                WHERE id = %s
                  AND user_id = %s
            """, (
                request["nft_id"],
                request["user_id"]
            ))

            # Меняем статус заявки.
            cur.execute("""
                UPDATE withdrawal_requests
                SET
                    status = 'approved',
                    processed_by = %s,
                    processed_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                admin_id,
                request_id
            ))

        conn.commit()

    return request, "approved"


def reject_withdrawal(
    request_id: int,
    admin_id: int
):
    """
    Отклоняет заявку.
    NFT при этом не трогается.
    """

    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    user_id,
                    nft_id,
                    nft_name,
                    status
                FROM withdrawal_requests
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
                UPDATE withdrawal_requests
                SET
                    status = 'rejected',
                    processed_by = %s,
                    processed_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                admin_id,
                request_id
            ))

        conn.commit()

    return request, "rejected"


def get_pending_withdrawals_count():
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT COUNT(*) AS count
                FROM withdrawal_requests
                WHERE status = 'pending'
            """)

            row = cur.fetchone()

            return int(row["count"])


# ============================================================
# USERS / STATISTICS
# ============================================================

def get_all_users():
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT user_id
                FROM users
            """)

            rows = cur.fetchall()

    return [int(row["user_id"]) for row in rows]


def user_exists(user_id: int) -> bool:
    with db_connect() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT user_id
                FROM users
                WHERE user_id = %s
            """, (user_id,))

            return cur.fetchone() is not None


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

            cur.execute("""
                SELECT COUNT(*) AS count
                FROM withdrawal_requests
                WHERE status = 'pending'
            """)
            pending = cur.fetchone()["count"]

    return users, stars, nfts, pending


# ============================================================
# NFT ШАНСЫ
# ============================================================

LUXURY_NFTS = [
    ("💎 Котел", 10),
    ("👑 Рюкз", 20),
    ("🌌 Календарь", 30),
    ("🔥 Глазик", 40),
]


def random_luxury_nft():
    roll = random.uniform(0, 100)
    current = 0

    for nft_name, chance in LUXURY_NFTS:
        current += chance

        if roll <= current:
            return nft_name

    return LUXURY_NFTS[-1][0]


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def main_keyboard():
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


def back_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🔙 Назад",
        callback_data="home"
    )

    return kb.as_markup()


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


def withdrawal_admin_keyboard(request_id: int):
    kb = InlineKeyboardBuilder()

    kb.button(
        text="✅ Подтвердить вывод",
        callback_data=f"withdraw_approve:{request_id}"
    )

    kb.button(
        text="❌ Отклонить",
        callback_data=f"withdraw_reject:{request_id}"
    )

    kb.adjust(1)

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
        reply_markup=main_keyboard()
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# БАЛАНС
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
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ПОПОЛНЕНИЕ
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
        "После оплаты отправь админу свой "
        "Telegram ID.\n\n"
        "🆔 Твой ID:\n"
        f"<code>{callback.from_user.id}</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ПРОФИЛЬ
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username or ""
    )

    nft_count = len(get_nfts(user_id))

    username = (
        f"@{escape(callback.from_user.username)}"
        if callback.from_user.username
        else "нет"
    )

    await callback.message.edit_text(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Username: {username}\n"
        f"⭐ Баланс: "
        f"<b>{get_balance(user_id)} ⭐</b>\n"
        f"🎁 NFT: <b>{nft_count}</b>",
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# МОИ NFT
# ============================================================

@dp.callback_query(F.data == "nfts")
async def nfts(callback: CallbackQuery):

    user_id = callback.from_user.id

    items = get_nfts(user_id)

    if not items:

        await callback.message.edit_text(
            "🎒 <b>Мои NFT</b>\n\n"
            "У тебя пока нет NFT.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        await callback.answer()
        return

    text = "🎒 <b>Мои NFT</b>\n\n"

    kb = InlineKeyboardBuilder()

    for nft in items:

        pending = get_pending_withdrawal_for_nft(
            int(nft["id"])
        )

        text += (
            f"🎁 <b>{escape(nft['name'])}</b>\n"
            f"📦 {escape(nft['source'])}\n"
            f"🆔 ID: <code>{nft['id']}</code>\n"
        )

        if pending:

            text += (
                "⏳ <i>Заявка на вывод уже создана</i>\n\n"
            )

            kb.button(
                text=f"⏳ {nft['name']}",
                callback_data="nothing"
            )

        else:

            text += "\n"

            kb.button(
                text=f"📤 Вывести {nft['name']}",
                callback_data=f"withdraw:{nft['id']}"
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


# ============================================================
# СОЗДАНИЕ ЗАЯВКИ НА ВЫВОД
# ============================================================

@dp.callback_query(F.data.startswith("withdraw:"))
async def withdraw_nft(callback: CallbackQuery):

    user_id = callback.from_user.id

    try:
        nft_id = int(
            callback.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):

        await callback.answer(
            "❌ Неверный ID NFT.",
            show_alert=True
        )
        return

    nft = get_nft(nft_id)

    if not nft:

        await callback.answer(
            "❌ NFT не найден.",
            show_alert=True
        )
        return

    if int(nft["user_id"]) != user_id:

        await callback.answer(
            "⛔ Этот NFT тебе не принадлежит.",
            show_alert=True
        )
        return

    request_id, result = create_withdrawal_request(
        user_id,
        nft_id
    )

    if result == "nft_not_found":

        await callback.answer(
            "❌ NFT уже отсутствует.",
            show_alert=True
        )
        return

    if result == "already_pending":

        await callback.answer(
            "⏳ Заявка на этот NFT уже создана.",
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
        f"🆔 ID игрока: <code>{user_id}</code>\n\n"
        f"🎁 NFT: <b>{escape(nft['name'])}</b>\n"
        f"🆔 ID NFT: <code>{nft_id}</code>\n"
        f"📦 Источник: {escape(nft['source'])}\n\n"
        f"📝 Заявка №<code>{request_id}</code>\n\n"
        "Выберите действие:"
    )

    # Отправляем заявку ВСЕМ администраторам.
    for admin_id in ADMINS:

        try:

            await bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=withdrawal_admin_keyboard(
                    request_id
                )
            )

        except Exception:
            pass

    await callback.message.edit_text(
        "📤 <b>Заявка на вывод создана!</b>\n\n"
        f"🎁 NFT: <b>{escape(nft['name'])}</b>\n"
        f"🆔 ID NFT: <code>{nft_id}</code>\n"
        f"📝 Заявка №<code>{request_id}</code>\n\n"
        "⏳ Заявка отправлена администраторам.\n\n"
        "NFT пока остаётся у тебя в инвентаре.\n"
        "После подтверждения администратором "
        "он будет удалён.",
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )

    await callback.answer(
        "📤 Заявка отправлена!"
    )


# ============================================================
# КНОПКА ОЖИДАЮЩЕЙ ЗАЯВКИ
# ============================================================

@dp.callback_query(F.data == "nothing")
async def nothing(callback: CallbackQuery):

    await callback.answer(
        "⏳ Заявка на этот NFT уже ожидает решения.",
        show_alert=True
    )


# ============================================================
# АДМИН — ПОДТВЕРДИТЬ ВЫВОД
# ============================================================

@dp.callback_query(
    F.data.startswith("withdraw_approve:")
)
async def withdraw_approve(
    callback: CallbackQuery
):

    if callback.from_user.id not in ADMINS:

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    try:

        request_id = int(
            callback.data.split(":", 1)[1]
        )

    except (ValueError, IndexError):

        await callback.answer(
            "❌ Неверная заявка.",
            show_alert=True
        )
        return

    request, result = approve_withdrawal(
        request_id,
        callback.from_user.id
    )

    if not request:

        await callback.answer(
            "❌ Заявка не найдена.",
            show_alert=True
        )
        return

    if result == "already_processed":

        await callback.answer(
            "⚠️ Эта заявка уже обработана.",
            show_alert=True
        )
        return

    if result == "nft_not_found":

        await callback.answer(
            "❌ NFT уже отсутствует в инвентаре.",
            show_alert=True
        )
        return

    admin_name = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else f"ID {callback.from_user.id}"
    )

    result_text = (
        "✅ <b>ВЫВОД NFT ПОДТВЕРЖДЁН</b>\n\n"
        f"📝 Заявка: <code>{request_id}</code>\n"
        f"👤 Игрок: <code>{request['user_id']}</code>\n"
        f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
        f"🆔 NFT ID: <code>{request['nft_id']}</code>\n\n"
        f"👑 Подтвердил: <b>{escape(admin_name)}</b>\n"
        f"🆔 ID админа: <code>{callback.from_user.id}</code>\n\n"
        "🗑 NFT удалён из инвентаря пользователя."
    )

    # Меняем сообщение с заявкой.
    try:

        await callback.message.edit_text(
            result_text,
            parse_mode="HTML"
        )

    except Exception:

        await callback.message.answer(
            result_text,
            parse_mode="HTML"
        )

    # Уведомляем игрока.
    try:

        await bot.send_message(
            int(request["user_id"]),
            "✅ <b>Вывод NFT подтверждён!</b>\n\n"
            f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
            f"🆔 ID: <code>{request['nft_id']}</code>\n\n"
            "Администратор подтвердил вывод.\n"
            "NFT удалён из твоего инвентаря.",
            parse_mode="HTML"
        )

    except Exception:
        pass

    await callback.answer(
        "✅ Вывод подтверждён!"
    )


# ============================================================
# АДМИН — ОТКЛОНИТЬ ВЫВОД
# ============================================================

@dp.callback_query(
    F.data.startswith("withdraw_reject:")
)
async def withdraw_reject(
    callback: CallbackQuery
):

    if callback.from_user.id not in ADMINS:

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    try:

        request_id = int(
            callback.data.split(":", 1)[1]
        )

    except (ValueError, IndexError):

        await callback.answer(
            "❌ Неверная заявка.",
            show_alert=True
        )
        return

    request, result = reject_withdrawal(
        request_id,
        callback.from_user.id
    )

    if not request:

        await callback.answer(
            "❌ Заявка не найдена.",
            show_alert=True
        )
        return

    if result == "already_processed":

        await callback.answer(
            "⚠️ Эта заявка уже обработана.",
            show_alert=True
        )
        return

    admin_name = (
        f"@{callback.from_user.username}"
        if callback.from_user.username
        else f"ID {callback.from_user.id}"
    )

    result_text = (
        "❌ <b>ВЫВОД NFT ОТКЛОНЁН</b>\n\n"
        f"📝 Заявка: <code>{request_id}</code>\n"
        f"👤 Игрок: <code>{request['user_id']}</code>\n"
        f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
        f"🆔 NFT ID: <code>{request['nft_id']}</code>\n\n"
        f"👑 Отклонил: <b>{escape(admin_name)}</b>\n"
        f"🆔 ID админа: <code>{callback.from_user.id}</code>\n\n"
        "🎒 NFT остался в инвентаре пользователя."
    )

    try:

        await callback.message.edit_text(
            result_text,
            parse_mode="HTML"
        )

    except Exception:

        await callback.message.answer(
            result_text,
            parse_mode="HTML"
        )

    # Уведомляем игрока.
    try:

        await bot.send_message(
            int(request["user_id"]),
            "❌ <b>Заявка на вывод отклонена</b>\n\n"
            f"🎁 NFT: <b>{escape(request['nft_name'])}</b>\n"
            f"🆔 ID: <code>{request['nft_id']}</code>\n\n"
            "NFT остался в твоём инвентаре.",
            parse_mode="HTML"
        )

    except Exception:
        pass

    await callback.answer(
        "❌ Вывод отклонён."
    )


# ============================================================
# КЕЙСЫ
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
        "🎫 Обычный билет — 80%\n"
        "🏆 Золотой билет — 20%\n"
        "🏆 Золотой билет даёт NFT.\n\n"

        "💎 <b>Лакшери</b> — 2000 ⭐\n"
        "💎 Котел — 10%\n"
        "👑 Рюкз — 20%\n"
        "🌌 Календарь — 30%\n"
        "🔥 Глазик — 40%\n"

        "😵 <b>Наркоман</b> — 100 ⭐\n"
        "⭐ 50 звёзд — 80%\n"
        "🔥 Глазик — 20%",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# БИЛИТЕР
# ============================================================

@dp.callback_query(F.data == "case_bileter")
async def bileter(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not take_stars(
        user_id,
        100,
        "Кейс Билитер"
    ):

        await callback.answer(
            "❌ Нужно 100 ⭐",
            show_alert=True
        )

        return

    if random.random() < 0.80:

        add_stars(
            user_id,
            50,
            "Билитер — обычный билет"
        )

        result = (
            "🎫 <b>Билитер</b>\n\n"
            "🎫 Выпал обычный билет!\n\n"
            "⭐ Получено: <b>50 ⭐</b>"
        )

    else:

        nft = random_luxury_nft()

        add_nft(
            user_id,
            nft,
            "Билитер — золотой билет"
        )

        await notify_nft(
            user_id,
            callback.from_user.username,
            nft,
            "Билитер — золотой билет"
        )

        result = (
            "🏆 <b>ЗОЛОТОЙ БИЛЕТ!</b>\n\n"
            f"🎁 NFT: <b>{escape(nft)}</b>\n\n"
            "Поздравляем! 🔥"
        )

    await callback.message.edit_text(
        result,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ЛАКШЕРИ
# ============================================================

@dp.callback_query(F.data == "case_luxury")
async def luxury(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not take_stars(
        user_id,
        2000,
        "Кейс Лакшери"
    ):

        await callback.answer(
            "❌ Нужно 2000 ⭐",
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
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# НАРКОМАН
# ============================================================

@dp.callback_query(F.data == "case_nark")
async def nark(callback: CallbackQuery):

    user_id = callback.from_user.id

    if not take_stars(
        user_id,
        100,
        "Кейс Наркоман"
    ):

        await callback.answer(
            "❌ Нужно 100 ⭐",
            show_alert=True
        )

        return

    if random.random() < 0.80:

        add_stars(
            user_id,
            50,
            "Наркоман — 50 звёзд"
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
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# УВЕДОМЛЕНИЕ О NFT
# ============================================================

async def notify_nft(
    user_id: int,
    username: str | None,
    nft_name: str,
    case_name: str
):

    if username:
        player = f"@{username}"
    else:
        player = "без username"

    text = (
        "🚨 <b>ВЫПАЛ NFT!</b>\n\n"
        f"👤 Игрок: <b>{escape(player)}</b>\n"
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
# АДМИНКА
# ============================================================

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
# АДМИН — ВЫДАТЬ ЗВЁЗДЫ
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
        "Напиши:\n"
        "<code>/give ID количество</code>\n\n"
        "Пример:\n"
        "<code>/give 123456789 1000</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# /GIVE
# ============================================================

@dp.message(Command("give"))
async def give_command(message: Message):

    if message.from_user.id not in ADMINS:
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.answer(
            "❌ Используй:\n"
            "<code>/give ID количество</code>",
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

    ensure_user(user_id)

    add_stars(
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
# АДМИН — ЗАБРАТЬ ЗВЁЗДЫ
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
        "Напиши:\n"
        "<code>/remove ID количество</code>\n\n"
        "Пример:\n"
        "<code>/remove 123456789 500</code>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# /REMOVE
# ============================================================

@dp.message(Command("remove"))
async def remove_command(message: Message):

    if message.from_user.id not in ADMINS:
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.answer(
            "❌ Используй:\n"
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

    success = take_stars(
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
# АДМИН — ВЫДАТЬ NFT
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
        "<code>/giving ID название NFT</code>\n\n"
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
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(
        maxsplit=2
    )

    if len(parts) < 3:

        await message.answer(
            "❌ Используй:\n"
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
            "❌ Укажи название NFT."
        )
        return

    if not user_exists(user_id):

        await message.answer(
            "❌ Этот пользователь ещё "
            "не запускал бота."
        )
        return

    add_nft(
        user_id,
        nft_name,
        f"Выдан администратором {message.from_user.id}"
    )

    if message.from_user.username:
        admin_name = (
            f"@{message.from_user.username}"
        )
    else:
        admin_name = (
            f"ID {message.from_user.id}"
        )

    notification = (
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
                notification,
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
# СТАТИСТИКА
# ============================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMINS:
        await callback.answer(
            "⛔ Нет доступа",
            show_alert=True
        )
        return

    users, stars, nfts, pending = get_statistics()

    await callback.message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👤 Пользователей: <b>{users}</b>\n"
        f"⭐ Всего звёзд: <b>{stars}</b>\n"
        f"🎁 NFT: <b>{nfts}</b>\n"
        f"📤 Ожидающих выводов: <b>{pending}</b>",
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# РАССЫЛКА — КНОПКА
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
        "Отправь сообщение, которое нужно "
        "разослать всем пользователям.\n\n"
        "Можно отправить обычный текст.\n\n"
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
            "⛔ Нет доступа."
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
# РАССЫЛКА — ПОЛУЧЕНИЕ ТЕКСТА
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

    users = get_all_users()

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
# НЕИЗВЕСТНЫЕ КОМАНДЫ
# ============================================================

@dp.message(F.text.startswith("/"))
async def unknown_command(message: Message):

    if message.from_user.id in ADMINS:

        await message.answer(
            "❓ Неизвестная команда."
        )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    init_database()

    await bot.get_me()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
