import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# TOKEN via variável de ambiente (Railway) ou valor padrão local
TOKEN = os.getenv("BOT_TOKEN", "8029001643:AAFoGXOXXcSNvsgxWVoXlyTm9P0quPT1IQE")

# === LINKS DE CHECKOUT (STRIPE) ===
STRIPE_MONTHLY_URL   = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL    = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

# Para renovação, pode usar o mesmo do mensal ou criar um link específico
STRIPE_RENEW_URL     = STRIPE_MONTHLY_URL

# LINKS FIXOS
FREE_GROUP_URL = "https://t.me/lukaeurope77"
SALES_WEBSITE_URL = "https://lukamagiceurope.com"

# Texto How It Works (inalterado)
HOW_IT_WORKS_TEXT = (
    "ℹ️ **How It Works**\n\n"
    "**1️⃣ Choose Your Plan**\n"
    "Tap on **🌟 Plans** and pick Monthly, Quarterly, or Annual.\n\n"
    "**2️⃣ Complete Your Payment (Stripe)**\n"
    "In checkout, if asked, enter your **Telegram ID** (from /myid).\n\n"
    "**3️⃣ Unlock Your VIP Access**\n"
    "After payment, return to this bot and tap **🔓 Unlock Access**.\n"
    "If you don't receive the link automatically, tap **🆘 Support**.\n\n"
    "💡 Tip: Use /myid to copy your Telegram ID."
)

# Menu inicial (inalterado)
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

# /myid (inalterado)
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text(f"🆔 Your Telegram ID is: {user_id}")

# >>>>>> TELA DE PLANOS (ATUALIZADA APENAS AQUI) <<<<<<
async def open_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Usando HTML para suportar <s>texto riscado</s> e <b>ênfase</b>
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
    # Apenas aqui troquei para parse_mode="HTML" (necessário pro <s>risco</s>)
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# Voltar ao menu (inalterado)
async def back_to_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(query, context)

# How It Works (inalterado)
async def show_how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=HOW_IT_WORKS_TEXT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]])
    )

# Renew (inalterado)
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

# Unlock Access (inalterado)
async def unlock_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🆘 Support", url="https://t.me/Sthefano_p")],
        [InlineKeyboardButton("⬅️ Back", callback_data="plans.back")]
    ]
    await query.edit_message_text(
        text=(
            "🔓 **Unlock Access**\n\n"
            "If your Stripe payment is confirmed, you will receive your VIP invite.\n"
            "If not, tap Support and send:\n"
            "• Your Telegram ID (/myid)\n"
            "• Email used on Stripe\n"
            "• Payment proof"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# Callback genérico (inalterado)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "plans.open":
        return await open_plans(update, context)
    if data == "plans.back":
        return await back_to_home(update, context)
    if data == "howitworks":
        return await show_how_it_works(update, context)
    if data == "renew":
        return await renew(update, context)
    if data == "unlock.access":
        return await unlock_access(update, context)

    await query.answer()
    await query.edit_message_text(text=f"✅ You clicked: {data}")

# Inicialização do bot (inalterado)
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("✅ Bot is running...")
    app.run_polling()
