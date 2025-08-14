import os
import re
from datetime import datetime, timedelta
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

import stripe
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

from fastapi import FastAPI, Request, Header, HTTPException

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ======================
# Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"
STRIPE_RENEW_URL     = STRIPE_MONTHLY_URL

VIP_INVITE_LINK = os.getenv("VIP_INVITE_LINK", "https://t.me/+SEU_LINK_VIP_AQUI")

def _parse_group_ids(raw: str) -> List[int]:
    ids = []
    for p in (raw or "").split(","):
        p = p.strip()
        if p:
            try:
                ids.append(int(p))
            except ValueError:
                pass
    return ids

VIP_GROUP_IDS: List[int] = _parse_group_ids(os.getenv("VIP_GROUP_IDS", ""))

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    engine = None

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# ======================
# DB
# ======================
DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subscribers (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE,
    customer_id TEXT,
    subscription_id TEXT,
    plan TEXT,
    status TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

def db_setup():
    if not engine:
        print("[DB] Sem DB configurado.")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))

def upsert_subscriber(email: Optional[str], customer_id: Optional[str],
                      subscription_id: Optional[str], plan: Optional[str], status: str):
    if not engine or not (email or customer_id):
        return
    sql = text("""
        INSERT INTO subscribers (email, customer_id, subscription_id, plan, status, updated_at)
        VALUES (:email, :customer_id, :subscription_id, :plan, :status, NOW())
        ON CONFLICT (email) DO UPDATE SET
            customer_id = COALESCE(EXCLUDED.customer_id, subscribers.customer_id),
            subscription_id = COALESCE(EXCLUDED.subscription_id, subscribers.subscription_id),
            plan = COALESCE(EXCLUDED.plan, subscribers.plan),
            status = EXCLUDED.status,
            updated_at = NOW();
    """)
    with engine.begin() as conn:
        conn.execute(sql, dict(
            email=(email.lower() if email else None),
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan=plan,
            status=status
        ))

def get_by_email(email: str) -> Optional[dict]:
    if not engine:
        return None
    sql = text("SELECT email, customer_id, subscription_id, plan, status FROM subscribers WHERE email = :email")
    with engine.begin() as conn:
        row = conn.execute(sql, {"email": email.lower()}).mappings().first()
        return dict(row) if row else None

def set_status_by_customer(customer_id: str, status: str, subscription_id: Optional[str] = None):
    if not engine:
        return
    sql = text("""
        UPDATE subscribers
        SET status = :status,
            subscription_id = COALESCE(:subscription_id, subscription_id),
            updated_at = NOW()
        WHERE customer_id = :customer_id
    """)
    with engine.begin() as conn:
        conn.execute(sql, {"status": status, "subscription_id": subscription_id, "customer_id": customer_id})

# ======================
# Bot Texts
# ======================
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "**1️⃣ Choose Your Plan**\n"
    "Tap on **🌟 Plans** and pick Monthly, Quarterly, or Annual.\n\n"
    "**2️⃣ Complete Your Payment (Stripe)**\n"
    "Use your email normally.\n\n"
    "**3️⃣ Unlock Your VIP Access**\n"
    "Come back to this bot and tap **🔓 Unlock Access**.\n"
    "Enter the **email** you used in Stripe. If active, you'll receive your VIP invite(s).\n\n"
    "💡 Tip: If you have any issues, tap **🆘 Support**."
)

# ======================
# Bot Handlers
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🆘 Support", url="https://t.me/Sthefano_p"),
            InlineKeyboardButton("🔁 Renew", callback_data="renew")
        ],
        [
            InlineKeyboardButton("🔓 Unlock Access", callback_data="unlock.access"),
            InlineKeyboardButton("🌟 Plans", callback_data="plans.open")
        ],
        [
            InlineKeyboardButton("🎁 Free Group", url="https://t.me/lukaeurope77"),
            InlineKeyboardButton("ℹ️ How It Works", callback_data="howitworks")
        ],
        [
            InlineKeyboardButton("🌐 Sales Website", url="https://lukamagiceurope.com")
        ]
    ]
    await update.effective_message.reply_text("✅ Welcome! Please choose an option:", reply_markup=InlineKeyboardMarkup(keyboard))

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: {update.effective_user.id}")

async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"📌 Group Name: {chat.title or 'Private Chat'}\n🆔 Group ID: `{chat.id}`",
        parse_mode="Markdown"
    )

async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🌟 <b>Luka Magic Europe – Plans</b>\n\n"
        "💶 <s>€50</s> → <b>€30</b>\n"
        "<i>€30 / month – 40% off</i>\n\n"
        "📊 <s>€150</s> → <b>€80</b>\n"
        "<i>€26.67 / month – 46% off</i>\n\n"
        "🏆 <s>€600</s> → <b>€270</b>\n"
        "<i>€22.50 / month – 55% off</i>"
    )
    keyboard = [
        [InlineKeyboardButton("💶 Monthly – €30", url=STRIPE_MONTHLY_URL)],
        [InlineKeyboardButton("📊 Quarterly – €80", url=STRIPE_QUARTERLY_URL)],
        [InlineKeyboardButton("🏆 Annual – €270", url=STRIPE_ANNUAL_URL)],
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=HOW_IT_WORKS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]])
    )

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🔁 Renew Now", url=STRIPE_RENEW_URL)],
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]
    ]
    await query.edit_message_text(
        text="🔁 **Renew your subscription below:**",
        reply_markup=
