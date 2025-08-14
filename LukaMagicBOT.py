import os
import re
import stripe
import logging
from datetime import datetime, timedelta
from typing import Optional, List

# ========= LOGGING =========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("LukaMagicBOT")

# .env local
from dotenv import load_dotenv
load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

# ==== DB (PostgreSQL via SQLAlchemy) ====
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# Stripe
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Links Stripe
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"
STRIPE_RENEW_URL     = STRIPE_MONTHLY_URL

# Convite fallback
VIP_INVITE_LINK = os.getenv("VIP_INVITE_LINK", "https://t.me/+SEU_LINK_VIP_AQUI")

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

# DB
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ======================
# Stripe init
# ======================
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY
    log.info("[STRIPE] API key configurada.")

# ======================
# DB init + helpers
# ======================
engine = None
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    log.info("[DB] Engine criado.")
else:
    log.warning("[DB] DATABASE_URL não configurado — rodando sem persistência!")

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
        log.warning("[DB] Sem engine; pulando criação de tabela.")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))
    log.info("[DB] Tabela 'subscribers' pronta.")

def upsert_subscriber(*, email: Optional[str], customer_id: Optional[str],
                      subscription_id: Optional[str], plan: Optional[str], status: str):
    if not engine:
        log.warning("[DB] skip upsert (sem DB). email=%s status=%s", email, status)
        return
    if not (email or customer_id):
        log.warning("[DB] upsert ignorado (sem email e sem customer_id)")
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
    log.info("[DB] upsert feito: email=%s customer=%s sub=%s status=%s", email, customer_id, subscription_id, status)

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
    log.info("[DB] status atualizado: customer=%s status=%s sub=%s", customer_id, status, subscription_id)

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
    "Come back to this bot and tap **🔓 Unlock Access**.\n"
    "Enter the **email** you used in Stripe. If active, you'll receive your VIP invite(s).\n\n"
    "💡 Tip: If you have any issues, tap **🆘 Support**."
)

# ======================
# Bot UI
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
    await update.effective_message.reply_text(
        "✅ Welcome! Please choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: {user_id}")

async def debug_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /debug_email seu@email.com  -> mostra o que está no DB """
    parts = (update.effective_message.text or "").strip().split()
    if len(parts) != 2:
        await update.effective_message.reply_text("Use: /debug_email seu@email.com")
        return
    email = parts[1].lower()
    row = get_by_email(email)
    await update.effective_message.reply_text(f"DB -> {row!r}")

async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Private Chat"
    await update.effective_message.reply_text(
        f"📌 Group Name: {chat_title}\n🆔 Group ID: `{chat_id}`",
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
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

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
# Unlock Access (email)
# ======================
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def _generate_single_use_invites(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    if not VIP_GROUP_IDS:
        return None
    try:
        expire_at = datetime.utcnow() + timedelta(hours=24)
        lines = []
        for gid in VIP_GROUP_IDS:
            try:
                link = await context.bot.create_chat_invite_link(
                    chat_id=gid,
                    expire_date=expire_at,
                    member_limit=1
                )
                lines.append(f"• {link.invite_link}")
            except Exception as e:
                log.error("[INVITE] Falha ao criar convite para %s: %s", gid, e)
        if lines:
            return "🔗 Your VIP invites (1 use each, valid 24h):\n" + "\n".join(lines)
    except Exception as e:
        log.exception("[INVITE] Erro geral: %s", e)
    return None

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=(
            "🔓 **Unlock Access**\n\n"
            "Please type the **email** you used on Stripe.\n"
            "If your subscription is active, I'll send your VIP invite(s)."
        ),
        parse_mode="Markdown"
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    log.info("[UNLOCK] Email recebido: %s", email)
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text("⚠️ That doesn't look like a valid email. Try again, please.")
        return ASK_EMAIL

    sub = get_by_email(email)
    log.info("[UNLOCK] Consulta no DB: %s", sub)
    if sub and sub.get("status") in ("active", "trialing"):
        invites_text = await _generate_single_use_invites(context)
        if invites_text:
            await update.effective_message.reply_text(
                f"✅ Access granted for **{email}**!\n{invites_text}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        else:
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
    cd = session.get("customer_details") or {}
    email = cd.get("email")
    if email:
        return email.lower()
    email = session.get("customer_email")
    if email:
        return email.lower()
    return None

# Aceita **ambas** as rotas para não ter 404
@app.post("/stripe_webhook")
@app.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    payload = await request.body()
    # log bruto (curto) pra debug
    log.info("[WEBHOOK] Recebido %d bytes", len(payload))
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        log.error("[WEBHOOK] Assinatura inválida: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    etype = event.get("type")
    obj = event["data"]["object"]
    log.info("[WEBHOOK] Type=%s", etype)

    if etype == "checkout.session.completed":
        email = _extract_email_from_session(obj)
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        plan = None
        status = "active"
        upsert_subscriber(
            email=email,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan=plan,
            status=status
        )

    elif etype == "invoice.payment_succeeded":
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        set_status_by_customer(customer_id, "active", subscription_id)

    elif etype == "invoice.payment_failed":
        customer_id = obj.get("customer")
        set_status_by_customer(customer_id, "past_due", None)

    elif etype == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        set_status_by_customer(customer_id, "canceled", obj.get("id"))

    return JSONResponse({"received": True})

# ======================
# Main
# ======================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN não definido. Configure no .env ou nas Variables do Railway.")

    try:
        db_setup()
    except OperationalError as e:
        log.error("[DB] Erro ao conectar/criar tabela: %s", e)

    application: Application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("groupid", groupid))
    application.add_handler(CommandHandler("debug_email", debug_email))
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

    # Execução
    if os.getenv("LOCAL_POLLING", "0") == "1":
        log.info("[BOT] Rodando em modo LOCAL (polling).")
        application.run_polling()
    else:
        if not PUBLIC_URL:
            raise RuntimeError("PUBLIC_URL não definido para webhook.")
        log.info("[BOT] Rodando em modo WEBHOOK. URL: %s/%s", PUBLIC_URL, TOKEN)
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "8080")),
            url_path=TOKEN,
            webhook_url=f"{PUBLIC_URL}/{TOKEN}"
        )

if __name__ == "__main__":
    main()
