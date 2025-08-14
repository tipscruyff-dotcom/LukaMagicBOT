import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List

# ---------- ENV ----------
from dotenv import load_dotenv
load_dotenv()

# ---------- Telegram ----------
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, ApplicationBuilder,
    CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

# ---------- FastAPI (Stripe webhook) ----------
from fastapi import FastAPI, Request, Header, HTTPException

# ---------- Stripe ----------
import stripe

# ---------- DB ----------
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ======================
# CONFIG
# ======================
TOKEN = os.getenv("BOT_TOKEN", "").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

# Links do Stripe (pagamento pelo botão Plans)
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

# Convite padrão (caso não consigamos criar 1-uso)
VIP_INVITE_LINK = os.getenv("VIP_INVITE_LINK", "").strip()

def _parse_group_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for p in (raw or "").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except ValueError:
            pass
    return ids

VIP_GROUP_IDS: List[int] = _parse_group_ids(os.getenv("VIP_GROUP_IDS", ""))

# DB URL
DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://")

# Stripe init
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# ======================
# DB init + helpers
# ======================
engine = None
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subscribers (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE,
    telegram_id TEXT,
    customer_id TEXT,
    subscription_id TEXT,
    plan TEXT,
    status TEXT,                      -- active, trialing, past_due, canceled
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

def db_setup():
    if not engine:
        print("[DB] DATABASE_URL não configurado — rodando sem DB!")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))
    print("[DB] Tabela 'subscribers' ok.")

def upsert_subscriber(*, email: Optional[str], telegram_id: Optional[str],
                      customer_id: Optional[str], subscription_id: Optional[str],
                      plan: Optional[str], status: str):
    if not engine:
        print("[DB] skip upsert (sem DB).", email, status)
        return
    if not (email or customer_id or telegram_id):
        print("[DB] upsert ignorado (sem chave de identificação)")
        return
    sql = text("""
        INSERT INTO subscribers (email, telegram_id, customer_id, subscription_id, plan, status, updated_at)
        VALUES (:email, :telegram_id, :customer_id, :subscription_id, :plan, :status, NOW())
        ON CONFLICT (email) DO UPDATE SET
            telegram_id = COALESCE(EXCLUDED.telegram_id, subscribers.telegram_id),
            customer_id = COALESCE(EXCLUDED.customer_id, subscribers.customer_id),
            subscription_id = COALESCE(EXCLUDED.subscription_id, subscribers.subscription_id),
            plan = COALESCE(EXCLUDED.plan, subscribers.plan),
            status = EXCLUDED.status,
            updated_at = NOW();
    """)
    with engine.begin() as conn:
        conn.execute(sql, dict(
            email=(email.lower() if email else None),
            telegram_id=(str(telegram_id) if telegram_id else None),
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan=plan,
            status=status
        ))
    print(f"[DB] upsert {email or customer_id or telegram_id}: {status}")

def get_by_email(email: str) -> Optional[dict]:
    if not engine:
        return None
    sql = text("SELECT email, telegram_id, customer_id, subscription_id, plan, status FROM subscribers WHERE email = :email")
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
    print(f"[DB] status {customer_id} -> {status}")

def set_telegram_for_email(email: str, telegram_id: str):
    if not engine:
        return
    sql = text("""
        UPDATE subscribers
        SET telegram_id = :telegram_id,
            updated_at = NOW()
        WHERE email = :email
    """)
    with engine.begin() as conn:
        conn.execute(sql, {"telegram_id": str(telegram_id), "email": email.lower()})
    print(f"[DB] vinculado telegram_id {telegram_id} ao email {email}")

# ======================
# Textos do bot
# ======================
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "**1️⃣ Choose Your Plan**\n"
    "Tap on **🌟 Plans** and pick Monthly, Quarterly, or Annual.\n\n"
    "**2️⃣ Complete Your Payment (Stripe)**\n"
    "Use your email normally.\n\n"
    "**3️⃣ Unlock Your VIP Access**\n"
    "Tap **🔓 Unlock Access** and type the same email you used on Stripe.\n"
    "If active, you'll receive your VIP invite(s).\n\n"
    "💡 If you need help, tap **🆘 Support**."
)

# ======================
# BOT
# ======================
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🆘 Support", url="https://t.me/Sthefano_p"),
            InlineKeyboardButton("🆔 My ID", callback_data="myid.show"),
        ],
        [
           
