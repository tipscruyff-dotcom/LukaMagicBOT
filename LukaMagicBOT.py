import os
import re
from typing import List

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
# Config (mínimo)
# ======================
TOKEN = os.getenv("BOT_TOKEN")

# Links (iguais aos que você já usa)
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

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

# ======================
# Textos
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
            InlineKeyboardButton("🌟 Plans", callback_data="plans.open"),
        ],
        [
            InlineKeyboardButton("🎁 Free Group", url="https://t.me/lukaeurope77"),
            InlineKeyboardButton("ℹ️ How It Works", callback_data="howitworks"),
        ],
        [
            InlineKeyboardButton("🌐 Sales Website", url="https://lukamagiceurope.com"),
        ],
    ]
    await update.effective_message.reply_text(
        "✅ Welcome! Please choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID is: <code>{user_id}</code>", parse_mode="HTML"
    )

async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Private Chat"
    await update.effective_message.reply_text(
        f"📌 Group Name: {chat_title}\n🆔 Group ID: <code>{chat_id}</code>",
        parse_mode="HTML",
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
        [InlineKeyboardButton("⬅️ Back", callback_data="home.back")],
    ]
    await query.edit_message_text(
        text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
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
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]]),
    )

# ======================
# Unlock Access (simples)
# ======================
ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]]
    await query.edit_message_text(
        text=(
            "🔓 <b>Unlock Access</b><br><br>"
            "Please type the <b>email</b> you used on Stripe.<br>"
            "<i>(This step is temporary while we finish the automation.)</i>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_EMAIL

async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text(
            "⚠️ That doesn't look like a valid email. Try again, please."
        )
        return ASK_EMAIL

    await update.effective_message.reply_text(
        f"✅ Thanks! We received <b>{email}</b>. We'll verify and send your VIP invite shortly.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END

async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

# ======================
# Router
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]]),
        )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text=f"✅ You clicked: {data}")

# ======================
# Main
# ======================
def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN não definido nas Variables do Railway.")

    application: Application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", cmd_myid))
    application.add_handler(CommandHandler("groupid", groupid))
    application.add_handler(CallbackQueryHandler(button_router))

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(unlock_access_prompt, pattern="^unlock\\.access$")],
        states={ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]},
        fallbacks=[CommandHandler("cancel", unlock_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv)

    print("[BOT] Running in POLLING mode.")
    application.run_polling(allowed_updates=["message", "callback_query"])

if _name_ == "_main_":
    main()
