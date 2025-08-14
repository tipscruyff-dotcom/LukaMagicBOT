import os
import re
import stripe
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from fastapi.responses import JSONResponse

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ---------- LOGGING ----------
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("LukaMagicBOT")

# ---------- CONFIG ----------
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

DATABASE_URL = os.getenv("DATABASE_URL", "")

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

engine = None
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

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
        log.warning("[DB] DATABASE_URL não configurado — sem persistência.")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))
    log.info("[DB] Tabela 'subscribers' ok.")

def upsert_subscriber(*, email: Optional[str], customer_id: Optional[str],
                      subscription_id: Optional[str], plan: Optional[str], status: str):
    if not engine:
        log.info("[DB] skip upsert (sem DB). %s %s", email, status)
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
    log.info("[DB] upsert %s: %s", email or customer_id, status)

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
    log.info("[DB] set status by customer %s: %s", customer_id, status)

# ---------- TEXTOS (HTML para evitar erro de parse do Markdown) ----------
HOW_IT_WORKS_TEXT = (
    "ℹ️ <b>How It Works</b>\n\n"
    "<b>1️⃣ Choose Your Plan</b>\n"
    "Tap on <b>🌟 Plans</b> and pick Monthly, Quarterly, or Annual.\n\n"
    "<b>2️⃣ Complete Your Payment (Stripe)</b>\n"
    "Use your email normally.\n\n"
    "<b>3️⃣ Unlock Your VIP Access</b>\n"
    "Come back to this bot and tap <b>🔓 Unlock Access</b>.\n"
    "Enter the <b>email</b> you used in Stripe. If active, you'll receive your VIP invite(s).\n\n"
    "💡 Tip: If you have any issues, tap <b>🆘 Support</b>."
)

# ---------- BOT ----------
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("[/start] from %s", update.effective_user.id if update.effective_user else "?")
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
    uid = update.effective_user.id
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: {uid}")

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
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(update, context)

async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=HOW_IT_WORKS_TEXT, parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]]))

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🔁 Renew Now", url=STRIPE_RENEW_URL)],
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]
    ]
    await query.edit_message_text(text="🔁 <b>Renew your subscription below:</b>",
                                  reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def _generate_single_use_invites(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    if not VIP_GROUP_IDS:
        return None
    try:
        expire_at = datetime.utcnow() + timedelta(hours=24)
        lines = []
        for gid in VIP_GROUP_IDS:
            try:
                link = await context.bot.create_chat_invite_link(
                    chat_id=gid, expire_date=expire_at, member_limit=1
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
        text=("🔓 <b>Unlock Access</b>\n\n"
              "Please type the <b>email</b> you used on Stripe.\n"
              "If your subscription is active, I'll send your VIP invite(s)."),
        parse_mode="HTML"
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
                f"✅ Access granted for <b>{email}</b>!\n{invites_text}",
                parse_mode="HTML", disable_web_page_preview=True
            )
        else:
            await update.effective_message.reply_text(
                f"✅ Access granted for <b>{email}</b>!\nHere is your VIP invite:\n{VIP_INVITE_LINK}",
                parse_mode="HTML"
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

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    log.info("[BTN] %s", data)
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

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Exceção ao processar update %s: %s", getattr(update, "update_id", "?"), context.error)

# ---------- FASTAPI + TELEGRAM MONTADO ----------
app = FastAPI()
tg_app: Application = ApplicationBuilder().token(TOKEN).build()

# Handlers no app do Telegram
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CommandHandler("myid", myid))
tg_app.add_handler(CommandHandler("groupid", groupid))
tg_app.add_handler(CallbackQueryHandler(button_router))
conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
    states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]},
    fallbacks=[CommandHandler("cancel", unlock_cancel)],
    allow_reentry=True,
    per_message=True,
)
tg_app.add_handler(conv)
tg_app.add_error_handler(error_handler)

# Monte o webhook do Telegram dentro da FastAPI
app.mount(f"/{TOKEN}", tg_app.webhook_application())

@app.on_event("startup")
async def on_startup():
    db_setup()
    # Inicializa o app do Telegram e seta o webhook corretamente
    await tg_app.initialize()
    webhook_url = f"{PUBLIC_URL}/{TOKEN}"
    await tg_app.bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message", "callback_query"]
    )
    await tg_app.start()
    log.info("[BOOT] Webhook setado: %s", webhook_url)

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

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

    if etype == "checkout.session.completed":
        email = _extract_email_from_session(obj)
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        upsert_subscriber(email=email, customer_id=customer_id, subscription_id=subscription_id, plan=None, status="active")
    elif etype == "invoice.payment_succeeded":
        set_status_by_customer(obj.get("customer"), "active", obj.get("subscription"))
    elif etype == "invoice.payment_failed":
        set_status_by_customer(obj.get("customer"), "past_due", None)
    elif etype == "customer.subscription.deleted":
        set_status_by_customer(obj.get("customer"), "canceled", obj.get("id"))

    return JSONResponse({"received": True})
