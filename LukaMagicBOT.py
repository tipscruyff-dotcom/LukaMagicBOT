# LukaMagicBOT.py
import os
import re
import stripe
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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

# Prefixo seguro do token para usar no path (remove os ':' etc)
TOKEN_PREFIX = TOKEN.split(":")[0]

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Links Stripe
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"
STRIPE_RENEW_URL     = STRIPE_MONTHLY_URL

# Link de convite fixo (fallback)
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

# DB URL (Railway Postgres)
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
        print("[DB] DATABASE_URL não configurado — rodando sem persistência!")
        return
    with engine.begin() as conn:
        conn.execute(text(DDL_CREATE_TABLE))
    print("[DB] Tabela 'subscribers' ok.")

def upsert_subscriber(*, email: Optional[str], customer_id: Optional[str],
                      subscription_id: Optional[str], plan: Optional[str], status: str):
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
        conn.execute(sql, dict(
            email=(email.lower() if email else None),
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan=plan,
            status=status
        ))
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
# Textos do bot (HTML)
# ======================
HOW_IT_WORKS_TEXT = (
    "ℹ️ <b>How It Works</b><br><br>"
    "<b>1️⃣ Choose Your Plan</b><br>"
    "Tap on <b>🌟 Plans</b> and pick Monthly, Quarterly, or Annual.<br><br>"
    "<b>2️⃣ Complete Your Payment (Stripe)</b><br>"
    "Use your email normally.<br><br>"
    "<b>3️⃣ Unlock Your VIP Access</b><br>"
    "Come back to this bot and tap <b>🔓 Unlock Access</b>.<br>"
    "Enter the <b>email</b> you used in Stripe. If active, you'll receive your VIP invite(s).<br><br>"
    "💡 Tip: If you have any issues, tap <b>🆘 Support</b>."
)

# ======================
# Handlers do Bot
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
    await update.effective_message.reply_html(
        "✅ <b>Welcome!</b> Please choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_html(f"🆔 Your Telegram ID is: <code>{user_id}</code>")

async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Private Chat"
    await update.effective_message.reply_html(
        f"📌 Group Name: <b>{chat_title}</b><br>🆔 Group ID: <code>{chat_id}</code>"
    )

async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🌟 <b>Luka Magic Europe – Plans</b><br><br>"
        "💶 <s>€50</s> → <b>€30</b><br>"
        "<i>€30 / month – 40% off</i><br><br>"
        "📊 <s>€150</s> → <b>€80</b><br>"
        "<i>€26.67 / month – 46% off</i><br><br>"
        "🏆 <s>€600</s> → <b>€270</b><br>"
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
        parse_mode="HTML",
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
        text="🔁 <b>Renew your subscription below:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# ======================
# Unlock Access
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
                    chat_id=gid, expire_date=expire_at, member_limit=1
                )
                lines.append(f"• {link.invite_link}")
            except Exception as e:
                print(f"[INVITE] Falha ao criar convite para {gid}: {e}")
        if lines:
            return "🔗 Your VIP invites (1 use each, valid 24h):\n" + "\n".join(lines)
    except Exception as e:
        print(f"[INVITE] Erro geral: {e}")
    return None

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=(
            "🔓 <b>Unlock Access</b><br><br>"
            "Please type the <b>email</b> you used on Stripe.<br>"
            "If your subscription is active, I'll send your VIP invite(s)."
        ),
        parse_mode="HTML"
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_html("⚠️ That doesn't look like a valid email. Try again, please.")
        return ASK_EMAIL

    sub = get_by_email(email)
    if sub and sub.get("status") in ("active", "trialing"):
        invites_text = await _generate_single_use_invites(context)
        if invites_text:
            await update.effective_message.reply_html(
                f"✅ Access granted for <b>{email}</b>!<br>{invites_text}",
                disable_web_page_preview=True
            )
        else:
            await update.effective_message.reply_html(
                f"✅ Access granted for <b>{email}</b>!<br>Here is your VIP invite:<br>{VIP_INVITE_LINK}"
            )
        return ConversationHandler.END

    await update.effective_message.reply_html(
        "❌ I couldn't find an active subscription for this email.<br>"
        "If you paid recently, wait a minute and try again, or tap <b>Support</b> and send your receipt."
    )
    return ConversationHandler.END

async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_html("Cancelled.")
    return ConversationHandler.END

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
# Construtor do Application (Telegram)
# ======================
def build_tg_app() -> Application:
    application: Application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("groupid", groupid))
    application.add_handler(CallbackQueryHandler(button_router))

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
        states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]},
        fallbacks=[CommandHandler("cancel", unlock_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv)
    return application

tg_app: Application = build_tg_app()

# ======================
# FastAPI (Stripe + Telegram Webhook + Health)
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

# --- Stripe Webhook ---
@app.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    etype = event.get("type")
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        email = _extract_email_from_session(obj)
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        upsert_subscriber(
            email=email, customer_id=customer_id, subscription_id=subscription_id,
            plan=None, status="active"
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

    return {"received": True}

# --- Telegram Webhook via FastAPI ---
@app.post(f"/telegram/{{token_prefix}}")
async def telegram_webhook(token_prefix: str, request: Request):
    # valida o prefixo para evitar chamadas aleatórias
    if token_prefix != TOKEN_PREFIX:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

# --- Lifecycle: inicia PTB e define webhook ---
@app.on_event("startup")
async def on_startup():
    try:
        db_setup()
    except OperationalError as e:
        print(f"[DB] Erro ao conectar/criar tabela: {e}")

    await tg_app.initialize()
    await tg_app.start()

    if PUBLIC_URL:
        url = f"{PUBLIC_URL}/telegram/{TOKEN_PREFIX}"
        try:
            await tg_app.bot.set_webhook(
                url, allowed_updates=["message", "callback_query"]
            )
            print(f"[TG] Webhook setado em: {url}")
        except Exception as e:
            print(f"[TG] Falha ao setar webhook: {e}")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await tg_app.stop()
        await tg_app.shutdown()
    except Exception as e:
        print(f"[TG] Erro ao finalizar app: {e}")

# Execução local (opcional)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("LukaMagicBOT:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
