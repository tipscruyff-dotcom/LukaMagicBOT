import os
import re
from datetime import datetime, timedelta
from typing import Optional, List

# Carrega variáveis do .env (para rodar local sem export manual)
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

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# Links Stripe (mantidos — você já usa esses links no botão Plans)
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

# Fallback de convite (mantido como antes)
VIP_INVITE_LINK = os.getenv("VIP_INVITE_LINK", "https://t.me/+SEU_LINK_VIP_AQUI")

# Se no futuro quisermos convites 1-uso, deixo essa lista aqui pronta
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
            InlineKeyboardButton("🆔 My ID", callback_data="myid.show"),
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

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(f"🆔 Your Telegram ID is: {user_id}")

# /groupid — retorna o ID do chat/grupo atual
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
        [InlineKeyboardButton("⬅️ Back", callback_data="home.back")]
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]])
    )

# ======================
# Unlock Access (somente fluxo básico por enquanto)
# ======================
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]]
    await query.edit_message_text(
        text=(
            "🔓 **Unlock Access**\n\n"
            "Please type the **email** you used on Stripe.\n"
            "_(This step is temporary while we finish the automation.)_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text("⚠️ That doesn't look like a valid email. Try again, please.")
        return ASK_EMAIL

    # Aqui, por enquanto, só confirmamos o recebimento.
    # Depois, quando ligarmos o Stripe + DB, a gente verifica e já envia os convites.
    await update.effective_message.reply_text(
        f"✅ Thanks! We received **{email}**. We'll verify and send your VIP invite shortly.",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return ConversationHandler.END

async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

# ======================
# Router dos botões
# ======================
async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    if data == "plans.open":
        return await open_plans(update, context)
    if data == "home.back":
        return await back_to_home(update, context)
    if data == "howitworks":
        return await show_how_it_works(update, context)
    if data == "unlock.access":
        return await unlock_access_prompt(update, context)
    if data == "myid.show":
        await update.callback_query.answer()
        uid = update.effective_user.id
        return await update.callback_query.edit_message_text(
            text=f"🆔 Your Telegram ID is: <code>{uid}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]])
        )

    # fallback
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text=f"✅ You clicked: {data}")

# ======================
# Main
# ======================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN não definido. Configure no .env ou nas Variables do Railway.")

    application: Application = ApplicationBuilder().token(TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", cmd_myid))
    application.add_handler(CommandHandler("groupid", groupid))
    application.add_handler(CallbackQueryHandler(button_router))

    # Unlock Access (conversa simples, com Back separado no router)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]
        },
        fallbacks=[CommandHandler("cancel", unlock_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv)

    # Execução:
    # Local (polling): defina LOCAL_POLLING=1 no .env
    # Nuvem (webhook PTB): padrão
    if os.getenv("LOCAL_POLLING", "0") == "1":
        print("[BOT] Rodando em modo LOCAL (polling).")
        application.run_polling()
    else:
        if not PUBLIC_URL:
            raise RuntimeError("PUBLIC_URL não definido para webhook.")
        print("[BOT] Rodando em modo WEBHOOK (PTB).")
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "8080")),
            url_path=TOKEN,
            webhook_url=f"{PUBLIC_URL}/{TOKEN}"
        )

if __name__ == "__main__":
    main()
