import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# TOKEN via variável de ambiente (Railway) ou valor padrão (PC local)
TOKEN = os.getenv("BOT_TOKEN", "8029001643:AAFoGXOXXcSNvsgxWVoXlyTm9P0quPT1IQE")

# === LINKS DE CHECKOUT (trocar pelos do LemonSqueezy depois) ===
CHECKOUT_MONTHLY_URL = "https://example.com/checkout-monthly"
CHECKOUT_QUARTERLY_URL = "https://example.com/checkout-quarterly"
CHECKOUT_ANNUAL_URL = "https://example.com/checkout-annual"

# LINKS FIXOS
FREE_GROUP_URL = "https://t.me/lukaeurope77"
SALES_WEBSITE_URL = "https://lukamagiceurope.com"

# Texto How It Works
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "**1️⃣ Choose Your Plan**\n"
    "Tap on **🌟 Plans** and select the subscription that works best for you: Monthly, Quarterly, or Annual.\n\n"
    "**2️⃣ Complete Your Payment**\n"
    "You’ll be redirected to our secure checkout on LemonSqueezy.\n"
    "After payment, you will receive a confirmation email with your purchase details.\n\n"
    "**3️⃣ Unlock Your VIP Access**\n"
    "Return to this bot and tap **🔓 Unlock Access**.\n"
    "Enter the **email** you used for your purchase (or the unique code sent to your email).\n"
    "Once verified, you will automatically receive your invitation to the VIP group.\n\n"
    "💡 **Tip:** If you have any issues, tap **🆘 Support** to contact us directly."
)

# Menu inicial
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
            InlineKeyboardButton("🎁 Free Group", url=FREE_GROUP_URL),
            InlineKeyboardButton("ℹ️ How It Works", callback_data="howitworks")
        ],
        [
            InlineKeyboardButton("🌐 Sales Website", url=SALES_WEBSITE_URL)
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("✅ Welcome! Please choose an option:", reply_markup=reply_markup)

# Comando /myid
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text(f"🆔 Your Telegram ID is: {user_id}")

# Tela de planos
async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "🌟 **Luka Magic Europe – Plans**\n\n"
        "💶 **Monthly – €30/month**\n"
        "_Access for 30 days._\n\n"
        "📊 **Quarterly – €81 (€27/month)**\n"
        "_Save €9 vs monthly._\n\n"
        "🏆 **Annual – €264 (€22/month)**\n"
        "_Save €96 vs monthly._"
    )

    keyboard = [
        [InlineKeyboardButton("💶 Monthly – €30", url=CHECKOUT_MONTHLY_URL)],
        [InlineKeyboardButton("📊 Quarterly – €81 (€27/mo)", url=CHECKOUT_QUARTERLY_URL)],
        [InlineKeyboardButton("🏆 Annual – €264 (€22/mo)", url=CHECKOUT_ANNUAL_URL)],
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# Voltar ao menu
async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(query, context)

# Mostrar How It Works
async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=HOW_IT_WORKS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]])
    )

# Callback genérico
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "plans.open":
        return await open_plans(update, context)
    if data == "plans.back":
        return await back_to_home(update, context)
    if data == "howitworks":
        return await show_how_it_works(update, context)

    await query.answer()
    await query.edit_message_text(text=f"✅ You clicked: {data}")

# Inicialização do bot
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("✅ Bot is running...")
    app.run_polling()
