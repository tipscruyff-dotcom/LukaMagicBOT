import os
import logging
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
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

# ======================
# LOG
# ======================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger("LukaMagicBOT")

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
LOCAL_POLLING = os.getenv("LOCAL_POLLING", "0")

# Links (os seus mesmos)
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

SUPPORT_URL = "https://t.me/Sthefano_p"
FREE_GROUP_URL = "https://t.me/lukaeurope77"
SALES_SITE_URL = "https://lukamagiceurope.com"

# ======================
# Textos
# ======================
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "1️⃣ Choose a plan in **🌟 Plans** and finish payment on Stripe.\n"
    "2️⃣ Come back here and tap **🔓 Unlock Access** (temporarily manual).\n"
    "3️⃣ If you need help, tap **🆘 Support**.\n\n"
    "_This screen will be updated after we finish the automation._"
)

HOME_TEXT = "✅ Welcome! Please choose an option:"

# ======================
# /start
# ======================
def home_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🆘 Support", url=SUPPORT_URL),
            InlineKeyboardButton("🔁 Renew", callback_data="renew"),
        ],
        [
            InlineKeyboardButton("🔓 Unlock Access", callback_data="unlock.access"),
            InlineKeyboardButton("🌟 Plans", callback_data="plans.open"),
        ],
        [
            InlineKeyboardButton("🎁 Free Group", url=FREE_GROUP_URL),
            InlineKeyboardButton("ℹ️ How It Works", callback_data="howitworks"),
        ],
        [
            InlineKeyboardButton("🌐 Sales Website", url=SALES_SITE_URL),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HOME_TEXT, reply_markup=home_keyboard())

# ======================
# Utilidades
# ======================
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: `{user_id}`", parse_mode=ParseMode.MARKDOWN)

async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Private Chat"
    await update.effective_message.reply_text(
        f"📌 Group Name: {chat_title}\n🆔 Group ID: `{chat_id}`",
        parse_mode=ParseMode.MARKDOWN
    )

# ======================
# PLANS
# ======================
async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # evita "loading" infinito
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
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # reenvia um novo message (alguns clientes não atualizam bem com edit)
    await query.message.reply_text(HOME_TEXT, reply_markup=home_keyboard())

# ======================
# HOW IT WORKS
# ======================
async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]])
    await query.edit_message_text(text=HOW_IT_WORKS_TEXT, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ======================
# RENEW (placeholder)
# ======================
async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]])
    await query.edit_message_text(
        text="🔁 **Renew**\n\nUse *🌟 Plans* to choose a subscription for now.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

# ======================
# UNLOCK ACCESS (placeholder funcional)
# ======================
ASK_EMAIL = 10

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🔓 **Unlock Access**\n\n"
        "Please type the **email** you used on Stripe.\n"
        "_(Temporary manual check while we finish automation.)_",
        parse_mode=ParseMode.MARKDOWN
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip()
    if "@" not in email:
        await update.effective_message.reply_text("⚠️ That doesn't look like a valid email. Try again, please.")
        return ASK_EMAIL

    # Apenas resposta funcional por enquanto (sem DB/Stripe)
    await update.effective_message.reply_text(
        f"✅ Thanks! We got your email: **{email}**.\n"
        "Our system will verify and send your VIP invite shortly.\n\n"
        "If nothing arrives, tap 🆘 Support.",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

# ======================
# Router de botões
# ======================
async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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

        # fallback genérico
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=f"✅ You clicked: {data}")
    except Exception as e:
        logger.exception("Erro no button_router: %s", e)
        # Sempre responder para não travar o "loading"
        try:
            await update.callback_query.answer("Something went wrong", show_alert=True)
        except Exception:
            pass

# ======================
# Error handler global
# ======================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception: %s", context.error)

# ======================
# Main
# ======================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN não definido. Configure no .env/Variables do Railway.")

    application: Application = ApplicationBuilder().token(TOKEN).build()
    application.add_error_handler(on_error)

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("groupid", groupid))

    # Botões
    application.add_handler(CallbackQueryHandler(button_router))

    # Conversa do Unlock (email)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
        states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]},
        fallbacks=[CommandHandler("cancel", unlock_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv)

    # Execução
    if LOCAL_POLLING == "1":
        logger.info("[BOT] Rodando em modo LOCAL (polling).")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        if not PUBLIC_URL:
            raise RuntimeError("PUBLIC_URL não definido para webhook. Sete em Variables do Railway.")
        logger.info("[BOT] Rodando em modo WEBHOOK.")
        # IMPORTANTE: url_path deve ser o TOKEN para evitar colisões
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "8080")),
            url_path=TOKEN,
            webhook_url=f"{PUBLIC_URL}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES
        )

if __name__ == "__main__":
    main()
