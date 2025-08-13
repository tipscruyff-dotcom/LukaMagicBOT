import os
import re
import stripe
from datetime import datetime
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from fastapi import FastAPI, Request, Header, HTTPException
import uvicorn

# ==== DB (PostgreSQL via SQLAlchemy) ====
from sqlalchemy import (
    create_engine, text
)
from sqlalchemy.exc import OperationalError

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN", "8029001643:AAFoGXOXXcSNvsgxWVoXlyTm9P0quPT1IQE")

# Domínio público do Railway
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://worker-production-8a90.up.railway.app")

# Stripe
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Link de convite VIP (fixo por enquanto; podemos trocar por link de uso único depois)
VIP_INVITE_LINK = os.getenv("VIP_INVITE_LINK", "https://t.me/+SEU_LINK_VIP_AQUI")

# Links Stripe (você me passou)
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"
STRIPE_RENEW_URL     = STRIPE_MONTHLY_URL

# DB URL (Railway Postgres add-on define DATABASE_URL)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ======================
# Stripe init
# ======================
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# ======================
# DB init + helpers
# ======================
engine = None
if DATABASE_URL:
    # Railway normalmente fornece a URL no formato postgres://... ; SQLAlchemy aceita postgresql://...
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS subscribers (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE,
    customer_id TEXT,
    subscription_id TEXT,
    plan TEXT,
    status TEXT,                      -- 'active', 'trialing', 'past_due', 'canceled', etc
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

def db_setup():
    if not engine:
        print("[DB] DATABASE_URL não configurado — rodando sem persistência!")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))
    print("[DB] Tabela 'subscribers' ok.")

def upsert_subscriber(*, email: Optional[str], customer_id: Optional[str], subscription_id: Optional[str], plan: Optional[str], status: str):
    if not engine:
        print("[DB] skip upsert (sem DB).", email, status)
        return
    if not (email or customer_id):
        print("[DB] upsert ignorado (sem email e sem customer_id)")
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
        conn.execute(sql, dict(email=email, customer_id=customer_id, subscription_id=subscription_id, plan=plan, status=status))
    print(f"[DB] upsert {email or customer_id}: {status}")

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
    print(f"[DB] set status by customer {customer_id}: {status}")

# ======================
# Textos do bot (mantidos)
# ======================
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "**1️⃣ Choose Your Plan**\n"
    "Tap on **🌟 Plans** and pick Monthly, Quarterly, or Annual.\n\n"
    "**2️⃣ Complete Your Payment (Stripe)**\n"
    "Use your email normally.\n\n"
    "**3️⃣ Unlock Your VIP Access**\n"
    "Come back to this bot and tap **🔓 Unlock Access**.\n"
    "Enter the **email** you used in Stripe. If active, you'll receive the VIP invite.\n\n"
    "💡 Tip: If you have any issues, tap **🆘 Support**."
)

# ======================
# Bot UI (mantido)
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
    user_id = update.effective_user.id
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: {user_id}")

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
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ======================
# Unlock Access (Opção B)
# ======================
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=(
            "🔓 **Unlock Access**\n\n"
            "Please type the **email** you used on Stripe.\n"
            "I'll verify and send your VIP invite if your subscription is active."
        ),
        parse_mode="Markdown"
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text("⚠️ That doesn't look like a valid email. Try again, please.")
        return ASK_EMAIL

    sub = get_by_email(email)
    if sub and sub.get("status") in ("active", "trialing"):
        await update.effective_message.reply_text(
            f"✅ Access granted for **{email}**!\nHere is your VIP invite:\n{VIP_INVITE_LINK}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        "❌ I couldn't find an active subscription for this email.\n"
        "If you paid recently, wait a minute and try again, or tap Support and send your receipt."
    )
    return ConversationHandler.END

async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

# Router dos botões
async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "plans.open":
        return await open_plans(update, context)
    if data == "plans.back":
        return await back_to_home(update, context)
    if data == "howitworks":
        return await show_how_it_works(update, context)
    if data == "renew":
        return await renew(update, context)
    if data == "unlock.access":
        return await unlock_access_prompt(update, context)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text=f"✅ You clicked: {data}")

# ======================
# FastAPI (Webhook Stripe + Health)
# ======================
app = FastAPI()

@app.get("/")
async def health():
    return {"ok": True, "service": "LukaMagicBOT + Stripe Webhook"}

def _extract_email_from_session(session: dict) -> Optional[str]:
    email = None
    # Priority: customer_details.email
    cd = session.get("customer_details") or {}
    email = cd.get("email")
    if email:
        return email.lower()

    # legacy fields
    email = session.get("customer_email")
    if email:
        return email.lower()

    # As fallback, try line items? (skipped for simplicity)
    return None

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="Stripe-Signature")):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    etype = event.get("type")
    obj = event["data"]["object"]

    # checkout.session.completed —> marca ativo
    if etype == "checkout.session.completed":
        email = _extract_email_from_session(obj)
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        plan = None
        status = "active"
        upsert_subscriber(email=email, customer_id=customer_id, subscription_id=subscription_id, plan=plan, status=status)

    # invoice.payment_succeeded —> mantém ativo
    elif etype == "invoice.payment_succeeded":
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        set_status_by_customer(customer_id, "active", subscription_id)

    # invoice.payment_failed —> marca past_due (aviso)
    elif etype == "invoice.payment_failed":
        customer_id = obj.get("customer")
        set_status_by_customer(customer_id, "past_due", None)

    # customer.subscription.deleted —> cancelado
    elif etype == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        set_status_by_customer(customer_id, "canceled", obj.get("id"))

    return {"received": True}

# ======================
# Main: start app
# ======================
def main():
    # DB
    try:
        db_setup()
    except OperationalError as e:
        print(f"[DB] Erro ao conectar/criar tabela: {e}")

    # Telegram App
    application: Application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CallbackQueryHandler(button_router))

    # Conversa do Unlock (email)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]
        },
        fallbacks=[CommandHandler("cancel", unlock_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv)

    # Telegram Webhook (não usar polling no Railway)
    # Webhook URL: https://<PUBLIC_URL>/<TOKEN>
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        url_path=TOKEN,
        webhook_url=f"{PUBLIC_URL.rstrip('/')}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
