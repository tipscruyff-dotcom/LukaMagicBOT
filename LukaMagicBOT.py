import os
import re
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List
from contextlib import asynccontextmanager
import uvicorn
import stripe
from fastapi import FastAPI, Request, HTTPException
from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
import models
from sqlalchemy import inspect
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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

# Load environment variables from .env (for local runs)
from dotenv import load_dotenv
load_dotenv()


# FastAPI and Stripe

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database
try:
    from db import SessionLocal, db_path_info, init_db
    from crud import (
        get_active_by_email,
        get_active_and_not_expired_by_email,
        mark_telegram_id,
        get_recent_invite_for_email,
        get_recent_invite_for_user,
        log_invite,
    )
    from stripe_handlers import process_stripe_webhook_event
    DATABASE_AVAILABLE = True
except ImportError as e:
    logger.warning("Database modules not available: %s", e)
    DATABASE_AVAILABLE = False

# ======================
# 🔐 Config
# ======================
TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-this-admin-secret")

# Stripe Configuration
STRIPE_SECRET_KEY = os.getenv(
    "STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Stripe links (static Payment Links used in the Plans button)
STRIPE_MONTHLY_URL = "https://buy.stripe.com/8x29AVb3M4qn99xh0sawo00"
STRIPE_QUARTERLY_URL = "https://buy.stripe.com/00w7sN4FocWT0D19y0awo01"
STRIPE_ANNUAL_URL = "https://buy.stripe.com/4gM3cx7RAg952L939Cawo02"

# VIP invite fallback (primary static link)
VIP_INVITE_LINK = os.getenv(
    "VIP_INVITE_LINK", "https://t.me/+PSEZYQQnodszYjYx")

# Placeholder for future one-time invites for multiple groups


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

# Global application instance
application: Optional[Application] = None

# ======================
# Bot texts
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
            InlineKeyboardButton(
                "🔓 Unlock Access", callback_data="unlock.access"),
            InlineKeyboardButton("🌟 Plans", callback_data="plans.open")
        ],
        [
            InlineKeyboardButton(
                "🎁 Free Group", url="https://t.me/lukaeurope77"),
            InlineKeyboardButton("ℹ️ How It Works", callback_data="howitworks")
        ],
        [
            InlineKeyboardButton(
                "🌐 Sales Website", url="https://lukamagiceurope.com")
        ]
    ]
    await update.effective_message.reply_text(
        "✅ Welcome! Please choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(
        "🆔 Your Telegram ID is: {}".format(user_id)
    )

# /groupid — retorna o ID do chat/grupo atual


async def groupid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Private Chat"
    await update.effective_message.reply_text(
        "📌 Group Name: {}\n🆔 Group ID: `{}`".format(chat_title, chat_id),
        parse_mode="Markdown"
    )

# (removed) test_invite command to avoid any shortcut for generating links


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
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]])
    )

# ======================
# Unlock Access (basic flow)


async def unlock_access_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]]
    await update.callback_query.edit_message_text(
        text=(
            "🔓 **Unlock Access**\n\n"
            "📧 **Type the email** you used on Stripe to pay.\n\n"
            "✨ After verifying your subscription, you will receive:\n"
            "• Your subscription details\n"
            "• A temporary link to the VIP group\n\n"
            "💡 Please type only your email below:"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ASK_EMAIL
# ======================

ASK_EMAIL = 10
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^@]+\.[^\s@]+$")


async def unlock_access_check_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = (update.effective_message.text or "").strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.effective_message.reply_text(
            "⚠️ That doesn't look like a valid email. Try again, please."
        )
        return ASK_EMAIL

    if not DATABASE_AVAILABLE:
        await update.effective_message.reply_text(
            f"✅ Thanks! We received **{email}**. Database integration is being set up.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    with SessionLocal() as db:
        logger.info("DB URL runtime: %s", db_path_info(db))
        try:
            subscription = get_active_and_not_expired_by_email(db, email)
            if subscription:
                user_id = str(update.effective_user.id)
                logger.info(
                    "Trying to link telegram_user_id %s to email %s", user_id, email)
                success = mark_telegram_id(db, email, user_id)
                logger.info("Result of mark_telegram_id: %s", success)
                if success:
                    # FIRST MESSAGE: Subscription details
                    subscription_info = (
                        "✅ **Subscription Found!**\n\n"
                        f"📧 **Email:** {subscription.email}\n"
                        f"📋 **Plan:** {subscription.plan_type.title()}\n"
                        f"📅 **Status:** {subscription.status.title()}\n"
                        f"⏰ **Expires at:** {subscription.expires_at.strftime('%d/%m/%Y') if subscription.expires_at else 'N/A'}\n\n"
                        "🔄 Generating your access link..."
                    )
                    await update.effective_message.reply_text(
                        subscription_info,
                        parse_mode="Markdown"
                    )
                    
                    # Pequeno delay para melhor UX
                    await asyncio.sleep(1.5)
                    
                    # SECOND MESSAGE: Temporary link (with cooldown control)
                    try:
                        logger.info(f"🔗 Generating invite link for user {user_id}")
                        cooldown_seconds = int(os.getenv("INVITE_COOLDOWN_SECONDS", "180"))
                        # Checar por email E por telegram_user_id
                        recent_email = get_recent_invite_for_email(db, email, cooldown_seconds)
                        recent_user = get_recent_invite_for_user(db, user_id, cooldown_seconds)
                        recent = recent_email or recent_user
                        if recent:
                            # Em vez de reutilizar, avisar cooldown restante
                            now = datetime.utcnow()
                            elapsed = (now - recent.created_at).total_seconds()
                            remaining = max(0, int(cooldown_seconds - elapsed))
                            await update.effective_message.reply_text(
                                f"⏳ Please wait {remaining} seconds before requesting a new invite link.")
                            return ConversationHandler.END
                        
                        # Generate invite link
                        invite_link = await create_one_time_invite_link(
                            context.bot, update.effective_user.id)
                        is_temporary = invite_link != VIP_INVITE_LINK
                        expires_at = (datetime.utcnow() + timedelta(hours=1)) if is_temporary else None
                        
                        # Log the invite
                        log_invite(
                            db,
                            email=email,
                            telegram_user_id=user_id,
                            invite_link=invite_link,
                            expires_at=expires_at,
                            member_limit=1,
                            is_temporary=is_temporary,
                        )
                        
                        # Check whether the link is temporary or fallback
                        link_type = "temporary (1 use)" if is_temporary else "static"
                        
                        # Format message for multiple links
                        if "\n" in invite_link:
                            # Multiple links (one per line)
                            links_text = "\n".join([f"🔗 {link}" for link in invite_link.split("\n")])
                            links_count = len(invite_link.split("\n"))
                            await update.effective_message.reply_text(
                                f"🎉 **Access Granted!**\n\n"
                                f"🔗 **Your VIP links ({link_type}):**\n{links_text}\n\n"
                                f"📊 **Total:** {links_count} VIP groups\n\n"
                                "⏰ **Important:**\n"
                                f"• {'These links expire in 1 hour' if is_temporary else 'Permanent group links'}\n"
                                f"• {'Valid for one person only' if is_temporary else 'Can be used multiple times'}\n"
                                "• Use them to join the VIP groups\n\n"
                                "🎯 Welcome to VIP!",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                        else:
                            # Single link (fallback)
                            await update.effective_message.reply_text(
                                "🎉 **Access Granted!**\n\n"
                                f"🔗 **Your VIP link ({link_type}):**\n{invite_link}\n\n"
                                "⏰ **Important:**\n"
                                f"• {'This link expires in 1 hour' if is_temporary else 'Permanent group link'}\n"
                                f"• {'Valid for one person only' if is_temporary else 'Can be used multiple times'}\n"
                                "• Use it to join the VIP group\n\n"
                                "🎯 Welcome to VIP!",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                        logger.info(
                            "✅ VIP access granted to user %s for email %s (link type: %s)", 
                            user_id, email, link_type)
                    except Exception as e:
                        logger.error("❌ Error in invite link process: %s", e, exc_info=True)
                        await update.effective_message.reply_text(
                            "⚠️ Error generating invite link. Please contact support: @Sthefano_p"
                        )
                else:
                    logger.error(
                        "Failed to link telegram_user_id %s to email %s", user_id, email)
                    await update.effective_message.reply_text(
                        "⚠️ Technical error. Please try again or contact support."
                    )
            else:
                # Check if there is a subscription but expired
                any_sub = None
                try:
                    any_sub = get_active_by_email(db, email)
                except Exception:
                    any_sub = None
                if any_sub and any_sub.expires_at and any_sub.expires_at < datetime.utcnow():
                    # expired
                    keyboard = [[InlineKeyboardButton("🌟 Plans", callback_data="plans.open")]]
                    await update.effective_message.reply_text(
                        "❌ Your subscription has expired. Please renew your plan to continue. 💳",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await update.effective_message.reply_text(
                        "❌ Subscription not found for this email.\n\n"
                        "Please check if the payment was completed, if the email is correct, or try again later.\n"
                        "If you need help, contact support: @Sthefano_p"
                    )
        except (ValueError, TypeError) as e:
            logger.error("Database error in unlock_access_check_email: %s", e)
            await update.effective_message.reply_text(
                "⚠️ Technical database error. Please try again or contact support: @Sthefano_p"
            )
        except Exception as e:
            logger.critical(
                "Unexpected error in unlock_access_check_email: %s", e, exc_info=True)
            await update.effective_message.reply_text(
                "⚠️ Unexpected error. Please contact support: @Sthefano_p"
            )
    return ConversationHandler.END


async def unlock_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

# ======================
# VIP Invite Helpers
# ======================


async def create_one_time_invite_link(bot, user_id: int, ttl_seconds: int = 3600, member_limit: int = 1) -> str:
    """
    Generate one-time invite links for all VIP groups

    Args:
        bot: Bot instance
        user_id: Telegram user ID (for logs)
        ttl_seconds: Time to live in seconds (default: 1 hour)
        member_limit: Members limit (default: 1)

    Returns:
        Invite URLs (one-time or fallback)
    """
    logger.info(f"Starting invite link creation for user {user_id}")
    logger.info(f"Configured VIP_GROUP_IDS: {VIP_GROUP_IDS}")
    logger.info(f"Fallback VIP_INVITE_LINK: {VIP_INVITE_LINK}")
    
    allow_fallback = os.getenv("ALLOW_FALLBACK_INVITE", "0") == "1"
    if not VIP_GROUP_IDS:
        if allow_fallback:
            logger.warning("No VIP_GROUP_IDS configured, using fallback link (dev mode)")
            logger.info(f"Returning fallback link: {VIP_INVITE_LINK}")
            return VIP_INVITE_LINK
        logger.error("VIP group configuration missing and fallback disabled")
        raise RuntimeError("VIP group configuration is missing. Please contact support.")

    # Use epoch timestamp and disable join requests (1 hour, 1 use)
    expire_epoch = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
    logger.info(f"Links will expire at epoch: {expire_epoch}")

    invite_links = []
    
    # Generate links for all groups
    for group_id in VIP_GROUP_IDS:
        try:
            logger.info(f"Trying to create invite for group: {group_id}")
            
            invite_link = await bot.create_chat_invite_link(
                chat_id=group_id,
                expire_date=expire_epoch,
                member_limit=member_limit,
                creates_join_request=False,
                name=f"VIP Access - User {user_id}"
            )

            invite_links.append(invite_link.invite_link)
            logger.info(
                "✅ Created one-time invite for user %s in group %s: %s",
                user_id, group_id, invite_link.invite_link)

        except Exception as e:
            error_msg = str(e)
            if "not enough rights" in error_msg.lower() or "forbidden" in error_msg.lower():
                logger.error("❌ Bot lacks admin permissions in group %s: %s", group_id, error_msg)
            else:
                logger.error("❌ Error creating invite link for user %s in group %s: %s",
                             user_id, group_id, e, exc_info=True)
            # Continue with other groups even if one fails

    if invite_links:
        # Return all links separated by newlines
        all_links = "\n".join(invite_links)
        logger.info("✅ Successfully created %d invite links for user %s", len(invite_links), user_id)
        return all_links
    else:
        # If no links were created, use fallback
        if allow_fallback:
            logger.warning("🔄 Using fallback VIP link due to errors (dev mode)")
            logger.info(f"Returning fallback link: {VIP_INVITE_LINK}")
            return VIP_INVITE_LINK
        raise RuntimeError("Failed to create invite links for any VIP group. Please contact support.")

# ======================
# Button router
# ======================


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data if update.callback_query else None
    if data == "plans.open":
        await open_plans(update, context)
        return
    if data == "home.back":
        await back_to_home(update, context)
        return
    if data == "howitworks":
        await show_how_it_works(update, context)
        return
    if data == "unlock.access":
        await unlock_access_prompt(update, context)
        return
    if data == "myid.show":
        try:
            await update.callback_query.answer()
        except Exception as e:
            logger.warning("CallbackQuery answer failed: %s", e)
        uid = update.effective_user.id
        await update.callback_query.edit_message_text(
            text=f"🆔 Your Telegram ID is: <code>{uid}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="home.back")]])
        )
        return
    # fallback
    try:
        await update.callback_query.answer()
    except Exception as e:
        logger.warning("CallbackQuery answer failed: %s", e)
    await update.callback_query.edit_message_text(
        text="✅ You clicked: {}".format(data)
    )

# ======================
# FastAPI Setup
# ======================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the application lifecycle"""
    global application
    polling_task = None

    # Startup
    # Initialize database
    if DATABASE_AVAILABLE:
        try:
            init_db()
            with SessionLocal() as db:
                logger.info("Database available (Postgres/SQL) - URL: %s", db_path_info(db))
                # Lightweight SQLite migration: add missing columns if needed
                try:
                    _apply_sqlite_migrations(db)
                except Exception as mig_err:
                    logger.warning("DB migration step skipped/failed: %s", mig_err)
        except Exception as e:
            logger.exception("Failed to initialize database: %s", e)
    else:
        logger.warning("Database not available - running in limited mode")

    # Configure bot
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN not defined")

    global application
    application = ApplicationBuilder().token(TOKEN).build()

    # Add handlers
    setup_handlers(application)

    # Execution mode
    local_mode = os.getenv("LOCAL_POLLING", "0") == "1"
    if local_mode:
        # Local mode: initialize + start + start_polling (compatible with running event loop)
        logger.info("Starting bot in LOCAL POLLING mode")
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    else:
        # Production mode: webhook
        if not PUBLIC_URL:
            raise RuntimeError("PUBLIC_URL not defined for webhook")
        await application.initialize()
        await application.start()
        webhook_url = f"{PUBLIC_URL}/telegram/{TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"Bot webhook set to: {webhook_url}")

    # Start auto-removal scheduler
    logger.info(f"🔍 Scheduler initialization check:")
    logger.info(f"   - DATABASE_AVAILABLE: {DATABASE_AVAILABLE}")
    logger.info(f"   - ENABLE_AUTO_REMOVAL: {os.getenv('ENABLE_AUTO_REMOVAL', '1')}")
    
    if DATABASE_AVAILABLE and os.getenv("ENABLE_AUTO_REMOVAL", "1") == "1":
        logger.info("🚀 Starting scheduler...")
        try:
            start_scheduler()
            logger.info("✅ Scheduler initialization completed")
        except Exception as e:
            logger.error(f"❌ Failed to start scheduler in lifespan: {e}")
    else:
        logger.info("⏰ Auto-removal scheduler disabled")

    yield

    # Shutdown
    if application:
        if local_mode:
            try:
                await application.updater.stop()
            except Exception:
                pass
        try:
            await application.stop()
            await application.shutdown()
        except Exception:
            pass
        logger.info("Bot application shut down")
    
    # Stop scheduler
    stop_scheduler()


def _apply_sqlite_migrations(session):
    """Apply minimal schema migrations for SQLite (non-destructive)."""
    bind = session.get_bind()
    try:
        dialect = bind.dialect.name
    except Exception:
        dialect = "sqlite"
    if dialect != "sqlite":
        return
    conn = bind.connect()
    # Ensure subscriptions.full_name exists
    cols = conn.exec_driver_sql("PRAGMA table_info(subscriptions)").fetchall()
    col_names = {row[1] for row in cols} if cols else set()
    if "full_name" not in col_names:
        conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN full_name VARCHAR(255)")




app = FastAPI(
    title="LukaMagicBOT",
    description="Telegram Bot + Stripe Webhook Integration",
    lifespan=lifespan
)

# Sessions for admin area
app.add_middleware(SessionMiddleware, secret_key=ADMIN_SECRET)


def _html_page(title: str, body: str) -> str:
    """
    Legacy function - will be replaced by template system
    Keeping for backward compatibility
    """
    try:
        from template_engine import render_simple_page
        return render_simple_page(title, body)
    except ImportError:
        # Fallback to original implementation
        return (
            f"""
<!DOCTYPE html>
<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\"/>\n<title>{title}</title>\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n<style>
/* Base */
body{{background:#0b1220;color:#e2e8f0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;line-height:1.6}}
h1,h2,h3,h4,h5,h6{{color:#f1f5f9;font-weight:600}}
p{{color:#cbd5e1;line-height:1.6}}
a{{color:#60a5fa;text-decoration:underline}}
a:hover{{color:#93c5fd}}
.muted{{color:#94a3b8}}
.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
table{{border-collapse:collapse;width:100%;border-radius:10px;overflow:hidden}}
th,td{{padding:12px;border-bottom:1px solid #1f2937;text-align:left}}
th{{background:#111827;color:#f1f5f9;text-transform:uppercase;font-size:12px;letter-spacing:.6px;font-weight:600}}
tr:hover td{{background:#0e1626}}
label{{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#cbd5e1}}
input,select{{width:100%;background:#0b1220;color:#e2e8f0;border:1px solid #1f2937;border-radius:8px;padding:10px}}
form{{margin:0}}
a.button, button, input[type=submit]{{background:#6366f1;color:#fff;border:none;padding:9px 14px;border-radius:8px;text-decoration:none;cursor:pointer;font-weight:500}}
a.button:hover, button:hover, input[type=submit]:hover{{filter:brightness(1.1)}}
.danger{{background:#ef4444}}
.alert-success{{background:#222025;color:#10b981;border:1px solid #10b981;padding:15px;border-radius:8px}}
.alert-error{{background:#222025;color:#ef4444;border:1px solid #ef4444;padding:15px;border-radius:8px}}
.alert-warning{{background:#222025;color:#f59e0b;border:1px solid #f59e0b;padding:15px;border-radius:8px}}
.alert-info{{background:#222025;color:#3b82f6;border:1px solid #3b82f6;padding:15px;border-radius:8px}}
.light-bg-override{{background:rgb(8,10,13)!important;border:1px solid #374151!important;color:#e5e7eb!important}}
.light-bg-override h2,.light-bg-override h3{{color:#f9fafb!important}}
.light-bg-override p{{color:#d1d5db!important}}
.light-bg-override strong{{color:#f3f4f6!important}}
.debug-table td{{background:#1f2937;color:#e5e7eb;padding:12px}}
.debug-table th{{background:#374151;color:#f9fafb;font-weight:700}}
.debug-table tr:hover td{{background:#374151}}
.nav-buttons a{{background:#4f46e5;color:#ffffff;font-weight:600;box-shadow:0 2px 8px rgba(79,70,229,0.3);margin:5px}}
.nav-buttons a:hover{{background:#4338ca;transform:translateY(-1px);box-shadow:0 4px 12px rgba(79,70,229,0.4)}}
</style>\n</head>\n<body>\n{body}\n</body></html>"""
        )


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _require_admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_form(request: Request):
    try:
        from template_engine import render_template
        return HTMLResponse(render_template("admin_login", title="Admin Login"))
    except Exception as e:
        # Fallback to inline HTML
        body = """
        <h1>Admin Login</h1>
        <form method=\"post\" action=\"/admin/login\">\n<div class=\"row\">\n<label>Username <input name=\"username\" required/></label>\n<label>Password <input type=\"password\" name=\"password\" required/></label>\n<input type=\"submit\" value=\"Sign In\"/>\n</div>\n</form>
        """
        return HTMLResponse(_html_page("Admin Login", body))


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse(url="/admin/subscriptions", status_code=303)
    return HTMLResponse(_html_page("Admin Login", "<p>Invalid credentials.</p><p><a href=\"/admin/login\">Try again</a></p>"))


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


def _subscription_row(s):
    # Determine if user should show "Expulsar" button
    show_expulsar = s.telegram_user_id and s.status in ["active", "expired", "cancelled", "canceled"]
    
    expulsar_button = ""
    if show_expulsar:
        expulsar_button = f'<a class="button" href="/admin/subscriptions/{s.id}/expulsar" style="background: #dc2626; color: white; margin-left: 5px; padding: 6px 10px; font-size: 0.8em;" onclick="return confirm(\'Remove {s.email} from VIP groups?\');">🚫</a>'
    
    return f"<tr><td>{s.id}</td><td>{s.full_name or ''}</td><td>{s.email}</td><td>{s.telegram_user_id or ''}</td><td>{s.plan_type or ''}</td><td>{s.status or ''}</td><td>{(s.created_at or '')}</td><td>{(s.expires_at or '')}</td><td class=\"row\"><a class=\"button\" href=\"/admin/subscriptions/{s.id}/edit\">Edit</a><form method=\"post\" action=\"/admin/subscriptions/{s.id}/delete\" onsubmit=\"return confirm('Delete?');\"><input class=\"danger\" type=\"submit\" value=\"Delete\"/></form>{expulsar_button}</td></tr>"


@app.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_list_subscriptions(
    request: Request, 
    page: int = 1, 
    per_page: int = 50,
    search_email: str = "",
    search_name: str = "",
    search_telegram: str = "",
    search_status: str = ""
):
    _require_admin(request)
    from sqlalchemy import desc
    
    try:
        # Simple audit log (without external module)
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"[AUDIT] VIEW_SUBSCRIPTIONS | IP: {client_ip} | Page: {page}")
        
        # Get subscriptions with search and pagination
        with SessionLocal() as db:
            # Build query with search filters
            query = db.query(models.Subscription)
            
            # Apply search filters
            if search_email:
                query = query.filter(models.Subscription.email.ilike(f"%{search_email}%"))
            if search_name:
                query = query.filter(models.Subscription.full_name.ilike(f"%{search_name}%"))
            if search_telegram:
                query = query.filter(models.Subscription.telegram_user_id.ilike(f"%{search_telegram}%"))
            if search_status:
                query = query.filter(models.Subscription.status.ilike(f"%{search_status}%"))
            
            # Get total count with filters
            total_count = query.count()
            
            # Get page data
            offset = (page - 1) * per_page
            subs = query.order_by(desc(models.Subscription.id)).offset(offset).limit(per_page).all()
        
        total_pages = (total_count + per_page - 1) // per_page
        
        rows = "".join(_subscription_row(s) for s in subs)
        rows_html = rows if rows else '<tr><td colspan=9 class="muted">No records</td></tr>'
        
        # Build search params for pagination links
        search_params = ""
        if search_email:
            search_params += f"&search_email={search_email}"
        if search_name:
            search_params += f"&search_name={search_name}"
        if search_telegram:
            search_params += f"&search_telegram={search_telegram}"
        if search_status:
            search_params += f"&search_status={search_status}"
        
        # Pagination HTML with search preservation
        pagination_html = ""
        if total_pages > 1:
            pagination_html = f"""
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-top: 20px; text-align: center;">
                <p><strong>Page {page} of {total_pages}</strong> (Total: {total_count} subscriptions)</p>
                <div style="margin-top: 10px;">
                    {f'<a href="/admin/subscriptions?page={page-1}{search_params}" class="button">← Previous</a>' if page > 1 else ''}
                    {f'<a href="/admin/subscriptions?page={page+1}{search_params}" class="button" style="margin-left: 10px;">Next →</a>' if page < total_pages else ''}
                </div>
            </div>
            """
        
        try:
            from template_engine import render_template
            return HTMLResponse(render_template(
                "admin_subscriptions",
                title="Subscriptions",
                rows_html=rows_html,
                pagination_html=pagination_html or "",
                search_email=search_email,
                search_name=search_name,
                search_telegram=search_telegram,
                search_status=search_status
            ))
            
        except Exception as template_error:
            logger.warning(f"Template engine failed, using fallback: {template_error}")
            # Enhanced fallback with Tools menu and search
            search_form = f"""
            <form method="get" action="/admin/subscriptions" style="background: rgb(8, 10, 13); padding: 20px; border-radius: 8px; margin-bottom: 20px;">
                <h3>🔍 Search Subscriptions</h3>
                <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr auto auto; gap: 10px; align-items: end;">
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: #cbd5e1;">Email:</label>
                        <input type="text" name="search_email" value="{search_email}" placeholder="user@example.com" style="width: 100%; padding: 8px; border: 1px solid #374151; border-radius: 4px; background: #0f172a; color: #f1f5f9;">
                    </div>
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: #cbd5e1;">Name:</label>
                        <input type="text" name="search_name" value="{search_name}" placeholder="John Doe" style="width: 100%; padding: 8px; border: 1px solid #374151; border-radius: 4px; background: #0f172a; color: #f1f5f9;">
                    </div>
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: #cbd5e1;">Telegram ID:</label>
                        <input type="text" name="search_telegram" value="{search_telegram}" placeholder="123456789" style="width: 100%; padding: 8px; border: 1px solid #374151; border-radius: 4px; background: #0f172a; color: #f1f5f9;">
                    </div>
                    <div>
                        <label style="display: block; margin-bottom: 5px; color: #cbd5e1;">Status:</label>
                        <select name="search_status" style="width: 100%; padding: 8px; border: 1px solid #374151; border-radius: 4px; background: #0f172a; color: #f1f5f9;">
                            <option value="">All Status</option>
                            <option value="active" {"selected" if search_status == "active" else ""}>✅ Active</option>
                            <option value="past_due" {"selected" if search_status == "past_due" else ""}>⚠️ Past Due</option>
                            <option value="canceled" {"selected" if search_status == "canceled" else ""}>❌ Canceled</option>
                            <option value="auto_removed" {"selected" if search_status == "auto_removed" else ""}>🚫 Auto Removed</option>
                            <option value="manually_removed" {"selected" if search_status == "manually_removed" else ""}>👤 Manually Removed</option>
                            <option value="incomplete" {"selected" if search_status == "incomplete" else ""}>🔄 Incomplete</option>
                            <option value="trialing" {"selected" if search_status == "trialing" else ""}>🆓 Trialing</option>
                        </select>
                    </div>
                    <button type="submit" style="background: #10b981; color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">🔍 Search</button>
                    <a href="/admin/subscriptions" style="background: #6b7280; color: white; padding: 8px 16px; border-radius: 4px; text-decoration: none;">Clear</a>
                </div>
            </form>
            """
            
            tools_menu = """
            <div style="position: relative; display: inline-block; margin-right: 10px;">
                <button onclick="toggleDropdown()" style="background: #3b82f6; color: white; padding: 9px 14px; border: none; border-radius: 8px; cursor: pointer;">🛠️ Tools ▼</button>
                <div id="toolsDropdown" style="display: none; position: absolute; background: #0f172a; border: 1px solid #374151; border-radius: 8px; padding: 10px; top: 100%; left: 0; min-width: 200px; z-index: 1000;">
                    <a href="/admin/data" style="display: block; padding: 8px; color: #e2e8f0; text-decoration: none; border-radius: 4px;">📊 Data Viewer</a>
                    <a href="/admin/debug/database" style="display: block; padding: 8px; color: #e2e8f0; text-decoration: none; border-radius: 4px;">🔧 Database Debug</a>
                    <a href="/admin/removal/diagnose" style="display: block; padding: 8px; color: #e2e8f0; text-decoration: none; border-radius: 4px;">🔍 System Diagnosis</a>
                    <a href="/admin/removal/debug-specific" style="display: block; padding: 8px; color: #e2e8f0; text-decoration: none; border-radius: 4px;">🎯 Debug Users</a>
                    <a href="/admin/setup-tables" style="display: block; padding: 8px; color: #e2e8f0; text-decoration: none; border-radius: 4px;">🔧 Setup Tables</a>
                </div>
            </div>
            <script>
            function toggleDropdown() {{
                const dropdown = document.getElementById('toolsDropdown');
                dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
            }}
            window.onclick = function(event) {{
                if (!event.target.matches('button')) {{
                    const dropdown = document.getElementById('toolsDropdown');
                    if (dropdown.style.display === 'block') {{
                        dropdown.style.display = 'none';
                    }}
                }}
            }}
            </script>
            """
            
            body = f"""
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;">
                <h1>Subscriptions ({total_count} total)</h1>
                <div>
                    <a href="/admin/subscriptions/new" style="background: #6366f1; color: white; padding: 9px 14px; border-radius: 8px; text-decoration: none; margin-right: 10px;">New</a>
                    <a href="/admin/removal" style="background: #ef4444; color: white; padding: 9px 14px; border-radius: 8px; text-decoration: none; margin-right: 10px;">🚫 Auto Removal</a>
                    <a href="/admin/groups" style="background: #10b981; color: white; padding: 9px 14px; border-radius: 8px; text-decoration: none; margin-right: 10px;">🏘️ VIP Groups</a>
                    {tools_menu}
                    <a href="/admin/logout" style="background: #6b7280; color: white; padding: 9px 14px; border-radius: 8px; text-decoration: none;">Logout</a>
                </div>
            </div>
            {search_form}
            <table style="border-collapse: collapse; width: 100%; background: #0f172a; border-radius: 8px; overflow: hidden;">
                <thead>
                    <tr style="background: #1f2937;">
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">ID</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Name</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Email</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Telegram ID</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Plan</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Status</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Created</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Expires</th>
                        <th style="padding: 12px; color: #f1f5f9; text-align: left;">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
            {pagination_html}
            """
            return HTMLResponse(_html_page("Subscriptions", body))
        
    except Exception as e:
        logger.error(f"Error in admin_list_subscriptions: {e}")
        return HTMLResponse(_html_page("Error", f"<h1>Error loading subscriptions: {str(e)}</h1>"))


@app.get("/admin/subscriptions/new", response_class=HTMLResponse)
async def admin_new_subscription_form(request: Request):
    _require_admin(request)
    body = """
    <h1>New Subscription</h1>
    <form method=\"post\" action=\"/admin/subscriptions\">\n<div class=\"row\">\n<label>Name <input name=\"full_name\"/></label>\n<label>Email <input name=\"email\" required/></label>\n<label>Telegram ID <input name=\"telegram_user_id\"/></label>\n<label>Plan <select name=\"plan_type\"><option value=\"monthly\">monthly</option><option value=\"quarterly\">quarterly</option><option value=\"annual\">annual</option></select></label>\n<label>Status <select name=\"status\"><option value=\"active\">active</option><option value=\"past_due\">past_due</option><option value=\"canceled\">canceled</option></select></label>\n<label>Expires at (YYYY-MM-DD) <input name=\"expires_at\"/></label>\n<input type=\"submit\" value=\"Create\"/>\n</div>\n</form>
    """
    return HTMLResponse(_html_page("New Subscription", body))


@app.get("/admin/subscriptions/{sub_id}/edit", response_class=HTMLResponse)
async def admin_edit_subscription_form(request: Request, sub_id: int):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Not found")
    def val(x):
        return "" if x is None else str(x)
    expires_val = val(sub.expires_at.date() if sub.expires_at else "")
    body = f"""
    <h1>Edit Subscription #{sub.id}</h1>
    <form method=\"post\" action=\"/admin/subscriptions/{sub.id}\">\n<div class=\"row\">\n<label>Name <input name=\"full_name\" value=\"{val(sub.full_name)}\"/></label>\n<label>Email <input name=\"email\" value=\"{val(sub.email)}\" required/></label>\n<label>Telegram ID <input name=\"telegram_user_id\" value=\"{val(sub.telegram_user_id)}\"/></label>\n<label>Plan <select name=\"plan_type\"><option {'selected' if sub.plan_type=='monthly' else ''} value=\"monthly\">monthly</option><option {'selected' if sub.plan_type=='quarterly' else ''} value=\"quarterly\">quarterly</option><option {'selected' if sub.plan_type=='annual' else ''} value=\"annual\">annual</option></select></label>\n<label>Status <select name=\"status\"><option {'selected' if sub.status=='active' else ''} value=\"active\">active</option><option {'selected' if sub.status=='past_due' else ''} value=\"past_due\">past_due</option><option {'selected' if sub.status=='canceled' else ''} value=\"canceled\">canceled</option></select></label>\n<label>Expires at (YYYY-MM-DD) <input name=\"expires_at\" value=\"{expires_val}\"/></label>\n<input type=\"submit\" value=\"Save\"/>\n</div>\n</form>
    """
    return HTMLResponse(_html_page("Edit Subscription", body))


def _parse_date_or_none(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


@app.post("/admin/subscriptions")
async def admin_create_subscription(
    request: Request,
    full_name: str = Form(None),
    email: str = Form(...),
    telegram_user_id: str = Form(None),
    plan_type: str = Form("monthly"),
    status: str = Form("active"),
    expires_at: str = Form(None),
):
    _require_admin(request)
    with SessionLocal() as db:
        sub = models.Subscription(
            full_name=full_name,
            email=email.lower().strip(),
            telegram_user_id=telegram_user_id,
            plan_type=plan_type,
            status=status,
            expires_at=_parse_date_or_none(expires_at),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(sub)
        db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sub_id}")
async def admin_update_subscription(
    request: Request,
    sub_id: int,
    full_name: str = Form(None),
    email: str = Form(...),
    telegram_user_id: str = Form(None),
    plan_type: str = Form("monthly"),
    status: str = Form("active"),
    expires_at: str = Form(None),
):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Not found")
        sub.full_name = full_name
        sub.email = email.lower().strip()
        sub.telegram_user_id = telegram_user_id
        sub.plan_type = plan_type
        sub.status = status
        sub.expires_at = _parse_date_or_none(expires_at)
        sub.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sub_id}/delete")
async def admin_delete_subscription(request: Request, sub_id: int):
    _require_admin(request)
    with SessionLocal() as db:
        sub = db.query(models.Subscription).filter_by(id=sub_id).first()
        if sub:
            db.delete(sub)
            db.commit()
    return RedirectResponse(url="/admin/subscriptions", status_code=303)


@app.get("/admin/subscriptions/{sub_id}/expulsar")
async def admin_expulsar_user(request: Request, sub_id: int):
    """Expulsar usuário individual dos grupos VIP"""
    _require_admin(request)
    
    try:
        with SessionLocal() as db:
            subscription = db.query(models.Subscription).filter_by(id=sub_id).first()
            if not subscription:
                return HTMLResponse(_html_page(
                    "User Not Found",
                    f"""
                    <h1>❌ Subscription Not Found</h1>
                    <p>Subscription with ID {sub_id} not found.</p>
                    <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                    """
                ))
            
            email = subscription.email
            telegram_user_id = subscription.telegram_user_id
            
            if not telegram_user_id:
                return HTMLResponse(_html_page(
                    "Cannot Remove User",
                    f"""
                    <h1>❌ Cannot Remove User</h1>
                    <p><strong>User:</strong> {email}</p>
                    <p><strong>Problem:</strong> No Telegram ID found</p>
                    <p>This user cannot be removed from groups because they don't have a Telegram ID linked.</p>
                    <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                    """
                ))
            
            if not application or not application.bot:
                raise Exception("Bot application not available")
            
            bot = application.bot
            user_id_int = int(telegram_user_id)
            
            results = {
                'removed_groups': [],
                'failed_groups': [],
                'dm_sent': False,
                'details': []
            }
            
            # Remove from each VIP group
            for group_id in VIP_GROUP_IDS:
                try:
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id_int)
                    await bot.unban_chat_member(chat_id=group_id, user_id=user_id_int, only_if_banned=True)
                    results['removed_groups'].append(group_id)
                    detail = f"✅ Removed from group {group_id}"
                    logger.info(f"{email}: {detail}")
                    results['details'].append(detail)
                except Exception as e:
                    results['failed_groups'].append((group_id, str(e)))
                    detail = f"❌ Failed to remove from group {group_id}: {str(e)}"
                    logger.error(f"{email}: {detail}")
                    results['details'].append(detail)
            
            # Send notification DM
            try:
                message = f"""🚫 VIP Access Removed

Hi! You have been manually removed from the VIP groups.

📧 Account: {email}
⏰ Removed: Just now

💬 Questions? Contact support @Sthefano_p"""
                
                await bot.send_message(chat_id=user_id_int, text=message)
                results['dm_sent'] = True
                detail = "✅ Notification DM sent"
                logger.info(f"{email}: {detail}")
                results['details'].append(detail)
            except Exception as e:
                detail = f"❌ Failed to send DM: {str(e)}"
                logger.error(f"{email}: {detail}")
                results['details'].append(detail)
            
            # Update subscription status
            if results['removed_groups']:
                subscription.status = "manually_removed"
                subscription.updated_at = datetime.utcnow()
                db.commit()
                detail = "✅ Status updated to manually_removed"
                logger.info(f"{email}: {detail}")
                results['details'].append(detail)
            
            details_html = "<br>".join(results['details'])
            success_count = len(results['removed_groups'])
            error_count = len(results['failed_groups'])
            
            return HTMLResponse(_html_page(
                "User Removal Results",
                f"""
                <h1>🚫 User Removal Results</h1>
                
                <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <h2>👤 User Information</h2>
                    <p><strong>Email:</strong> {email}</p>
                    <p><strong>Telegram ID:</strong> {telegram_user_id}</p>
                    <p><strong>Previous Status:</strong> {subscription.status}</p>
                </div>
                
                <div class="{'alert-success' if success_count > 0 else 'alert-error'}" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <h2>📊 Removal Summary</h2>
                    <p><strong>✅ Successfully removed from:</strong> {success_count} groups</p>
                    <p><strong>❌ Failed to remove from:</strong> {error_count} groups</p>
                    <p><strong>📱 DM sent:</strong> {'✅ Yes' if results['dm_sent'] else '❌ No'}</p>
                </div>
                
                <h2>📋 Detailed Log</h2>
                <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 0.9em;">
                    {details_html}
                </div>
                
                <div style="margin-top: 20px;">
                    <a href="/admin/subscriptions" class="button">← Back to Admin</a>
                    <a href="/admin/removal" class="button">📊 Auto Removal Dashboard</a>
                </div>
                """
            ))
            
    except Exception as e:
        logger.error(f"Manual expulsar failed: {e}")
        return HTMLResponse(_html_page(
            "Expulsar Failed", 
            f"""
            <h1>❌ Expulsar Failed</h1>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><a href="/admin/subscriptions">← Back to Admin</a></p>
            """
        ))


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/telegram/{token}")
async def telegram_webhook(token: str, request: Request):
    """
    Receive Telegram updates and process them via PTB.

    Security: validate token in URL
    """
    if token != TOKEN:
        logger.warning("Invalid token in webhook: %s", token)
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update_data = await request.json()
        update = Update.de_json(update_data, application.bot)
        await application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.critical(
            "Unexpected error processing Telegram update: %s", e, exc_info=True)
        raise


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Process Stripe webhooks.

    Security: validate webhook signature
    """
    if not DATABASE_AVAILABLE:
        logger.warning("Stripe webhook received but database not available")
        return {"status": "database_unavailable"}

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing signature")

    try:
        # Validate signature
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as exc:
        logger.error("Invalid payload in stripe webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except stripe.error.SignatureVerificationError as exc:
        logger.error("Invalid signature in stripe webhook: %s", exc)
        raise HTTPException(
            status_code=400, detail="Invalid signature") from exc

    # Processar evento
    with SessionLocal() as db:
        logger.info("DB URL runtime: %s", db_path_info(db))
        try:
            success = await process_stripe_webhook_event(db, event)
            if success:
                logger.info(
                    "Stripe webhook processed: %s (%s)", event['id'], event['type'])
                return {"status": "received"}
            else:
                logger.error(
                    "Failed to process stripe webhook: %s", event['id'])
                raise HTTPException(
                    status_code=500, detail="Processing failed")
        except Exception as e:
            logger.critical(
                "Erro inesperado no handler do stripe webhook: %s", e, exc_info=True)
            raise


@app.post("/stripe/webhook-test")
async def stripe_webhook_test(request: Request):
    """
    Endpoint de teste para webhooks do Stripe (sem validação de assinatura)
    USAR APENAS PARA DESENVOLVIMENTO/TESTE
    """
    if not DATABASE_AVAILABLE:
        logger.warning(
            "Stripe webhook test received but database not available")
        return {"status": "database_unavailable"}

    try:
        event = await request.json()
        logger.info(
            "Test webhook received: %s - %s",
            event.get('type', 'unknown'), event.get('id', 'no-id'))

        # Processar evento
        with SessionLocal() as db:
            logger.info("DB URL runtime: %s", db_path_info(db))
            success = await process_stripe_webhook_event(db, event)
            if success:
                logger.info(
                    f"Test webhook processed successfully: {event.get('id')}")
                return {"status": "received", "processed": True}
            else:
                logger.warning(
                    f"Test webhook processing failed: {event.get('id')}")
                return {"status": "received", "processed": False}

    except Exception as e:
        logger.critical(
            "Erro inesperado no test webhook handler: %s", e, exc_info=True)
        raise

# ======================
# Bot Handlers Setup
# ======================


def setup_handlers(app: Application):
    """Configura todos os handlers do bot"""
    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("groupid", groupid))
    # Removed: testinvite command

    # ConversationHandler para Unlock Access
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            unlock_access_prompt, pattern=r"^unlock\.access$")],
        states={
            ASK_EMAIL: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, unlock_access_check_email)]
        },
        fallbacks=[
            CommandHandler("cancel", unlock_cancel),
            CallbackQueryHandler(back_to_home, pattern=r"^home\.back$")
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)

    # CallbackQueryHandler global para outros botões (exceto unlock.access e home.back no contexto da conversa)
    app.add_handler(CallbackQueryHandler(
        button_router, pattern=r"^(plans\.open|howitworks|myid\.show)$"))
    
    # Handler separado para home.back fora do contexto da conversa
    app.add_handler(CallbackQueryHandler(
        back_to_home, pattern=r"^home\.back$"))
# ======================
# Main
# ======================


def main():
    """
    Executa o bot via FastAPI unificado

    Local: LOCAL_POLLING=1 → FastAPI + bot polling
    Produção: LOCAL_POLLING=0 → FastAPI + bot webhook
    """
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN não definido. Configure no .env ou nas Variables do Railway.")

    # Configurar porta
    port = int(os.environ.get("PORT", "8080"))

    if os.getenv("LOCAL_POLLING", "0") == "1":
        logger.info("Starting in LOCAL mode: FastAPI + Bot Polling")
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
    else:
        logger.info("Starting in PRODUCTION mode: FastAPI + Bot Webhook")
        uvicorn.run(app, host="0.0.0.0", port=port, reload=False)


# ======================
# 🚫 Auto Removal System
# ======================

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
from typing import List, Dict, Any

# Timezone e utilitários robustos
TZ_NAME = os.getenv("TZ", "UTC")

def now_tz():
    """Retorna datetime atual com timezone configurado"""
    try:
        return datetime.now(ZoneInfo(TZ_NAME))
    except Exception:
        return datetime.now(timezone.utc)

# Global scheduler instance
scheduler: AsyncIOScheduler = None
cleanup_lock = asyncio.Lock()  # evita sobreposição do job


async def write_scheduler_heartbeat():
    """Grava heartbeat do scheduler no DB para diagnóstico"""
    try:
        with SessionLocal() as db:
            # Simples log de heartbeat - pode expandir depois
            logger.debug(f"Scheduler heartbeat: {now_tz()}")
    except Exception as e:
        logger.debug(f"Heartbeat falhou (não crítico): {e}")


async def safe_cleanup_expired_subscriptions():
    """Wrapper segura para cleanup_expired_subscriptions com lock e error handling"""
    logger.info("🔥 SCHEDULER JOB TRIGGERED - safe_cleanup_expired_subscriptions called!")
    
    if cleanup_lock.locked():
        # evita concorrência: apenas loga e sai
        logger.warning("cleanup: já em execução; ignorando chamada simultânea")
        return
    
    async with cleanup_lock:
        try:
            logger.info("🚀 Iniciando cleanup seguro de assinaturas expiradas")
            logger.info(f"🕐 Execution time: {now_tz()}")
            
            await cleanup_expired_past_gracecriptions()
            
            logger.info("✅ Cleanup seguro concluído com sucesso")
        except Exception as e:
            logger.exception("❌ Cleanup falhou: %s", e)
        finally:
            # opcional: gravar heartbeat/last_run no DB p/ diagnóstico
            try:
                await write_scheduler_heartbeat()
                logger.info("💓 Heartbeat gravado")
            except Exception as e:
                logger.debug(f"heartbeat indisponível: {e}")


async def _remove_user_from_vip_groups(bot, user_id: int, groups: List[int] = None) -> Dict[str, Any]:
    """
    Remove usuário dos grupos VIP
    
    Returns:
        Dict com resultado: {
            'success': bool,
            'groups_removed': List[int],
            'errors': Dict[int, str]
        }
    """
    if not groups:
        groups = VIP_GROUP_IDS
    
    result = {
        'success': False,
        'groups_removed': [],
        'errors': {}
    }
    
    for group_id in groups:
        try:
            await bot.ban_chat_member(
                chat_id=group_id,
                user_id=user_id,
                until_date=None  # Ban permanente
            )
            # Imediatamente desbanir para apenas remover (não banir)
            await bot.unban_chat_member(
                chat_id=group_id,
                user_id=user_id,
                only_if_banned=True
            )
            result['groups_removed'].append(group_id)
            logger.info(f"✅ Removed user {user_id} from group {group_id}")
            
        except Exception as e:
            error_msg = str(e)
            result['errors'][group_id] = error_msg
            logger.error(f"❌ Failed to remove user {user_id} from group {group_id}: {error_msg}")
    
    result['success'] = len(result['groups_removed']) > 0
    return result


async def _send_renewal_dm(bot, user_id: int, email: str, plan_type: str = None) -> bool:
    """
    Enviar DM com link de renovação
    
    Returns:
        bool: True se enviou com sucesso
    """
    try:
        plan_text = f" ({plan_type})" if plan_type else ""
        renewal_links = {
            'monthly': STRIPE_MONTHLY_URL,
            'quarterly': STRIPE_QUARTERLY_URL,
            'annual': STRIPE_ANNUAL_URL
        }
        
        # Link específico do plano ou link mensal como padrão
        renewal_link = renewal_links.get(plan_type, STRIPE_MONTHLY_URL)
        
        message = f"""
🚨 **VIP Access Expired**

Hi! Your VIP subscription{plan_text} has expired and you've been removed from the VIP groups.

📧 **Account**: {email}
⏰ **Expired**: Just now

🔄 **Renew Now**:
• [Monthly Plan]({STRIPE_MONTHLY_URL})
• [Quarterly Plan]({STRIPE_QUARTERLY_URL}) 
• [Annual Plan]({STRIPE_ANNUAL_URL})

💬 **Need Help?** Contact support: @Sthefano_p

Thank you for being a VIP member! 🌟
        """.strip()
        
        await bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
        logger.info(f"✅ Sent renewal DM to user {user_id} ({email})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send renewal DM to user {user_id} ({email}): {e}")
        return False


async def _send_expiry_warning_dm(bot, user_id: int, email: str, plan_type: str, days_until_expiry: int, expires_at: datetime) -> bool:
    """
    Enviar DM de aviso de expiração
    
    Args:
        days_until_expiry: Quantos dias até expirar (7, 3, 1, 0)
    """
    try:
        plan_text = f" ({plan_type})" if plan_type else ""
        expires_date = expires_at.strftime('%d/%m/%Y')
        
        if days_until_expiry == 0:
            # Expira hoje
            subject = "🚨 VIP Expires TODAY!"
            urgency = "TODAY"
            icon = "🚨"
        elif days_until_expiry == 1:
            subject = "⚠️ VIP Expires Tomorrow!"
            urgency = "TOMORROW"
            icon = "⚠️"
        elif days_until_expiry <= 3:
            subject = f"⏰ VIP Expires in {days_until_expiry} Days"
            urgency = f"in {days_until_expiry} days"
            icon = "⏰"
        else:
            subject = f"📅 VIP Expires in {days_until_expiry} Days"
            urgency = f"in {days_until_expiry} days"
            icon = "📅"
        
        message = f"""{icon} {subject}

Hi! Your VIP subscription{plan_text} expires {urgency}.

📧 Account: {email}
📅 Expires: {expires_date}
⏰ Time left: {days_until_expiry} day{'s' if days_until_expiry != 1 else ''}

🔄 Renew now to keep VIP access:
• Monthly: {STRIPE_MONTHLY_URL}
• Quarterly: {STRIPE_QUARTERLY_URL}
• Annual: {STRIPE_ANNUAL_URL}

💬 Need help? Contact @Sthefano_p

Don't lose your VIP benefits! 🌟"""
        
        await bot.send_message(
            chat_id=user_id,
            text=message,
            disable_web_page_preview=True
        )
        
        logger.info(f"✅ Sent {days_until_expiry}-day warning to user {user_id} ({email})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send {days_until_expiry}-day warning to user {user_id} ({email}): {e}")
        return False


async def send_expiry_notifications():
    """
    Enviar notificações de expiração antecipadas
    Executa diariamente para verificar avisos
    """
    logger.info("📱 Starting expiry notification process...")
    
    if not application or not application.bot:
        logger.error("❌ Bot application not available for notifications")
        return
    
    bot = application.bot
    notification_counts = {
        '7_days': 0,
        '3_days': 0,
        '1_day': 0,
        'today': 0,
        'errors': 0
    }
    
    with SessionLocal() as db:
        try:
            from crud import (
                get_subscriptions_expiring_in_days,
                has_notification_been_sent,
                log_notification
            )
            
            # Notification schedule: 7 days, 3 days, 1 day, today
            notification_schedule = [
                (7, '7_days'),
                (3, '3_days'), 
                (1, '1_day'),
                (0, 'today')
            ]
            
            for days, notification_type in notification_schedule:
                logger.info(f"📅 Checking subscriptions expiring in {days} days...")
                
                expiring_subs = get_subscriptions_expiring_in_days(db, days)
                logger.info(f"Found {len(expiring_subs)} subscriptions expiring in {days} days")
                
                for sub in expiring_subs:
                    # Check if notification already sent
                    if has_notification_been_sent(db, sub.id, notification_type):
                        logger.info(f"⏭️ Skipping {sub.email} - {notification_type} notification already sent")
                        continue
                    
                    try:
                        user_id_int = int(sub.telegram_user_id)
                        
                        # Send warning DM
                        dm_sent = await _send_expiry_warning_dm(
                            bot, user_id_int, sub.email, sub.plan_type, days, sub.expires_at
                        )
                        
                        # Log the notification
                        log_notification(
                            db, sub.email, sub.telegram_user_id, notification_type,
                            sub.id, sub.expires_at, dm_sent,
                            error_message=None if dm_sent else "Failed to send DM"
                        )
                        
                        if dm_sent:
                            notification_counts[notification_type] += 1
                        else:
                            notification_counts['errors'] += 1
                            
                    except ValueError:
                        logger.error(f"❌ Invalid telegram_user_id for {sub.email}: {sub.telegram_user_id}")
                        notification_counts['errors'] += 1
                    except Exception as e:
                        logger.error(f"❌ Error processing notification for {sub.email}: {e}")
                        notification_counts['errors'] += 1
                    
                    # Small delay between notifications
                    await asyncio.sleep(0.5)
            
            # Log summary
            total_sent = sum(count for key, count in notification_counts.items() if key != 'errors')
            logger.info(
                f"📱 Notification process completed: "
                f"{total_sent} sent, "
                f"{notification_counts['errors']} errors, "
                f"Details: {notification_counts}"
            )
            
        except Exception as e:
            logger.error(f"❌ Critical error in send_expiry_notifications: {e}", exc_info=True)


async def cleanup_expired_past_gracecriptions():
    """
    Rotina principal de limpeza automática
    Executa diariamente às 2h da manhã
    """
    logger.info("🚫 ===== AUTOMATIC CLEANUP EXECUTION STARTED =====")
    logger.info(f"🕐 Execution timestamp: {now_tz()}")
    logger.info(f"🤖 Application available: {application is not None}")
    logger.info(f"🤖 Bot available: {application.bot is not None if application else False}")
    
    if not application or not application.bot:
        logger.error("❌ Bot application not available for cleanup")
        logger.error("❌ CLEANUP ABORTED - No bot instance")
        return
    
    bot = application.bot
    processed_count = 0
    success_count = 0
    error_count = 0
    not_found_count = 0
    whitelisted_count = 0
    
    with SessionLocal() as db:
        try:
            # 1. Buscar assinaturas que devem ser removidas (com grace period)
            from crud import (
                get_subscriptions_past_grace_period, get_cancelled_subscriptions,
                is_whitelisted, log_removal_attempt, update_removal_log,
                mark_subscription_processed
            )
            
            # Grace period configurável (padrão: 3 dias)
            grace_period_days = int(os.getenv("GRACE_PERIOD_DAYS", "3"))
            logger.info(f"⏰ Using grace period of {grace_period_days} days")
            
            # Buscar apenas assinaturas que passaram do grace period
            expired_past_grace = get_subscriptions_past_grace_period(db, grace_period_days)
            cancelled_subs = get_cancelled_subscriptions(db)
            all_subs = expired_past_grace + cancelled_subs
            
            # Log detalhado da lógica de expiração
            current_time = datetime.utcnow()
            logger.info(f"🕐 Current server time: {current_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            logger.info(f"📊 Found {len(expired_past_grace)} past grace period and {len(cancelled_subs)} cancelled subscriptions")
            
            # Log de cada assinatura que será removida
            for sub in expired_past_grace:
                if sub.expires_at:
                    days_expired = (current_time - sub.expires_at).days
                    logger.info(f"❌ PAST GRACE: {sub.email} - expired {days_expired} days ago (grace period: {grace_period_days} days)")
                else:
                    logger.info(f"❌ PAST GRACE: {sub.email} - no expiry date set")
            
            for sub in cancelled_subs:
                logger.info(f"🚫 CANCELLED: {sub.email} - status: {sub.status}")
            
            for sub in all_subs:
                processed_count += 1
                email = sub.email
                telegram_user_id = sub.telegram_user_id
                reason = "expired" if sub in expired_past_grace else "cancelled"
                
                logger.info(f"🔄 Processing subscription ID {sub.id}: {email} (reason: {reason})")
                logger.info(f"   - Status: {sub.status}")
                logger.info(f"   - Expires at: {sub.expires_at}")
                logger.info(f"   - Telegram ID: {telegram_user_id}")
                
                # Verificar whitelist (PRIORIDADE: Telegram ID, fallback: email)
                logger.info(f"🔍 Checking whitelist for {email} (Telegram ID: {telegram_user_id})")
                
                # Garantir que telegram_user_id seja string para comparação
                telegram_id_str = str(telegram_user_id) if telegram_user_id else None
                logger.info(f"🔍 Telegram ID as string: '{telegram_id_str}'")
                
                is_protected = is_whitelisted(db, email=email, telegram_user_id=telegram_id_str)
                logger.info(f"🛡️ Whitelist check result: {is_protected}")
                
                # Debug: Listar todas as entradas da whitelist para comparação
                try:
                    whitelist_entries = db.query(models.Whitelist).all()
                    logger.info(f"🔍 Current whitelist entries ({len(whitelist_entries)}):")
                    for entry in whitelist_entries:
                        logger.info(f"   - Telegram ID: '{entry.telegram_user_id}' (type: {type(entry.telegram_user_id)})")
                        logger.info(f"   - Email: '{entry.email}'")
                        logger.info(f"   - Match check: telegram_id_str == entry.telegram_user_id = {telegram_id_str == entry.telegram_user_id}")
                except Exception as e:
                    logger.warning(f"Could not debug whitelist: {e}")
                
                if is_protected:
                    whitelisted_count += 1
                    logger.info(f"⚪ PROTECTED: Skipping {email} (Telegram ID: {telegram_user_id}) - whitelisted")
                    log_removal_attempt(
                        db, email, telegram_user_id, reason, 
                        status="whitelisted", dm_sent=False, 
                        error_message="User is protected by whitelist"
                    )
                    continue
                else:
                    logger.info(f"🚫 NOT PROTECTED: {email} (Telegram ID: {telegram_user_id}) - will be removed")
                
                # Verificar se tem telegram_user_id
                if not telegram_user_id:
                    not_found_count += 1
                    logger.warning(f"⚠️ No telegram_user_id for {email} - CANNOT REMOVE")
                    log_removal_attempt(
                        db, email, None, reason,
                        status="no_telegram_id", 
                        error_message="Telegram ID not found in database"
                    )
                    continue
                
                # Criar log inicial
                removal_log = log_removal_attempt(
                    db, email, telegram_user_id, reason, status="processing"
                )
                
                if not removal_log:
                    error_count += 1
                    continue
                
                try:
                    # Converter telegram_user_id para int
                    user_id_int = int(telegram_user_id)
                    logger.info(f"🎯 Starting removal process for user {user_id_int} ({email})")
                    
                    # 2. Remover dos grupos VIP
                    logger.info(f"🚫 Attempting to remove user {user_id_int} from VIP groups: {VIP_GROUP_IDS}")
                    removal_result = await _remove_user_from_vip_groups(bot, user_id_int)
                    logger.info(f"🚫 Removal result: {removal_result}")
                    
                    # 3. Enviar DM de renovação
                    logger.info(f"📱 Attempting to send renewal DM to user {user_id_int}")
                    dm_sent = await _send_renewal_dm(bot, user_id_int, email, sub.plan_type)
                    logger.info(f"📱 DM sent result: {dm_sent}")
                    
                    # 4. Atualizar log com resultado
                    if removal_result['success']:
                        success_count += 1
                        status = "success"
                        error_msg = None
                        if removal_result['errors']:
                            error_msg = f"Partial success. Errors: {removal_result['errors']}"
                    else:
                        error_count += 1
                        status = "failed"
                        error_msg = f"All removals failed: {removal_result['errors']}"
                    
                    update_removal_log(
                        db, removal_log.id,
                        status=status,
                        groups_removed_from=removal_result['groups_removed'],
                        error_message=error_msg,
                        dm_sent=dm_sent
                    )
                    
                    # 5. Marcar assinatura como processada
                    mark_subscription_processed(db, sub.id, "auto_removed")
                    
                    logger.info(f"✅ Processed {email}: {status}")
                    
                except ValueError:
                    error_count += 1
                    logger.error(f"❌ Invalid telegram_user_id for {email}: {telegram_user_id}")
                    update_removal_log(
                        db, removal_log.id,
                        status="invalid_user_id",
                        error_message=f"Invalid telegram_user_id: {telegram_user_id}"
                    )
                    
                except Exception as e:
                    error_count += 1
                    logger.error(f"❌ Error processing {email}: {e}")
                    update_removal_log(
                        db, removal_log.id,
                        status="error",
                        error_message=str(e)
                    )
                
                # Pequeno delay entre processamentos
                await asyncio.sleep(1)
            
            # Log final
            logger.info(
                f"🏁 Cleanup completed: "
                f"{processed_count} processed, "
                f"{success_count} successful, "
                f"{error_count} errors, "
                f"{not_found_count} not found, "
                f"{whitelisted_count} whitelisted"
            )
            
        except Exception as e:
            logger.error(f"❌ Critical error in cleanup_expired_past_gracecriptions: {e}", exc_info=True)


def start_scheduler():
    """Iniciar o scheduler para remoções automáticas (robusto e à prova de perdas)."""
    global scheduler
    if scheduler and getattr(scheduler, "running", False):
        logger.info("⏰ Scheduler already running")
        return  # já iniciado

    try:
        # Configurar jobs
        cleanup_time = os.getenv("CLEANUP_TIME", "2")  # Hora do dia (0-23)
        notification_time = os.getenv("NOTIFICATION_TIME", "10")  # Hora para avisos (padrão: 10h)
        
        logger.info(f"🔍 Starting robust scheduler initialization...")
        logger.info(f"📅 Configuration:")
        logger.info(f"   - Timezone: {TZ_NAME}")
        logger.info(f"   - Cleanup time: {cleanup_time}:00 {TZ_NAME}")
        logger.info(f"   - Notification time: {notification_time}:00 {TZ_NAME}")
        logger.info(f"   - Current time: {now_tz().strftime('%Y-%m-%d %H:%M:%S %Z')}")

        # Timezone e defaults seguros
        scheduler = AsyncIOScheduler(
            timezone=ZoneInfo(TZ_NAME),
            job_defaults={
                "coalesce": True,          # junta execuções atrasadas
                "max_instances": 1,        # evita concorrência
                "misfire_grace_time": 3600 # 60 min de tolerância
            }
        )
        logger.info("✅ AsyncIOScheduler instance created with robust defaults")

        # JOB DIÁRIO: REMOÇÃO
        scheduler.add_job(
            safe_cleanup_expired_subscriptions,   # usa a wrapper segura
            CronTrigger(
                hour=int(cleanup_time),
                minute=0,
                timezone=ZoneInfo(TZ_NAME),
            ),
            id="cleanup_expired",
            name="Cleanup Expired Subscriptions",
            replace_existing=True
        )
        logger.info(f"✅ Cleanup job added - will run daily at {cleanup_time}:00 {TZ_NAME}")

        # JOB DIÁRIO: NOTIFICAÇÕES
        if os.getenv("ENABLE_EXPIRY_NOTIFICATIONS", "1") == "1":
            scheduler.add_job(
                send_expiry_notifications,
                CronTrigger(
                    hour=int(notification_time),
                    minute=0,
                    timezone=ZoneInfo(TZ_NAME),
                ),
                id="send_notifications",
                name="Send Expiry Notifications",
                replace_existing=True
            )
            logger.info(f"✅ Notification job added - will run daily at {notification_time}:00 {TZ_NAME}")

        # HEARTBEAT a cada 1 min (se existir a função)
        try:
            scheduler.add_job(
                write_scheduler_heartbeat, "interval",
                minutes=1, id="scheduler_heartbeat", replace_existing=True
            )
            logger.info("✅ Heartbeat job added - will run every 1 minute")
        except Exception as e:
            logger.debug(f"Heartbeat job skipped: {e}")

        # CATCH-UP NA INICIALIZAÇÃO:
        # Se o horário diário (ex.: 01:00) já passou hoje e o job ainda não rodou após o boot,
        # agenda uma execução única e imediata, sem derrubar o serviço.
        try:
            now = now_tz()
            scheduled_today = now.replace(
                hour=int(cleanup_time), minute=0, second=0, microsecond=0
            )
            if now > scheduled_today:
                # Schedule immediate execution
                immediate_time = now + timedelta(seconds=30)
                scheduler.add_job(
                    safe_cleanup_expired_subscriptions,
                    trigger="date",
                    run_date=immediate_time,
                    id="cleanup_catchup_once",
                    replace_existing=True
                )
                logger.info(f"🚀 CATCH-UP: Agendado cleanup imediato para {immediate_time.strftime('%H:%M:%S')} (horário diário já passou)")
                
                # Also schedule a test run in 2 minutes to verify it's working
                test_time = now + timedelta(minutes=2)
                scheduler.add_job(
                    safe_cleanup_expired_subscriptions,
                    trigger="date", 
                    run_date=test_time,
                    id="cleanup_test_immediate",
                    replace_existing=True
                )
                logger.info(f"🧪 TEST: Agendado cleanup teste para {test_time.strftime('%H:%M:%S')} para verificar funcionamento")
        except Exception as e:
            logger.debug("catch-up desabilitado: %s", e)

        # Job de teste (opcional) - roda a cada 6 horas se habilitado
        if os.getenv("CLEANUP_TEST_MODE", "0") == "1":
            try:
                scheduler.add_job(
                    safe_cleanup_expired_subscriptions,
                    CronTrigger(hour="*/6", timezone=ZoneInfo(TZ_NAME)),  # A cada 6 horas
                    id='cleanup_test',
                    name='Cleanup Test Mode',
                    replace_existing=True
                )
                logger.info("🧪 Test mode job added - will run every 6 hours")
            except Exception as e:
                logger.error(f"❌ Failed to add test job: {e}")

        scheduler.start()
        logger.info("🚀 scheduler iniciado. TZ=%s | jobs=%s", TZ_NAME, [j.id for j in scheduler.get_jobs()])
        
        # Log all scheduled jobs with next run times
        jobs = scheduler.get_jobs()
        logger.info(f"📋 Active jobs ({len(jobs)}):")
        for job in jobs:
            next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z') if job.next_run_time else 'Never'
            logger.info(f"   - {job.name} (ID: {job.id}) - Next: {next_run}")
            
    except ImportError as e:
        logger.error(f"❌ APScheduler not available: {e}")
        logger.error("💡 Install with: pip install apscheduler")
    except Exception as e:
        logger.error(f"❌ Failed to start scheduler: {e}")
        logger.error(f"📋 Error details: {type(e).__name__}: {str(e)}")


def stop_scheduler():
    """Parar o scheduler de forma segura"""
    global scheduler
    try:
        if scheduler:
            scheduler.shutdown(wait=False)
            logger.info("⏰ Scheduler stopped safely")
    except Exception as e:
        logger.debug(f"shutdown do scheduler ignorou exceção: {e}")
    finally:
        scheduler = None


def _sched_snapshot():
    """Snapshot do estado do scheduler para diagnóstico"""
    snap = {
        "has_process_scheduler": bool(scheduler and getattr(scheduler, "running", False)),
        "tz": TZ_NAME, 
        "jobs": [],
        "current_time": now_tz().strftime('%Y-%m-%d %H:%M:%S %Z')
    }
    try:
        if scheduler:
            for j in scheduler.get_jobs():
                next_run_str = j.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z') if j.next_run_time else 'Never'
                snap["jobs"].append({
                    "id": j.id, 
                    "name": j.name,
                    "next": next_run_str,
                    "next_raw": j.next_run_time
                })
    except Exception as e:
        snap["error"] = str(e)
    return snap


# ======================
# 📊 Data Viewer & Export Routes
# ======================

@app.get("/admin/data", response_class=HTMLResponse)
async def admin_data_viewer(
    request: Request,
    show_subs: int = 10,
    show_invites: int = 10,
    show_events: int = 10
):
    """Página para visualizar dados do banco"""
    _require_admin(request)
    
    with SessionLocal() as db:
        # Subscriptions with "Show More" approach
        subs_total = db.query(models.Subscription).count()
        subscriptions = db.query(models.Subscription).order_by(models.Subscription.created_at.desc()).limit(show_subs).all()
        
        # Invite logs with "Show More" approach  
        invites_total = db.query(models.InviteLog).count()
        invite_logs = db.query(models.InviteLog).order_by(models.InviteLog.created_at.desc()).limit(show_invites).all()
        
        # Stripe events with "Show More" approach
        events_total = db.query(models.StripeEvent).count()
        stripe_events = db.query(models.StripeEvent).order_by(models.StripeEvent.received_at.desc()).limit(show_events).all()
    
    # Gerar HTML das tabelas
    subs_html = ""
    for s in subscriptions:
        subs_html += f"""
        <tr>
            <td>{s.id}</td>
            <td>{s.email}</td>
            <td>{s.full_name or 'N/A'}</td>
            <td>{s.plan_type or 'N/A'}</td>
            <td>{s.status or 'N/A'}</td>
            <td>{s.telegram_user_id or 'N/A'}</td>
            <td>{s.created_at.strftime('%d/%m/%Y %H:%M') if s.created_at else 'N/A'}</td>
            <td>{s.expires_at.strftime('%d/%m/%Y %H:%M') if s.expires_at else 'N/A'}</td>
        </tr>
        """
    
    invites_html = ""
    for inv in invite_logs:
        invites_html += f"""
        <tr>
            <td>{inv.id}</td>
            <td>{inv.email}</td>
            <td>{inv.telegram_user_id or 'N/A'}</td>
            <td>{'Sim' if inv.is_temporary else 'Não'}</td>
            <td>{inv.member_limit}</td>
            <td>{inv.created_at.strftime('%d/%m/%Y %H:%M') if inv.created_at else 'N/A'}</td>
            <td>{inv.expires_at.strftime('%d/%m/%Y %H:%M') if inv.expires_at else 'Permanente'}</td>
        </tr>
        """
    
    events_html = ""
    for evt in stripe_events:
        events_html += f"""
        <tr>
            <td>{evt.event_id}</td>
            <td>{evt.received_at.strftime('%d/%m/%Y %H:%M') if evt.received_at else 'N/A'}</td>
        </tr>
        """
    
    # "Show More" controls
    def create_show_more(current_count, total_count, category, current_params):
        if current_count >= total_count:
            return f'<p style="color: var(--text-dim); text-align: center; margin-top: 10px;">Showing all {total_count} records</p>'
        
        next_count = min(current_count + 20, total_count)
        return f"""
        <div style="text-align: center; margin-top: 15px;">
            <p style="color: var(--text-muted);">Showing {current_count} of {total_count} records</p>
            <a href="/admin/data?{category}={next_count}{current_params}" class="button" style="background: var(--color-info);">
                📄 Show More ({total_count - current_count} remaining)
            </a>
        </div>
        """
    
    # Create show more controls
    subs_show_more = create_show_more(show_subs, subs_total, "show_subs", f"&show_invites={show_invites}&show_events={show_events}")
    invites_show_more = create_show_more(show_invites, invites_total, "show_invites", f"&show_subs={show_subs}&show_events={show_events}")
    events_show_more = create_show_more(show_events, events_total, "show_events", f"&show_subs={show_subs}&show_invites={show_invites}")

    body = f"""
    <div style="margin-bottom: 20px;">
        <h1>📊 Visualizador de Dados</h1>
        <div class="nav-buttons" style="margin: 10px 0;">
            <a href="/admin/subscriptions" style="margin-right: 10px;">← Voltar Admin</a>
            <a href="/admin/export/subscriptions" style="margin-right: 10px;">📥 Exportar Assinaturas</a>
            <a href="/admin/export/invites" style="margin-right: 10px;">📥 Exportar Convites</a>
            <a href="/admin/export/all" style="margin-right: 10px;">📥 Exportar Tudo</a>
            <a href="/admin/cleanup/logs" style="margin-right: 10px; background: #f59e0b;" onclick="return confirm('This will permanently delete old logs. Continue?');">🧹 Cleanup Logs</a>
            <a href="/admin/logout">🚪 Logout</a>
        </div>
    </div>
    
    <h2>💳 Assinaturas (Mostrando {len(subscriptions)} de {subs_total})</h2>
    <div style="overflow-x: auto;">
        <table class="debug-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Email</th>
                    <th>Nome</th>
                    <th>Plano</th>
                    <th>Status</th>
                    <th>Telegram ID</th>
                    <th>Criado em</th>
                    <th>Expira em</th>
                </tr>
            </thead>
            <tbody>
                {subs_html}
            </tbody>
        </table>
    </div>
    {subs_show_more}
    
    <h2>🔗 Logs de Convites (Mostrando {len(invite_logs)} de {invites_total})</h2>
    <div style="overflow-x: auto;">
        <table class="debug-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Email</th>
                    <th>Telegram ID</th>
                    <th>Temporário</th>
                    <th>Limite</th>
                    <th>Criado em</th>
                    <th>Expira em</th>
                </tr>
            </thead>
            <tbody>
                {invites_html}
            </tbody>
        </table>
    </div>
    {invites_show_more}
    
    <h2>⚡ Eventos Stripe (Mostrando {len(stripe_events)} de {events_total})</h2>
    <div style="overflow-x: auto;">
        <table class="debug-table">
            <thead>
                <tr>
                    <th>Event ID</th>
                    <th>Recebido em</th>
                </tr>
            </thead>
            <tbody>
                {events_html}
            </tbody>
        </table>
    </div>
    {events_show_more}
    """
    
    return HTMLResponse(_html_page("Data Viewer", body))


@app.get("/admin/export/subscriptions")
async def export_subscriptions(request: Request):
    """Exportar assinaturas para CSV"""
    _require_admin(request)
    
    import csv
    import io
    from fastapi.responses import StreamingResponse
    
    with SessionLocal() as db:
        subscriptions = db.query(models.Subscription).order_by(models.Subscription.created_at.desc()).all()
    
    # Criar CSV em memória
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Cabeçalho
    writer.writerow([
        'ID', 'Email', 'Nome Completo', 'Telegram ID', 'Stripe Customer ID',
        'Stripe Session ID', 'Stripe Subscription ID', 'Status', 'Tipo de Plano',
        'Criado em', 'Atualizado em', 'Expira em'
    ])
    
    # Dados
    for s in subscriptions:
        writer.writerow([
            s.id,
            s.email,
            s.full_name or '',
            s.telegram_user_id or '',
            s.stripe_customer_id or '',
            s.stripe_session_id or '',
            s.stripe_subscription_id or '',
            s.status or '',
            s.plan_type or '',
            s.created_at.strftime('%d/%m/%Y %H:%M:%S') if s.created_at else '',
            s.updated_at.strftime('%d/%m/%Y %H:%M:%S') if s.updated_at else '',
            s.expires_at.strftime('%d/%m/%Y %H:%M:%S') if s.expires_at else ''
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename=subscriptions.csv'}
    )


@app.get("/admin/export/invites")
async def export_invites(request: Request):
    """Exportar logs de convites para CSV"""
    _require_admin(request)
    
    import csv
    import io
    from fastapi.responses import StreamingResponse
    
    with SessionLocal() as db:
        invite_logs = db.query(models.InviteLog).order_by(models.InviteLog.created_at.desc()).all()
    
    # Criar CSV em memória
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Cabeçalho
    writer.writerow([
        'ID', 'Email', 'Telegram ID', 'Link de Convite', 'Limite de Membros',
        'É Temporário', 'Criado em', 'Expira em'
    ])
    
    # Dados
    for inv in invite_logs:
        writer.writerow([
            inv.id,
            inv.email,
            inv.telegram_user_id or '',
            inv.invite_link,
            inv.member_limit,
            'Sim' if inv.is_temporary else 'Não',
            inv.created_at.strftime('%d/%m/%Y %H:%M:%S') if inv.created_at else '',
            inv.expires_at.strftime('%d/%m/%Y %H:%M:%S') if inv.expires_at else ''
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename=invite_logs.csv'}
    )


@app.get("/admin/export/all")
async def export_all_data(request: Request):
    """Exportar todos os dados para CSV compactado"""
    _require_admin(request)
    
    import csv
    import io
    import zipfile
    from fastapi.responses import StreamingResponse
    
    with SessionLocal() as db:
        subscriptions = db.query(models.Subscription).order_by(models.Subscription.created_at.desc()).all()
        invite_logs = db.query(models.InviteLog).order_by(models.InviteLog.created_at.desc()).all()
        stripe_events = db.query(models.StripeEvent).order_by(models.StripeEvent.received_at.desc()).all()
    
    # Criar arquivo ZIP em memória
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # 1. Assinaturas
        subs_output = io.StringIO()
        subs_writer = csv.writer(subs_output)
        subs_writer.writerow([
            'ID', 'Email', 'Nome Completo', 'Telegram ID', 'Stripe Customer ID',
            'Stripe Session ID', 'Stripe Subscription ID', 'Status', 'Tipo de Plano',
            'Criado em', 'Atualizado em', 'Expira em'
        ])
        for s in subscriptions:
            subs_writer.writerow([
                s.id, s.email, s.full_name or '', s.telegram_user_id or '',
                s.stripe_customer_id or '', s.stripe_session_id or '',
                s.stripe_subscription_id or '', s.status or '', s.plan_type or '',
                s.created_at.strftime('%d/%m/%Y %H:%M:%S') if s.created_at else '',
                s.updated_at.strftime('%d/%m/%Y %H:%M:%S') if s.updated_at else '',
                s.expires_at.strftime('%d/%m/%Y %H:%M:%S') if s.expires_at else ''
            ])
        zip_file.writestr('subscriptions.csv', subs_output.getvalue())
        
        # 2. Logs de convites
        invites_output = io.StringIO()
        invites_writer = csv.writer(invites_output)
        invites_writer.writerow([
            'ID', 'Email', 'Telegram ID', 'Link de Convite', 'Limite de Membros',
            'É Temporário', 'Criado em', 'Expira em'
        ])
        for inv in invite_logs:
            invites_writer.writerow([
                inv.id, inv.email, inv.telegram_user_id or '', inv.invite_link,
                inv.member_limit, 'Sim' if inv.is_temporary else 'Não',
                inv.created_at.strftime('%d/%m/%Y %H:%M:%S') if inv.created_at else '',
                inv.expires_at.strftime('%d/%m/%Y %H:%M:%S') if inv.expires_at else ''
            ])
        zip_file.writestr('invite_logs.csv', invites_output.getvalue())
        
        # 3. Eventos Stripe
        events_output = io.StringIO()
        events_writer = csv.writer(events_output)
        events_writer.writerow(['Event ID', 'Recebido em'])
        for evt in stripe_events:
            events_writer.writerow([
                evt.event_id,
                evt.received_at.strftime('%d/%m/%Y %H:%M:%S') if evt.received_at else ''
            ])
        zip_file.writestr('stripe_events.csv', events_output.getvalue())
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        io.BytesIO(zip_buffer.getvalue()),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename=database_export.zip'}
    )


@app.get("/admin/groups")
async def admin_groups_management(request: Request):
    """Página de gerenciamento dos grupos VIP"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        
        if not application or not application.bot:
            return HTMLResponse(_html_page(
                "Groups Management Error",
                """
                <div class="alert-error" style="padding: 20px; border-radius: 8px;">
                    <h1>❌ Bot Not Available</h1>
                    <p>Bot application is not available to check group information.</p>
                    <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                </div>
                """
            ))
        
        bot = application.bot
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        
        groups_info = []
        total_members = 0
        
        for group_id in VIP_GROUP_IDS:
            try:
                # Get group info
                chat = await bot.get_chat(group_id)
                group_name = chat.title or f"Group {group_id}"
                
                # Get member count
                try:
                    member_count = await bot.get_chat_member_count(group_id)
                    total_members += member_count
                except Exception as e:
                    member_count = "Unknown"
                    logger.warning(f"Could not get member count for {group_id}: {e}")
                
                # Check bot permissions
                try:
                    bot_member = await bot.get_chat_member(group_id, bot.id)
                    bot_status = "✅ Admin" if bot_member.status in ["administrator", "creator"] else "❌ Not Admin"
                    
                    # Check specific permissions
                    permissions = []
                    if hasattr(bot_member, 'can_restrict_members') and bot_member.can_restrict_members:
                        permissions.append("Ban users")
                    if hasattr(bot_member, 'can_invite_users') and bot_member.can_invite_users:
                        permissions.append("Invite users")
                    
                    permissions_text = ", ".join(permissions) if permissions else "Limited permissions"
                    
                except Exception as e:
                    bot_status = "❌ Error"
                    permissions_text = f"Error: {str(e)}"
                    logger.error(f"Could not check bot permissions in {group_id}: {e}")
                
                groups_info.append({
                    'group_id': group_id,
                    'group_name': group_name,
                    'member_count': member_count,
                    'bot_status': bot_status,
                    'permissions': permissions_text
                })
                
            except Exception as e:
                logger.error(f"Error getting info for group {group_id}: {e}")
                groups_info.append({
                    'group_id': group_id,
                    'group_name': f"Group {group_id}",
                    'member_count': "Error",
                    'bot_status': "❌ Error",
                    'permissions': f"Error: {str(e)}"
                })
        
        # Generate HTML for groups table
        groups_html = ""
        for group in groups_info:
            status_color = "#10b981" if "✅" in group['bot_status'] else "#ef4444"
            
            groups_html += f"""
            <tr>
                <td>{group['group_id']}</td>
                <td>{group['group_name']}</td>
                <td style="color: {status_color}; font-weight: bold;">{group['bot_status']}</td>
                <td style="font-weight: bold;">({group['member_count']})</td>
                <td>{group['permissions']}</td>
                <td>
                    <a href="https://t.me/c/{str(group['group_id']).replace('-100', '')}" 
                       target="_blank" class="button" style="font-size: 0.8em;">
                       🔗 Open Group
                    </a>
                </td>
            </tr>
            """
        
        try:
            from template_engine import render_template
            return HTMLResponse(render_template(
                "admin_groups",
                title="VIP Groups Management",
                current_time=current_time,
                bot_status="🟢 Online" if application.bot else "🔴 Offline",
                total_groups=len(VIP_GROUP_IDS),
                groups_html=groups_html or '<tr><td colspan="6" class="muted">No VIP groups configured</td></tr>'
            ))
        except Exception:
            # Fallback to inline HTML
            return HTMLResponse(_html_page(
                "VIP Groups Management",
                f"""
                <div style="margin-bottom: 20px;">
                    <h1>🏘️ VIP Groups Management</h1>
                    <div class="nav-buttons" style="margin: 10px 0;">
                        <a href="/admin/subscriptions" style="margin-right: 10px;">← Admin Panel</a>
                        <a href="/admin/data" style="margin-right: 10px;">📊 Data Viewer</a>
                        <a href="/admin/removal" style="margin-right: 10px;">🚫 Auto Removal</a>
                        <a href="/admin/logout">🚪 Logout</a>
                    </div>
                </div>
                
                <div class="alert-info" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <h2>📊 VIP Groups Overview</h2>
                    <p><strong>🕐 Last Updated:</strong> {current_time}</p>
                    <p><strong>🤖 Bot Status:</strong> {'🟢 Online' if application.bot else '🔴 Offline'}</p>
                    <p><strong>📋 Total VIP Groups:</strong> {len(VIP_GROUP_IDS)}</p>
                    <p><strong>👥 Total Members:</strong> ({total_members})</p>
                </div>
                
                <h2>🏘️ VIP Groups List</h2>
                <div style="overflow-x: auto;">
                    <table class="debug-table">
                        <thead>
                            <tr>
                                <th>Group ID</th>
                                <th>Group Name</th>
                                <th>Bot Status</th>
                                <th>Member Count</th>
                                <th>Bot Permissions</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groups_html or '<tr><td colspan="6" class="muted">No VIP groups configured</td></tr>'}
                        </tbody>
                    </table>
                </div>
                
                <div class="alert-warning" style="padding: 15px; border-radius: 8px; margin-top: 20px;">
                    <h3>ℹ️ Important Notes</h3>
                    <ul style="line-height: 1.6;">
                        <li><strong>Bot Admin Required:</strong> Bot must be admin in groups to remove users</li>
                        <li><strong>Member Count:</strong> Includes all members (bots, admins, regular users)</li>
                        <li><strong>Permissions:</strong> Bot needs "Ban users" permission for auto-removal</li>
                        <li><strong>Group IDs:</strong> Configured in VIP_GROUP_IDS environment variable</li>
                    </ul>
                </div>
                
                <div class="nav-buttons" style="margin-top: 20px;">
                    <a href="/admin/subscriptions" class="button">← Back to Admin</a>
                    <a href="/admin/removal" class="button">🚫 Auto Removal Dashboard</a>
                </div>
                """
            ))
        
    except Exception as e:
        logger.error(f"Groups management page failed: {e}")
        return HTMLResponse(_html_page(
            "Groups Management Error",
            f"""
            <div class="alert-error" style="padding: 20px; border-radius: 8px;">
                <h1>❌ Groups Management Error</h1>
                <p><strong>Error:</strong> {str(e)}</p>
                <p><a href="/admin/subscriptions">← Back to Admin</a></p>
            </div>
            """
        ))


@app.get("/admin/cleanup/logs")
async def admin_cleanup_logs(request: Request):
    """Limpar logs antigos do banco de dados"""
    _require_admin(request)
    
    try:
        from crud import (
            cleanup_old_stripe_events,
            cleanup_old_invite_logs, 
            cleanup_old_removal_logs,
            cleanup_old_notification_logs,
            get_database_stats
        )
        
        with SessionLocal() as db:
            # Get stats before cleanup
            stats_before = get_database_stats(db)
            
            # Perform cleanup
            results = {
                'stripe_events': cleanup_old_stripe_events(db, 30),
                'invite_logs': cleanup_old_invite_logs(db, 7),
                'removal_logs': cleanup_old_removal_logs(db, 30),
                'notification_logs': cleanup_old_notification_logs(db, 30)
            }
            
            # Get stats after cleanup
            stats_after = get_database_stats(db)
            
            total_cleaned = sum(count for count in results.values() if isinstance(count, int))
        
        return HTMLResponse(_html_page(
            "Database Cleanup Results",
            f"""
            <div class="alert-success" style="padding: 20px; margin-bottom: 20px; border-radius: 8px;">
                <h1>🧹 Database Cleanup Completed</h1>
                <p><strong>Total Records Cleaned:</strong> {total_cleaned}</p>
            </div>
            
            <h2>📊 Cleanup Details</h2>
            <div class="alert-info" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <p><strong>🔄 Stripe Events:</strong> {results['stripe_events']} records deleted (older than 30 days)</p>
                <p><strong>🔗 Invite Logs:</strong> {results['invite_logs']} records deleted (older than 7 days)</p>
                <p><strong>🚫 Removal Logs:</strong> {results['removal_logs']} records deleted (older than 30 days)</p>
                <p><strong>📱 Notification Logs:</strong> {results['notification_logs']} records deleted (older than 30 days)</p>
            </div>
            
            <h2>📈 Database Statistics</h2>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                <div class="alert-info" style="padding: 15px; border-radius: 8px;">
                    <h3>📊 Before Cleanup</h3>
                    <ul style="margin: 10px 0;">
                        <li><strong>Subscriptions:</strong> {stats_before.get('subscriptions', 'N/A')}</li>
                        <li><strong>Stripe Events:</strong> {stats_before.get('stripe_events', 'N/A')}</li>
                        <li><strong>Invite Logs:</strong> {stats_before.get('invite_logs', 'N/A')}</li>
                        <li><strong>Removal Logs:</strong> {stats_before.get('removal_logs', 'N/A')}</li>
                        <li><strong>Notification Logs:</strong> {stats_before.get('notification_logs', 'N/A')}</li>
                        <li><strong>Whitelist:</strong> {stats_before.get('whitelist', 'N/A')}</li>
                    </ul>
                </div>
                <div class="alert-success" style="padding: 15px; border-radius: 8px;">
                    <h3>📊 After Cleanup</h3>
                    <ul style="margin: 10px 0;">
                        <li><strong>Subscriptions:</strong> {stats_after.get('subscriptions', 'N/A')} (unchanged)</li>
                        <li><strong>Stripe Events:</strong> {stats_after.get('stripe_events', 'N/A')}</li>
                        <li><strong>Invite Logs:</strong> {stats_after.get('invite_logs', 'N/A')}</li>
                        <li><strong>Removal Logs:</strong> {stats_after.get('removal_logs', 'N/A')}</li>
                        <li><strong>Notification Logs:</strong> {stats_after.get('notification_logs', 'N/A')}</li>
                        <li><strong>Whitelist:</strong> {stats_after.get('whitelist', 'N/A')} (unchanged)</li>
                    </ul>
                </div>
            </div>
            
            <div class="alert-warning" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h3>ℹ️ What Was Cleaned</h3>
                <ul>
                    <li><strong>Stripe Events:</strong> Older than 30 days (prevents duplicate webhook processing)</li>
                    <li><strong>Invite Logs:</strong> Older than 7 days (temporary links expire anyway)</li>
                    <li><strong>Removal/Notification Logs:</strong> Older than 30 days (keep recent for monitoring)</li>
                </ul>
                <p><strong>⚠️ Important:</strong> Subscription and Whitelist data is NEVER deleted - these are critical business data.</p>
            </div>
            
            <div class="nav-buttons" style="margin-top: 20px;">
                <a href="/admin/data" class="button">← Back to Data Viewer</a>
                <a href="/admin/subscriptions" class="button">← Admin Panel</a>
            </div>
            """
        ))
        
    except Exception as e:
        logger.error(f"Database cleanup failed: {e}")
        return HTMLResponse(_html_page(
            "Cleanup Failed",
            f"""
            <div class="alert-error" style="padding: 20px; border-radius: 8px; margin-bottom: 20px;">
                <h1>❌ Database Cleanup Failed</h1>
                <p><strong>Error:</strong> {str(e)}</p>
            </div>
            
            <div class="nav-buttons">
                <a href="/admin/data" class="button">← Back to Data Viewer</a>
            </div>
            """
        ))


# ======================
# 🚫 Auto Removal Admin Routes
# ======================

@app.get("/admin/removal", response_class=HTMLResponse)
async def admin_removal_dashboard(request: Request):
    """Dashboard de remoções automáticas"""
    _require_admin(request)
    
    try:
        # Ensure tables exist
        if DATABASE_AVAILABLE:
            try:
                init_db()  # This will create missing tables
            except Exception as e:
                logger.warning(f"Could not initialize new tables: {e}")
        
        with SessionLocal() as db:
            from crud import get_recent_removal_logs
            
            # Buscar logs recentes (com fallback se tabela não existir)
            try:
                removal_logs = get_recent_removal_logs(db, 50)
            except Exception as e:
                logger.warning(f"Could not fetch removal logs: {e}")
                removal_logs = []
            
            # Estatísticas
            total_logs = len(removal_logs)
            success_logs = len([log for log in removal_logs if log.status == "success"])
            error_logs = len([log for log in removal_logs if log.status in ["failed", "error"]])
            whitelisted_logs = len([log for log in removal_logs if log.status == "whitelisted"])
            
            # Buscar whitelist (com fallback se tabela não existir)
            try:
                whitelist_entries = db.query(models.Whitelist).order_by(models.Whitelist.created_at.desc()).all()
            except Exception as e:
                logger.warning(f"Could not fetch whitelist: {e}")
                whitelist_entries = []
            
            # Buscar usuários próximos da expulsão
            try:
                from crud import get_expired_subscriptions, get_subscriptions_past_grace_period, get_subscriptions_in_grace_period, is_whitelisted
                
                # Usuários já expirados (past grace period) - serão expulsos na próxima execução
                expired_users = get_subscriptions_past_grace_period(db)
                
                # Usuários no período de graça (expirados mas ainda dentro do grace period)
                grace_users = get_subscriptions_in_grace_period(db)
                
                # Filtrar usuários não whitelistados
                expired_to_remove = []
                grace_period_users = []
                
                for user in expired_users:
                    if not is_whitelisted(db, user.telegram_user_id):
                        expired_to_remove.append(user)
                
                for user in grace_users:
                    if not is_whitelisted(db, user.telegram_user_id):
                        grace_period_users.append(user)
                        
            except Exception as e:
                logger.warning(f"Could not fetch upcoming removals: {e}")
                expired_to_remove = []
                grace_period_users = []
    
    except Exception as e:
        logger.error(f"Error in admin_removal_dashboard: {e}")
        return HTMLResponse(_html_page(
            "Auto Removal Error",
            f"""
            <h1>❌ Auto Removal System Error</h1>
            <p>There was an error loading the auto removal dashboard.</p>
            <p><strong>Error:</strong> {str(e)}</p>
            <p>This might be because the new database tables haven't been created yet.</p>
            <h2>🔧 Solution:</h2>
            <p>The system will automatically create the required tables on the next restart.</p>
            <p><a href="/admin/subscriptions">← Back to Admin</a></p>
            """
        ))
    
    # Gerar HTML dos logs
    logs_html = ""
    for log in removal_logs:
        status_color = {
            'success': '#22c55e',
            'failed': '#ef4444', 
            'error': '#ef4444',
            'whitelisted': '#f59e0b',
            'no_telegram_id': '#6b7280',
            'processing': '#3b82f6'
        }.get(log.status, '#6b7280')
        
        logs_html += f"""
        <tr>
            <td>{log.id}</td>
            <td>{log.email}</td>
            <td>{log.telegram_user_id or 'N/A'}</td>
            <td>{log.reason}</td>
            <td style="color: {status_color}; font-weight: bold;">{log.status}</td>
            <td>{'✅' if log.dm_sent else '❌'}</td>
            <td>{log.created_at.strftime('%d/%m/%Y %H:%M') if log.created_at else 'N/A'}</td>
            <td>{log.error_message or ''}</td>
        </tr>
        """
    
    # Gerar HTML dos usuários próximos da expulsão
    upcoming_removals_html = ""
    
    if expired_to_remove or grace_period_users:
        # Usuários que serão expulsos na próxima execução
        if expired_to_remove:
            upcoming_removals_html += f"""
            <div class="alert-error" style="margin-bottom: 20px; padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444;">
                <h3>🚨 Serão Expulsos na Próxima Execução ({len(expired_to_remove)} usuários)</h3>
                <p>Estes usuários já passaram do período de graça e serão removidos automaticamente:</p>
                <div style="overflow-x: auto; margin-top: 10px;">
                    <table style="width: 100%; background: rgba(0,0,0,0.3); border-radius: 4px;">
                        <thead>
                            <tr style="background: rgba(239,68,68,0.2);">
                                <th style="padding: 8px; color: #fee2e2;">Nome</th>
                                <th style="padding: 8px; color: #fee2e2;">Email</th>
                                <th style="padding: 8px; color: #fee2e2;">Telegram ID</th>
                                <th style="padding: 8px; color: #fee2e2;">Expirou em</th>
                                <th style="padding: 8px; color: #fee2e2;">Dias Expirado</th>
                                <th style="padding: 8px; color: #fee2e2;">Ação</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            
            for user in expired_to_remove:
                days_expired = (now_tz().date() - user.expires_at.date()).days if user.expires_at else 0
                upcoming_removals_html += f"""
                            <tr>
                                <td style="padding: 8px; color: #fecaca;">{user.full_name or 'N/A'}</td>
                                <td style="padding: 8px; color: #fecaca;">{user.email}</td>
                                <td style="padding: 8px; color: #fecaca;">{user.telegram_user_id or 'N/A'}</td>
                                <td style="padding: 8px; color: #fecaca;">{user.expires_at.strftime('%d/%m/%Y') if user.expires_at else 'N/A'}</td>
                                <td style="padding: 8px; color: #ef4444; font-weight: bold;">{days_expired} dias</td>
                                <td style="padding: 8px;">
                                    <a href="/admin/subscriptions/{user.id}/expulsar" class="button" style="background: #dc2626; color: white; padding: 4px 8px; font-size: 12px;" onclick="return confirm('Expulsar {user.email} agora?')">Expulsar Agora</a>
                                </td>
                            </tr>
                """
            
            upcoming_removals_html += """
                        </tbody>
                    </table>
                </div>
            </div>
            """
        
        # Usuários no período de graça
        if grace_period_users:
            upcoming_removals_html += f"""
            <div class="alert-warning" style="margin-bottom: 20px; padding: 15px; border-radius: 8px; border-left: 4px solid #f59e0b;">
                <h3>⚠️ No Período de Graça ({len(grace_period_users)} usuários)</h3>
                <p>Estes usuários expiraram recentemente mas ainda estão no período de graça de {os.getenv('GRACE_PERIOD_DAYS', '3')} dias:</p>
                <div style="overflow-x: auto; margin-top: 10px;">
                    <table style="width: 100%; background: rgba(0,0,0,0.3); border-radius: 4px;">
                        <thead>
                            <tr style="background: rgba(245,158,11,0.2);">
                                <th style="padding: 8px; color: #fef3c7;">Nome</th>
                                <th style="padding: 8px; color: #fef3c7;">Email</th>
                                <th style="padding: 8px; color: #fef3c7;">Telegram ID</th>
                                <th style="padding: 8px; color: #fef3c7;">Expirou em</th>
                                <th style="padding: 8px; color: #fef3c7;">Dias Restantes</th>
                                <th style="padding: 8px; color: #fef3c7;">Status</th>
                            </tr>
                        </thead>
                        <tbody>
            """
            
            grace_period_days = int(os.getenv('GRACE_PERIOD_DAYS', '3'))
            
            for user in grace_period_users:
                days_expired = (now_tz().date() - user.expires_at.date()).days if user.expires_at else 0
                days_remaining = grace_period_days - days_expired
                
                upcoming_removals_html += f"""
                            <tr>
                                <td style="padding: 8px; color: #fcd34d;">{user.full_name or 'N/A'}</td>
                                <td style="padding: 8px; color: #fcd34d;">{user.email}</td>
                                <td style="padding: 8px; color: #fcd34d;">{user.telegram_user_id or 'N/A'}</td>
                                <td style="padding: 8px; color: #fcd34d;">{user.expires_at.strftime('%d/%m/%Y') if user.expires_at else 'N/A'}</td>
                                <td style="padding: 8px; color: #f59e0b; font-weight: bold;">{days_remaining} dias</td>
                                <td style="padding: 8px; color: #fbbf24;">Período de Graça</td>
                            </tr>
                """
            
            upcoming_removals_html += """
                        </tbody>
                    </table>
                </div>
            </div>
            """
    else:
        upcoming_removals_html = """
        <div class="alert-success" style="padding: 15px; border-radius: 8px; border-left: 4px solid #10b981;">
            <h3>✅ Nenhum Usuário Pendente para Expulsão</h3>
            <p>Todas as assinaturas estão ativas ou os usuários expirados já foram processados.</p>
        </div>
        """

    # Gerar HTML da whitelist
    whitelist_html = ""
    for entry in whitelist_entries:
        whitelist_html += f"""
        <tr>
            <td>{entry.id}</td>
            <td style="font-weight: bold;">{entry.telegram_user_id}</td>
            <td>{entry.email or 'N/A'}</td>
            <td>{entry.reason}</td>
            <td>{entry.added_by or 'N/A'}</td>
            <td>{entry.created_at.strftime('%d/%m/%Y %H:%M') if entry.created_at else 'N/A'}</td>
            <td>
                <form method="post" action="/admin/whitelist/remove" style="display: inline;">
                    <input type="hidden" name="telegram_user_id" value="{entry.telegram_user_id}">
                    <input type="submit" value="Remove" class="danger" onclick="return confirm('Remove Telegram ID {entry.telegram_user_id} from whitelist?');">
                </form>
            </td>
        </tr>
        """
    
    # Get robust scheduler snapshot
    sched_snap = _sched_snapshot()
    scheduler_status = "🟢 Running" if sched_snap["has_process_scheduler"] else "🔴 Stopped"
    cleanup_time = os.getenv("CLEANUP_TIME", "2")
    test_mode = "🧪 Enabled" if os.getenv("CLEANUP_TEST_MODE", "0") == "1" else "❌ Disabled"
    
    # Use robust timezone-aware time
    server_time_str = sched_snap["current_time"]
    
    # Calculate next cleanup time from jobs
    next_cleanup_str = "Unknown"
    hours_until_cleanup = 0
    
    try:
        cleanup_job = next((job for job in sched_snap["jobs"] if job["id"] == "cleanup_expired"), None)
        if cleanup_job and cleanup_job["next_raw"]:
            next_cleanup_str = cleanup_job["next"]
            current_time = now_tz()
            hours_until_cleanup = int((cleanup_job["next_raw"] - current_time).total_seconds() / 3600)
        else:
            # Fallback calculation
            current_time = now_tz()
            next_cleanup = current_time.replace(hour=int(cleanup_time), minute=0, second=0, microsecond=0)
            if current_time.hour >= int(cleanup_time):
                next_cleanup = next_cleanup + timedelta(days=1)
            next_cleanup_str = next_cleanup.strftime('%Y-%m-%d %H:%M %Z')
            hours_until_cleanup = int((next_cleanup - current_time).total_seconds() / 3600)
    except Exception as e:
        logger.debug(f"Error calculating next cleanup: {e}")
    
    body = f"""
    <div style="margin-bottom: 20px;">
        <h1>🚫 Auto Removal Dashboard</h1>
        <div style="margin: 10px 0;">
            <a href="/admin/data" style="margin-right: 10px;">← Data Viewer</a>
            <a href="/admin/subscriptions" style="margin-right: 10px;">← Admin</a>
            <a href="/admin/removal/test-notifications" style="margin-right: 10px;">📱 Test Notifications</a>
            <a href="/admin/removal/diagnose" style="margin-right: 10px;">🔍 System Diagnosis</a>
            <a href="/admin/debug/database" style="margin-right: 10px;">🔧 Database Debug</a>
            <a href="/admin/removal/debug-specific" style="margin-right: 10px;">🎯 Debug Active Users</a>
            <a href="/admin/logout">🚪 Logout</a>
        </div>
    </div>
    
    <h2>🕐 Server Time & Schedule</h2>
    <div class="alert-info" style="padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #0284c7;">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div>
                <p><strong>🕐 Current Server Time:</strong></p>
                <p id="server-time" style="font-size: 1.2em; font-weight: 700; color: #1e40af;">{server_time_str}</p>
            </div>
            <div>
                <p><strong>⏰ Next Automatic Cleanup:</strong></p>
                <p class="schedule-info" style="font-size: 1.1em; font-weight: 600; color: #059669;">{next_cleanup_str}</p>
                <p class="schedule-info" style="font-size: 0.9em;">({hours_until_cleanup} hours from now)</p>
            </div>
        </div>
    </div>
    
    <h2>🎛️ Manual Controls</h2>
    <div class="alert-warning manual-controls" style="padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #f59e0b;">
        <div style="display: flex; gap: 15px; align-items: center;">
            <div style="flex: 1;">
                <h3 style="margin: 0 0 5px 0; color: #92400e;">⚡ Run Cleanup Now</h3>
                <p style="margin: 0; color: #78350f; font-size: 0.9em; font-weight: 500;">Execute the cleanup process immediately (removes expired users)</p>
            </div>
            <div>
                <a href="/admin/removal/execute-now" 
                   style="background: #dc2626; color: white; padding: 10px 20px; font-weight: bold; border-radius: 6px; text-decoration: none; display: inline-block;" 
                   onclick="return confirm('This will immediately remove all expired users from VIP groups and send renewal DMs. Continue?');">
                   🚫 Run Cleanup Now
                </a>
            </div>
        </div>
    </div>
    
    <h2>⚙️ System Status</h2>
    <div class="status-section" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
        <p><strong>Scheduler:</strong> {scheduler_status}</p>
        <p><strong>🌍 Timezone:</strong> {sched_snap["tz"]}</p>
        <p><strong>🚫 Cleanup Time:</strong> Daily at {cleanup_time}:00 {sched_snap["tz"]}</p>
        <p><strong>📱 Notification Time:</strong> Daily at {os.getenv('NOTIFICATION_TIME', '10')}:00 {sched_snap["tz"]}</p>
        <p><strong>⏰ Grace Period:</strong> {os.getenv('GRACE_PERIOD_DAYS', '3')} days</p>
        <p><strong>📱 Expiry Notifications:</strong> {'🟢 Enabled' if os.getenv('ENABLE_EXPIRY_NOTIFICATIONS', '1') == '1' else '🔴 Disabled'}</p>
        <p><strong>🧪 Test Mode:</strong> {test_mode}</p>
        <p><strong>💾 Database:</strong> {'🟢 Available' if DATABASE_AVAILABLE else '🔴 Unavailable'}</p>
    </div>
    
    <h2>📋 Scheduled Jobs</h2>
    <div style="background: rgb(8, 10, 13); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
        {"".join([f'<p><strong>{job["name"]}:</strong> {job["next"]}</p>' for job in sched_snap["jobs"]]) if sched_snap["jobs"] else '<p>❌ No jobs scheduled</p>'}
    </div>
    
    <script>
        // Update server time every second
        function updateServerTime() {{
            const now = new Date();
            const utcTime = now.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
            document.getElementById('server-time').textContent = utcTime;
        }}
        
        // Update immediately and then every second
        updateServerTime();
        setInterval(updateServerTime, 1000);
    </script>
    
    <h2>🚨 Próximos Usuários para Expulsão</h2>
    <div style="margin-bottom: 30px;">
        {upcoming_removals_html}
    </div>

    <h2>📊 Statistics</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 20px;">
        <div class="stats-card" style="background: #22c55e; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <h3 style="font-weight: 800; text-shadow: 0 1px 3px rgba(0,0,0,0.2);">{success_logs}</h3>
            <p style="font-weight: 600; opacity: 0.95;">Successful</p>
        </div>
        <div class="stats-card" style="background: #ef4444; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <h3 style="font-weight: 800; text-shadow: 0 1px 3px rgba(0,0,0,0.2);">{error_logs}</h3>
            <p style="font-weight: 600; opacity: 0.95;">Errors</p>
        </div>
        <div class="stats-card" style="background: #f59e0b; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <h3 style="font-weight: 800; text-shadow: 0 1px 3px rgba(0,0,0,0.2);">{whitelisted_logs}</h3>
            <p style="font-weight: 600; opacity: 0.95;">Whitelisted</p>
        </div>
        <div class="stats-card" style="background: #6b7280; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <h3 style="font-weight: 800; text-shadow: 0 1px 3px rgba(0,0,0,0.2);">{total_logs}</h3>
            <p style="font-weight: 600; opacity: 0.95;">Total Logs</p>
        </div>
    </div>
    
    <h2>🔒 Whitelist Management</h2>
    <form method="post" action="/admin/whitelist/add" class="alert-info" style="margin-bottom: 20px; padding: 15px; border-radius: 8px;">
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr auto; gap: 10px; align-items: end;">
            <div>
                <label>Telegram ID (Required):</label>
                <input type="text" name="telegram_user_id" placeholder="694383532" required>
            </div>
            <div>
                <label>Email (Optional):</label>
                <input type="email" name="email" placeholder="user@example.com">
            </div>
            <div>
                <label>Reason:</label>
                <input type="text" name="reason" placeholder="e.g., VIP member, staff" required>
            </div>
            <div>
                <label>Added by:</label>
                <input type="text" name="added_by" value="admin" required>
            </div>
            <div>
                <input type="submit" value="Add to Whitelist">
            </div>
        </div>
    </form>
    
    <div class="whitelist-section" style="overflow-x: auto;">
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Telegram ID</th>
                    <th>Email</th>
                    <th>Reason</th>
                    <th>Added by</th>
                    <th>Created</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {whitelist_html or '<tr><td colspan="7" class="muted">No whitelist entries</td></tr>'}
            </tbody>
        </table>
    </div>
    
    <h2>📋 Removal Logs ({total_logs} recent)</h2>
    <div class="logs-section" style="overflow-x: auto;">
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Email</th>
                    <th>Telegram ID</th>
                    <th>Reason</th>
                    <th>Status</th>
                    <th>DM Sent</th>
                    <th>Date</th>
                    <th>Error</th>
                </tr>
            </thead>
            <tbody>
                {logs_html or '<tr><td colspan="8" class="muted">No removal logs</td></tr>'}
            </tbody>
        </table>
        
        <div style="text-align: center; margin-top: 15px; padding: 15px; background: rgba(107, 114, 128, 0.1); border-radius: 8px;">
            <p style="color: var(--text-muted); margin-bottom: 10px;">Logs ocupando espaço desnecessário?</p>
            <a href="/admin/removal/clear-logs" class="button" style="background: var(--color-warning); color: white;" onclick="return confirm('⚠️ Isso irá DELETAR PERMANENTEMENTE todos os logs de remoção.\\n\\nTem certeza que deseja continuar?\\n\\nEsta ação NÃO pode ser desfeita!')">
                🗑️ Limpar Todos os Logs de Remoção
            </a>
        </div>
    </div>
    """
    
    return HTMLResponse(_html_page("Auto Removal Dashboard", body))


@app.post("/admin/whitelist/add")
async def admin_add_whitelist(
    request: Request,
    telegram_user_id: str = Form(...),
    email: str = Form(None),
    reason: str = Form(...),
    added_by: str = Form(...)
):
    """Adicionar usuário à whitelist por Telegram ID"""
    _require_admin(request)
    
    try:
        with SessionLocal() as db:
            from crud import add_to_whitelist
            success = add_to_whitelist(db, telegram_user_id, reason, added_by, email)
            if success:
                logger.info(f"✅ Added Telegram ID {telegram_user_id} to whitelist")
            else:
                logger.warning(f"⚠️ Failed to add Telegram ID {telegram_user_id} to whitelist (may already exist)")
    except Exception as e:
        logger.error(f"Error adding to whitelist: {e}")
        
    return RedirectResponse(url="/admin/removal", status_code=303)


@app.post("/admin/whitelist/remove")
async def admin_remove_whitelist(
    request: Request,
    telegram_user_id: str = Form(...)
):
    """Remover usuário da whitelist por Telegram ID"""
    _require_admin(request)
    
    try:
        with SessionLocal() as db:
            from crud import remove_from_whitelist
            success = remove_from_whitelist(db, telegram_user_id)
            if success:
                logger.info(f"✅ Removed Telegram ID {telegram_user_id} from whitelist")
            else:
                logger.warning(f"⚠️ Telegram ID {telegram_user_id} not found in whitelist")
    except Exception as e:
        logger.error(f"Error removing from whitelist: {e}")
        
    return RedirectResponse(url="/admin/removal", status_code=303)


def _safe_db_operation(operation_func, db_session, *args, **kwargs):
    """
    Execute database operation safely with automatic rollback on error
    """
    try:
        # Ensure clean transaction state
        db_session.rollback()
        result = operation_func(db_session, *args, **kwargs)
        return result, None
    except Exception as e:
        logger.error(f"Database operation failed: {e}")
        try:
            db_session.rollback()
        except:
            pass
        return None, str(e)


def _create_tables_safely():
    """Criar tabelas usando SQLAlchemy de forma segura"""
    try:
        from sqlalchemy import text, inspect
        
        with SessionLocal() as db:
            inspector = inspect(db.bind)
            existing_tables = inspector.get_table_names()
            
            # Check which tables exist
            tables_status = {
                'subscriptions': 'subscriptions' in existing_tables,
                'stripe_events': 'stripe_events' in existing_tables,
                'invite_logs': 'invite_logs' in existing_tables,
                'removal_logs': 'removal_logs' in existing_tables,
                'whitelist': 'whitelist' in existing_tables,
                'notification_logs': 'notification_logs' in existing_tables
            }
            
            missing_tables = [table for table, exists in tables_status.items() if not exists]
            
            if missing_tables:
                logger.info(f"Creating missing tables: {missing_tables}")
                # Only create missing tables
                init_db()
                logger.info("✅ Missing tables created successfully")
            else:
                logger.info("✅ All tables already exist")
            
            return True, f"Tables status: {tables_status}. Missing tables created: {missing_tables}"
            
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            logger.warning(f"Tables/indexes already exist: {e}")
            return True, "Tables already exist (this is normal)"
        else:
            logger.error(f"Error creating tables: {e}")
            return False, str(e)


@app.get("/admin/setup-tables")
async def admin_setup_tables(request: Request):
    """Forçar criação das novas tabelas de forma segura"""
    _require_admin(request)
    
    try:
        if DATABASE_AVAILABLE:
            success, message = _create_tables_safely()
            logger.info("📊 Admin triggered safe table creation")
            
            if success:
                return HTMLResponse(_html_page(
                    "Tables Setup Complete",
                    f"""
                    <h1>✅ Tables Setup Successfully</h1>
                    <p>{message}</p>
                    <p>The database tables (RemovalLog, Whitelist) are now ready.</p>
                    <p>You can now access the <a href="/admin/removal">Auto Removal Dashboard</a>.</p>
                    <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                    """
                ))
            else:
                return HTMLResponse(_html_page(
                    "Tables Setup Failed",
                    f"""
                    <h1>❌ Tables Setup Failed</h1>
                    <p>Error: {message}</p>
                    <p>The tables might already exist or there's a permission issue.</p>
                    <p><a href="/admin/removal">Try Auto Removal Dashboard</a> anyway.</p>
                    <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                    """
                ))
        else:
            return HTMLResponse(_html_page(
                "Database Not Available",
                """
                <h1>❌ Database Not Available</h1>
                <p>Database is not available. Cannot create tables.</p>
                <p><a href="/admin/subscriptions">← Back to Admin</a></p>
                """
            ))
            
    except Exception as e:
        logger.error(f"Table setup failed: {e}")
        return HTMLResponse(_html_page(
            "Table Setup Error", 
            f"""
            <h1>❌ Table Setup Error</h1>
            <p>Error: {str(e)}</p>
            <p>The tables might already exist. Try accessing the <a href="/admin/removal">Auto Removal Dashboard</a> directly.</p>
            <p><a href="/admin/subscriptions">← Back to Admin</a></p>
            """
        ))


@app.get("/admin/removal/test-notifications")
async def admin_test_notifications(request: Request):
    """Testar sistema de notificações antecipadas"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        start_time = datetime.utcnow()
        
        logger.info("📱 Admin triggered NOTIFICATION TEST")
        await send_expiry_notifications()
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        try:
            from template_engine import render_template
            return HTMLResponse(render_template(
                "notification_test_results",
                title="Notification Test Results",
                start_time=start_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
                end_time=end_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
                duration=f"{duration:.1f}"
            ))
        except Exception:
            # Fallback to inline HTML with better contrast
            return HTMLResponse(_html_page(
                "Notification Test Results",
                f"""
                <div class="alert-success" style="padding: 20px; margin-bottom: 20px; border-radius: 8px;">
                    <h1>✅ Notification Test Completed</h1>
                    <p><strong>📱 EXPIRY NOTIFICATIONS SENT</strong></p>
                    <p>🕐 <strong>Started:</strong> {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    <p>🏁 <strong>Completed:</strong> {end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    <p>⏱️ <strong>Duration:</strong> {duration:.1f} seconds</p>
                </div>
                
                <h2 style="color: #e2e8f0;">📊 What Happened:</h2>
                <ul style="color: #cbd5e1; line-height: 1.6;">
                    <li>✅ <strong>Checked 7-day warnings</strong> (expires in 7 days)</li>
                    <li>✅ <strong>Checked 3-day warnings</strong> (expires in 3 days)</li>
                    <li>✅ <strong>Checked 1-day warnings</strong> (expires tomorrow)</li>
                    <li>✅ <strong>Checked today warnings</strong> (expires today)</li>
                    <li>📱 <strong>Sent DMs</strong> to users who haven't received warnings yet</li>
                    <li>📝 <strong>Logged all notifications</strong> to prevent duplicates</li>
                </ul>
                
                <div class="nav-buttons" style="margin-top: 20px;">
                    <a href="/admin/removal" class="button">📊 View Dashboard</a>
                    <a href="/admin/subscriptions" class="button">← Back to Admin</a>
                </div>
                """
            ))
        
    except Exception as e:
        logger.error(f"Notification test failed: {e}")
        return HTMLResponse(_html_page(
            "Notification Test Failed", 
            f"""
            <h1>❌ Notification Test Failed</h1>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


@app.get("/admin/removal/execute-now")
async def admin_run_cleanup_now(request: Request):
    """Executar limpeza imediatamente - versão simplificada"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        from crud import get_subscriptions_past_grace_period, get_cancelled_subscriptions
        
        start_time = datetime.utcnow()
        logger.info("🚫 Admin triggered IMMEDIATE cleanup - REAL REMOVAL")
        
        results = {
            'processed': 0,
            'removed': 0,
            'dm_sent': 0,
            'errors': 0,
            'no_telegram_id': 0,
            'details': []
        }
        
        if not application or not application.bot:
            raise Exception("Bot application not available")
        
        bot = application.bot
        
        with SessionLocal() as db:
            # Get expired subscriptions
            expired_past_grace = get_subscriptions_past_grace_period(db)
            cancelled_subs = get_cancelled_subscriptions(db)
            all_subs = expired_past_grace + cancelled_subs
            
            logger.info(f"Found {len(expired_past_grace)} expired and {len(cancelled_subs)} cancelled subscriptions")
            
            # Debug specific users
            target_emails = ["teste@teste.com.br", "johnsantosgamer@gmail.com"]
            for target_email in target_emails:
                found_sub = db.query(models.Subscription).filter_by(email=target_email).first()
                if found_sub:
                    is_in_expired = found_sub in expired_past_grace
                    is_in_cancelled = found_sub in cancelled_subs
                    logger.info(f"🔍 DEBUG {target_email}:")
                    logger.info(f"   - Status: {found_sub.status}")
                    logger.info(f"   - Expires: {found_sub.expires_at}")
                    logger.info(f"   - Telegram ID: {found_sub.telegram_user_id}")
                    logger.info(f"   - In expired list: {is_in_expired}")
                    logger.info(f"   - In cancelled list: {is_in_cancelled}")
                    logger.info(f"   - Will be processed: {is_in_expired or is_in_cancelled}")
                else:
                    logger.warning(f"🔍 DEBUG {target_email}: NOT FOUND in database")
            
            for sub in all_subs:
                results['processed'] += 1
                email = sub.email
                telegram_user_id = sub.telegram_user_id
                reason = "expired" if sub in expired_past_grace else "cancelled"
                
                detail = f"Processing {email} ({reason})"
                logger.info(detail)
                results['details'].append(detail)
                
                if not telegram_user_id:
                    results['no_telegram_id'] += 1
                    detail = f"❌ {email}: No Telegram ID - cannot remove"
                    logger.warning(detail)
                    results['details'].append(detail)
                    continue
                
                try:
                    user_id_int = int(telegram_user_id)
                    
                    # Remove from VIP groups
                    removal_success = False
                    for group_id in VIP_GROUP_IDS:
                        try:
                            await bot.ban_chat_member(chat_id=group_id, user_id=user_id_int)
                            await bot.unban_chat_member(chat_id=group_id, user_id=user_id_int, only_if_banned=True)
                            removal_success = True
                            detail = f"✅ {email}: Removed from group {group_id}"
                            logger.info(detail)
                            results['details'].append(detail)
                        except Exception as e:
                            detail = f"❌ {email}: Failed to remove from group {group_id}: {str(e)}"
                            logger.error(detail)
                            results['details'].append(detail)
                    
                    if removal_success:
                        results['removed'] += 1
                        
                        # Send renewal DM
                        try:
                            message = f"""🚨 VIP Access Expired

Hi! Your VIP subscription has expired and you've been removed from the VIP groups.

📧 Account: {email}
⏰ Expired: Just now

🔄 Renew Now: Contact support @Sthefano_p

Thank you for being a VIP member! 🌟"""
                            
                            await bot.send_message(
                                chat_id=user_id_int,
                                text=message
                            )
                            results['dm_sent'] += 1
                            detail = f"✅ {email}: Renewal DM sent"
                            logger.info(detail)
                            results['details'].append(detail)
                        except Exception as e:
                            detail = f"❌ {email}: Failed to send DM: {str(e)}"
                            logger.error(detail)
                            results['details'].append(detail)
                        
                        # Update subscription status
                        sub.status = "auto_removed"
                        sub.updated_at = datetime.utcnow()
                        db.commit()
                        
                        detail = f"✅ {email}: Status updated to auto_removed"
                        logger.info(detail)
                        results['details'].append(detail)
                    
                except Exception as e:
                    results['errors'] += 1
                    detail = f"❌ {email}: Error in removal process: {str(e)}"
                    logger.error(detail)
                    results['details'].append(detail)
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        details_html = "<br>".join(results['details'])
        
        try:
            from template_engine import render_template
            return HTMLResponse(render_template(
                "cleanup_results",
                title="Cleanup Results",
                duration=f"{duration:.1f}",
                processed=results['processed'],
                removed=results['removed'],
                dm_sent=results['dm_sent'],
                errors=results['errors'],
                no_telegram_id=results['no_telegram_id'],
                details_html=details_html
            ))
        except Exception:
            # Fallback with better contrast
            return HTMLResponse(_html_page(
                "Cleanup Results",
                f"""
                <div class="alert-success" style="padding: 20px; margin-bottom: 20px; border-radius: 8px;">
                    <h1>✅ Cleanup Executed</h1>
                    <p>🕐 <strong>Duration:</strong> {duration:.1f} seconds</p>
                    <p>📊 <strong>Processed:</strong> {results['processed']} subscriptions</p>
                    <p>🚫 <strong>Removed:</strong> {results['removed']} users</p>
                    <p>📱 <strong>DMs Sent:</strong> {results['dm_sent']}</p>
                    <p>❌ <strong>Errors:</strong> {results['errors']}</p>
                    <p>⚠️ <strong>No Telegram ID:</strong> {results['no_telegram_id']}</p>
                </div>
                
                <h2 style="color: #e2e8f0;">📋 Detailed Log:</h2>
                <div class="alert-info" style="padding: 15px; border-radius: 8px; font-family: monospace; font-size: 0.9em; line-height: 1.4;">
                    {details_html}
                </div>
                
                <div class="nav-buttons" style="margin-top: 20px;">
                    <a href="/admin/removal" class="button">📊 Back to Dashboard</a>
                    <a href="/admin/subscriptions" class="button">← Admin Panel</a>
                </div>
                """
            ))
        
    except Exception as e:
        logger.error(f"Manual cleanup failed: {e}")
        return HTMLResponse(_html_page(
            "Cleanup Failed", 
            f"""
            <h1>❌ Cleanup Failed</h1>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


@app.get("/admin/removal/diagnose")
async def admin_diagnose_system(request: Request):
    """Diagnóstico completo do sistema de remoção"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        from crud import get_subscriptions_past_grace_period, get_cancelled_subscriptions
        
        current_time = datetime.utcnow()
        current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S UTC')
        
        # Diagnóstico completo
        diagnosis = {
            'scheduler_running': scheduler and scheduler.running,
            'database_available': DATABASE_AVAILABLE,
            'vip_groups_configured': len(VIP_GROUP_IDS) > 0,
            'bot_token_configured': bool(TOKEN),
            'auto_removal_enabled': os.getenv("ENABLE_AUTO_REMOVAL", "1") == "1"
        }
        
        with SessionLocal() as db:
            # Buscar dados específicos
            all_active = db.query(models.Subscription).filter_by(status="active").all()
            expired_past_grace = get_subscriptions_past_grace_period(db)
            
            # Analisar problemas específicos
            expired_without_telegram = []
            expired_with_telegram = []
            
            for sub in expired_past_grace:
                if not sub.telegram_user_id:
                    expired_without_telegram.append(sub)
                else:
                    expired_with_telegram.append(sub)
        
        # Status das configurações
        config_status = ""
        for key, value in diagnosis.items():
            icon = "✅" if value else "❌"
            config_status += f"<p>{icon} <strong>{key.replace('_', ' ').title()}:</strong> {value}</p>"
        
        # Análise das assinaturas expiradas
        expired_analysis = ""
        for sub in expired_past_grace:
            telegram_status = "✅ Has ID" if sub.telegram_user_id else "❌ Missing ID"
            telegram_color = "#16a34a" if sub.telegram_user_id else "#dc2626"
            
            expired_analysis += f"""
            <tr>
                <td>{sub.id}</td>
                <td>{sub.email}</td>
                <td>{sub.expires_at.strftime('%Y-%m-%d %H:%M:%S') if sub.expires_at else 'NULL'}</td>
                <td>{sub.status}</td>
                <td style="color: {telegram_color};">{telegram_status}</td>
                <td>{sub.telegram_user_id or 'NULL'}</td>
            </tr>
            """
        
        # Build the HTML content step by step to avoid f-string issues
        status_section = f"""
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>🕐 Current Status</h2>
                <p><strong>Server Time:</strong> {current_time_str}</p>
                <p><strong>Total Active Subscriptions:</strong> {len(all_active)}</p>
                <p><strong>Expired Subscriptions:</strong> {len(expired_past_grace)}</p>
                <p><strong>Expired with Telegram ID:</strong> {len(expired_with_telegram)}</p>
                <p><strong>Expired without Telegram ID:</strong> {len(expired_without_telegram)}</p>
            </div>
        """
        
        config_section = f"""
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>⚙️ System Configuration</h2>
                {config_status}
            </div>
        """
        
        # Build actions section safely
        actions_content = ""
        if all(diagnosis.values()):
            actions_content += '<p>✅ System is properly configured!</p>'
        else:
            if not diagnosis['scheduler_running']:
                actions_content += '<p>❌ <strong>Scheduler not running</strong> - Auto removal won\'t work</p>'
            if not diagnosis['database_available']:
                actions_content += '<p>❌ <strong>Database not available</strong> - Cannot access subscriptions</p>'
            if not diagnosis['vip_groups_configured']:
                actions_content += '<p>❌ <strong>No VIP groups configured</strong> - Nowhere to remove users from</p>'
            if not diagnosis['bot_token_configured']:
                actions_content += '<p>❌ <strong>Bot token missing</strong> - Cannot perform removals</p>'
            if not diagnosis['auto_removal_enabled']:
                actions_content += '<p>❌ <strong>Auto removal disabled</strong> - Set ENABLE_AUTO_REMOVAL=1</p>'
        
        if expired_without_telegram:
            actions_content += f'<p>⚠️ <strong>{len(expired_without_telegram)} expired users have no Telegram ID</strong> - Cannot remove them</p>'
        if expired_with_telegram:
            actions_content += f'<p>🎯 <strong>{len(expired_with_telegram)} expired users ready for removal</strong></p>'
        
        actions_section = f"""
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>🚨 Immediate Actions Needed</h2>
                {actions_content}
            </div>
        """
        
        table_section = f"""
            <h2>📋 Expired Subscriptions Analysis</h2>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Email</th>
                            <th>Expires At</th>
                            <th>Status</th>
                            <th>Telegram Status</th>
                            <th>Telegram ID</th>
                        </tr>
                    </thead>
                    <tbody>
                        {expired_analysis or '<tr><td colspan="6" class="muted">No expired subscriptions</td></tr>'}
                    </tbody>
                </table>
            </div>
        """
        
        recommendations_section = f"""
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-top: 20px;">
                <h3>🎯 Recommended Actions:</h3>
                <ol style="color: #d1d5db; line-height: 1.6;">
                    <li><strong>Check VIP Groups:</strong> Ensure bot is admin in groups {VIP_GROUP_IDS}</li>
                    <li><strong>Manual Test:</strong> Use "Run Cleanup Now" to test immediately</li>
                    <li><strong>Check Logs:</strong> Look for error messages in system logs</li>
                    <li><strong>Verify Scheduler:</strong> Ensure it's running and scheduled correctly</li>
                </ol>
            </div>
        """
        
        navigation_section = """
            <div style="margin-top: 20px;">
                <a href="/admin/removal" class="button">← Back to Dashboard</a>
                <a href="/admin/removal/run-now" class="button" style="background: #dc2626; color: white;">🚫 Run Cleanup Now</a>
                <a href="/admin/removal/check-expiry" class="button">📅 Check Expiry Logic</a>
            </div>
        """
        
        full_content = f"""
            <h1>🔍 System Diagnosis</h1>
            {status_section}
            {config_section}
            {actions_section}
            {table_section}
            {recommendations_section}
            {navigation_section}
        """
        
        return HTMLResponse(_html_page("System Diagnosis", full_content))
        
    except Exception as e:
        logger.error(f"System diagnosis failed: {e}")
        return HTMLResponse(_html_page(
            "Diagnosis Failed", 
            f"""
            <h1>❌ System Diagnosis Failed</h1>
            <p>Error: {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


@app.get("/admin/removal/check-expiry")
async def admin_check_expiry(request: Request):
    """Verificar lógica de expiração de assinaturas"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        from crud import get_subscriptions_past_grace_period, get_cancelled_subscriptions
        
        current_time = datetime.utcnow()
        current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S UTC')
        
        with SessionLocal() as db:
            # Buscar todas as assinaturas ativas (incluindo expiradas)
            all_active = db.query(models.Subscription).filter_by(status="active").all()
            
            # Buscar apenas as expiradas
            expired_past_grace = get_subscriptions_past_grace_period(db)
            
            # Buscar canceladas
            cancelled_subs = get_cancelled_subscriptions(db)
        
        # Análise detalhada
        analysis_html = ""
        
        for sub in all_active:
            if sub.expires_at:
                expires_str = sub.expires_at.strftime('%d/%m/%Y %H:%M')
                expires_iso = sub.expires_at.strftime('%Y-%m-%d %H:%M:%S')
                
                is_expired = sub.expires_at < current_time
                status_icon = "❌ EXPIRED" if is_expired else "✅ ACTIVE"
                status_color = "#dc2626" if is_expired else "#16a34a"
                
                analysis_html += f"""
                <tr>
                    <td>{sub.id}</td>
                    <td>{sub.email}</td>
                    <td>{expires_str}</td>
                    <td>{expires_iso}</td>
                    <td style="color: {status_color}; font-weight: bold;">{status_icon}</td>
                    <td>{'YES' if sub in expired_past_grace else 'NO'}</td>
                </tr>
                """
            else:
                analysis_html += f"""
                <tr>
                    <td>{sub.id}</td>
                    <td>{sub.email}</td>
                    <td>No expiry date</td>
                    <td>NULL</td>
                    <td style="color: #6b7280;">♾️ PERMANENT</td>
                    <td>NO</td>
                </tr>
                """
        
        return HTMLResponse(_html_page(
            "Expiry Logic Check",
            f"""
            <h1>📅 Expiry Logic Verification</h1>
            
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>🕐 Current Server Time</h2>
                <p style="font-size: 1.2em; font-weight: bold;">{current_time_str}</p>
                <p><strong>Logic:</strong> If <code>expires_at &lt; current_time</code> → EXPIRED</p>
            </div>
            
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>📊 Summary</h2>
                <p><strong>Total Active Subscriptions:</strong> {len(all_active)}</p>
                <p><strong>Expired (will be removed):</strong> {len(expired_past_grace)}</p>
                <p><strong>Cancelled (will be removed):</strong> {len(cancelled_subs)}</p>
            </div>
            
            <h2>📋 Detailed Analysis</h2>
            <div style="overflow-x: auto;">
                <table class="debug-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Email</th>
                            <th>Expires (Display)</th>
                            <th>Expires (Database)</th>
                            <th>Status</th>
                            <th>In Expired List</th>
                        </tr>
                    </thead>
                    <tbody>
                        {analysis_html or '<tr><td colspan="6" class="muted">No active subscriptions</td></tr>'}
                    </tbody>
                </table>
            </div>
            
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-top: 20px;">
                <h3>🔍 Example Logic:</h3>
                <p><strong>Subscription expires:</strong> 18/08/2025 00:00 → 2025-08-18 00:00:00</p>
                <p><strong>Current server time:</strong> {current_time_str}</p>
                <p><strong>Comparison:</strong> 2025-08-18 00:00:00 &lt; {current_time.strftime('%Y-%m-%d %H:%M:%S')} = <strong>{'TRUE (EXPIRED)' if datetime(2025, 8, 18, 0, 0) < current_time else 'FALSE (ACTIVE)'}</strong></p>
            </div>
            
            <div style="margin-top: 20px;">
                <a href="/admin/removal" class="button">← Back to Dashboard</a>
                <a href="/admin/removal/run-now" class="button" style="background: #dc2626; color: white;">🚫 Remove Expired Now</a>
            </div>
            """
        ))
        
    except Exception as e:
        logger.error(f"Expiry check failed: {e}")
        return HTMLResponse(_html_page(
            "Expiry Check Failed", 
            f"""
            <h1>❌ Expiry Check Failed</h1>
            <p>Error: {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


@app.get("/admin/removal/test")
async def admin_test_removal(request: Request):
    """Executar teste de remoção (modo seguro)"""
    _require_admin(request)
    
    try:
        # Executar cleanup em modo teste (não remove realmente)
        logger.info("🧪 Admin triggered test cleanup")
        await cleanup_expired_past_gracecriptions()
        
        return HTMLResponse(_html_page(
            "Test Completed",
            """
            <h1>🧪 Test Completed</h1>
            <p>The cleanup test has been executed. Check the logs and <a href="/admin/removal">dashboard</a> for results.</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))
        
    except Exception as e:
        logger.error(f"Test cleanup failed: {e}")
        return HTMLResponse(_html_page(
            "Test Failed", 
            f"""
            <h1>❌ Test Failed</h1>
            <p>Error: {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


@app.get("/admin/debug/whitelist")
async def admin_debug_whitelist(request: Request):
    """Debug da whitelist - verificar por que não está funcionando"""
    _require_admin(request)
    
    debug_info = []
    
    try:
        with SessionLocal() as db:
            # Listar todas as entradas da whitelist
            whitelist_entries = db.query(models.Whitelist).all()
            debug_info.append(f"🔍 <strong>Whitelist Debug</strong>")
            debug_info.append(f"Total entries: {len(whitelist_entries)}")
            debug_info.append("")
            
            for entry in whitelist_entries:
                debug_info.append(f"<strong>Entry ID {entry.id}:</strong>")
                debug_info.append(f"  Telegram ID: '{entry.telegram_user_id}' (type: {type(entry.telegram_user_id).__name__})")
                debug_info.append(f"  Email: '{entry.email}'")
                debug_info.append(f"  Reason: '{entry.reason}'")
                debug_info.append(f"  Added by: '{entry.added_by}'")
                debug_info.append("")
            
            # Testar verificação para Layd especificamente
            debug_info.append("<strong>🧪 Testing Layd specifically:</strong>")
            test_telegram_id = "1374977336"
            test_email = "layd@laydjane.com"
            
            # Teste 1: Por Telegram ID
            result1 = is_whitelisted(db, telegram_user_id=test_telegram_id)
            debug_info.append(f"Test 1 - is_whitelisted(telegram_user_id='{test_telegram_id}'): {result1}")
            
            # Teste 2: Por Email
            result2 = is_whitelisted(db, email=test_email)
            debug_info.append(f"Test 2 - is_whitelisted(email='{test_email}'): {result2}")
            
            # Teste 3: Ambos
            result3 = is_whitelisted(db, email=test_email, telegram_user_id=test_telegram_id)
            debug_info.append(f"Test 3 - is_whitelisted(email='{test_email}', telegram_user_id='{test_telegram_id}'): {result3}")
            
            # Query manual para debug
            debug_info.append("")
            debug_info.append("<strong>🔍 Manual Query Debug:</strong>")
            manual_query = db.query(models.Whitelist).filter_by(telegram_user_id=test_telegram_id).first()
            debug_info.append(f"Manual query result: {manual_query is not None}")
            if manual_query:
                debug_info.append(f"Found entry: ID={manual_query.id}, telegram_user_id='{manual_query.telegram_user_id}'")
            
    except Exception as e:
        debug_info.append(f"❌ Error: {str(e)}")
        import traceback
        debug_info.append(f"Traceback: {traceback.format_exc()}")
    
    body = f"""
    <h1>🔍 Whitelist Debug</h1>
    <div style="background: rgb(8, 10, 13); padding: 20px; border-radius: 8px; font-family: monospace;">
        {"<br>".join(debug_info)}
    </div>
    
    <div style="margin-top: 20px;">
        <a href="/admin/removal" class="button">← Back to Auto Removal</a>
        <a href="/admin" class="button">🏠 Admin Home</a>
    </div>
    """
    
    return HTMLResponse(_html_page("Whitelist Debug", body))

@app.get("/admin/debug/database")
async def admin_debug_database(request: Request):
    """Debug crítico da conexão com banco de dados"""
    _require_admin(request)
    
    debug_results = []
    
    try:
        debug_results.append("🔍 Starting comprehensive database debug...")
        
        # Test 1: Check if DATABASE_AVAILABLE
        debug_results.append(f"📊 DATABASE_AVAILABLE: {DATABASE_AVAILABLE}")
        
        # Test 2: Try to create session
        try:
            with SessionLocal() as db:
                debug_results.append("✅ SessionLocal() created successfully")
                
                # Test 3: Simple connection test
                try:
                    from sqlalchemy import text
                    result = db.execute(text("SELECT 1 as test")).scalar()
                    debug_results.append(f"✅ Basic SQL query works: {result}")
                except Exception as e:
                    debug_results.append(f"❌ Basic SQL query failed: {e}")
                    return _create_debug_response(debug_results, "Database Connection Failed")
                
                # Test 4: Check if subscriptions table exists
                try:
                    table_check = db.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar()
                    debug_results.append(f"✅ Subscriptions table exists with {table_check} records")
                except Exception as e:
                    debug_results.append(f"❌ Subscriptions table error: {e}")
                    return _create_debug_response(debug_results, "Table Access Failed")
                
                # Test 5: Try ORM query
                try:
                    import models
                    orm_count = db.query(models.Subscription).count()
                    debug_results.append(f"✅ ORM query works: {orm_count} subscriptions")
                except Exception as e:
                    debug_results.append(f"❌ ORM query failed: {e}")
                    return _create_debug_response(debug_results, "ORM Query Failed")
                
                # Test 6: Get all subscriptions with details
                try:
                    all_subs = db.query(models.Subscription).all()
                    debug_results.append(f"✅ Found {len(all_subs)} total subscriptions:")
                    
                    for sub in all_subs:
                        debug_results.append(f"   - ID {sub.id}: {sub.email} (status: {sub.status}, expires: {sub.expires_at})")
                        
                except Exception as e:
                    debug_results.append(f"❌ Failed to get subscription details: {e}")
                    return _create_debug_response(debug_results, "Subscription Details Failed")
                
                # Test 7: Check database URL
                try:
                    from db import db_path_info
                    db_url = db_path_info(db)
                    debug_results.append(f"✅ Database URL: {db_url}")
                except Exception as e:
                    debug_results.append(f"❌ Database URL error: {e}")
        
        except Exception as e:
            debug_results.append(f"❌ SessionLocal creation failed: {e}")
            return _create_debug_response(debug_results, "Session Creation Failed")
        
        debug_results.append("🎉 ALL TESTS PASSED - Database is working correctly!")
        return _create_debug_response(debug_results, "Database Debug Complete")
        
    except Exception as e:
        debug_results.append(f"❌ Critical error in database debug: {e}")
        return _create_debug_response(debug_results, "Critical Debug Error")


def _create_debug_response(debug_results, title):
    """Create debug response page"""
    results_html = "<br>".join(debug_results)
    
    return HTMLResponse(_html_page(
        title,
        f"""
        <h1>🔍 {title}</h1>
        
        <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h2>📋 Debug Results</h2>
            <div style="font-family: monospace; font-size: 0.9em; line-height: 1.6;">
                {results_html}
            </div>
        </div>
        
        <div class="nav-buttons" style="margin-top: 20px;">
            <a href="/admin/removal" class="button">← Back to Dashboard</a>
            <a href="/admin/subscriptions" class="button">← Admin Panel</a>
        </div>
        """
    ))


@app.get("/admin/removal/debug-specific")
async def admin_debug_specific_users(request: Request):
    """Debug específico para os usuários mencionados"""
    _require_admin(request)
    
    try:
        from datetime import datetime
        from crud import get_subscriptions_past_grace_period, is_whitelisted
        
        current_time = datetime.utcnow()
        
        # Buscar TODOS os usuários da database (não apenas emails específicos)
        target_emails = []
        
        debug_info = []
        
        with SessionLocal() as db:
            try:
                # Rollback any pending transaction to start fresh
                db.rollback()
                
                # NOVA ABORDAGEM: Usar SQL direto para garantir que funciona
                all_subscriptions = []
                
                try:
                    # Method 1: Direct SQL query
                    logger.info("🔍 Trying direct SQL query...")
                    from sqlalchemy import text
                    sql_result = db.execute(text("""
                        SELECT id, email, full_name, status, plan_type, 
                               telegram_user_id, expires_at, created_at
                        FROM subscriptions 
                        ORDER BY id DESC
                    """)).fetchall()
                    
                    logger.info(f"✅ Direct SQL found {len(sql_result)} subscriptions")
                    
                    # Convert SQL results to objects
                    for row in sql_result:
                        # Create a simple object with the data
                        class SimpleSubscription:
                            def __init__(self, row):
                                self.id = row[0]
                                self.email = row[1] 
                                self.full_name = row[2]
                                self.status = row[3]
                                self.plan_type = row[4]
                                self.telegram_user_id = row[5]
                                self.expires_at = row[6]
                                self.created_at = row[7]
                        
                        all_subscriptions.append(SimpleSubscription(row))
                        logger.info(f"   📋 ID {row[0]}: {row[1]} (status: {row[3]})")
                    
                except Exception as e:
                    logger.error(f"❌ Direct SQL query failed: {e}")
                    
                    # Method 2: Try ORM as fallback
                    try:
                        logger.info("🔍 Trying ORM query as fallback...")
                        db.rollback()  # Clear any failed transaction
                        orm_subs = db.query(models.Subscription).all()
                        all_subscriptions = orm_subs
                        logger.info(f"✅ ORM query found {len(all_subscriptions)} subscriptions")
                    except Exception as e2:
                        logger.error(f"❌ ORM query also failed: {e2}")
                        all_subscriptions = []
                
                for sub in all_subscriptions:
                    try:
                        email = sub.email
                        is_expired_logic = sub.expires_at < current_time if sub.expires_at else False
                        
                        # Check if in expired list safely
                        try:
                            expired_list = get_subscriptions_past_grace_period(db)
                            is_in_expired_list = sub in expired_list
                        except Exception as e:
                            logger.warning(f"Error checking expired list: {e}")
                            db.rollback()
                            is_in_expired_list = False
                        
                        # Check whitelist safely
                        try:
                            is_protected = is_whitelisted(db, email=email, telegram_user_id=sub.telegram_user_id)
                        except Exception as e:
                            logger.warning(f"Error checking whitelist for {email}: {e}")
                            db.rollback()
                            is_protected = False
                        
                        # Determine removal status
                        should_remove = (
                            is_expired_logic and 
                            sub.status == 'active' and 
                            not is_protected and 
                            sub.telegram_user_id
                        )
                        
                        debug_info.append({
                            'email': email,
                            'found': True,
                            'id': sub.id,
                            'status': sub.status,
                            'expires_at': sub.expires_at.strftime('%Y-%m-%d %H:%M:%S') if sub.expires_at else 'NULL',
                            'telegram_user_id': sub.telegram_user_id,
                            'is_expired_logic': is_expired_logic,
                            'is_in_expired_list': is_in_expired_list,
                            'is_whitelisted': is_protected,
                            'should_be_removed': should_remove
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing subscription {sub.id}: {e}")
                        db.rollback()
                        debug_info.append({
                            'email': f"Error processing ID {sub.id}",
                            'found': False,
                            'error': str(e)
                        })
                        
            except Exception as e:
                logger.error(f"Critical error in debug session: {e}")
                db.rollback()
                # Return error info
                debug_info = [
                    {'email': email, 'found': False, 'error': 'Database transaction error'}
                    for email in target_emails
                ]
        
        # Gerar HTML
        debug_html = ""
        for info in debug_info:
            if info['found']:
                should_remove_color = "#16a34a" if info['should_be_removed'] else "#dc2626"
                should_remove_text = "✅ YES" if info['should_be_removed'] else "❌ NO"
                
                debug_html += f"""
                <tr>
                    <td>{info['id']}</td>
                    <td>{info['email']}</td>
                    <td>{info['status']}</td>
                    <td>{info['expires_at']}</td>
                    <td>{info['telegram_user_id'] or 'NULL'}</td>
                    <td>{'✅ YES' if info['is_expired_logic'] else '❌ NO'}</td>
                    <td>{'✅ YES' if info['is_in_expired_list'] else '❌ NO'}</td>
                    <td>{'✅ YES' if info['is_whitelisted'] else '❌ NO'}</td>
                    <td style="color: {should_remove_color}; font-weight: bold;">{should_remove_text}</td>
                </tr>
                """
            else:
                debug_html += f"""
                <tr>
                    <td colspan="9" style="color: #dc2626;">❌ Subscription not found for {info['email']}</td>
                </tr>
                """
        
        return HTMLResponse(_html_page(
            "Debug Active Users",
            f"""
            <h1>🔍 Debug: Database Users Analysis</h1>
            
            <div class="light-bg-override" style="padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h2>🕐 Current Server Time</h2>
                <p id="debug-server-time" style="font-size: 1.2em; font-weight: bold;">{current_time.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                <p><strong>Subscriptions Found:</strong> {len(debug_info)}</p>
                <p><strong>Note:</strong> Showing all users for complete debugging</p>
            </div>
            
            <script>
                // Update server time every second
                function updateDebugServerTime() {{
                    const now = new Date();
                    const utcTime = now.toISOString().slice(0, 19).replace('T', ' ') + ' UTC';
                    const timeElement = document.getElementById('debug-server-time');
                    if (timeElement) {{
                        timeElement.textContent = utcTime;
                    }}
                }}
                
                // Update immediately and then every second
                updateDebugServerTime();
                setInterval(updateDebugServerTime, 1000);
            </script>
            
            <h2>📋 Active Users Analysis</h2>
            <div style="overflow-x: auto;">
                <table class="debug-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Email</th>
                            <th>Status</th>
                            <th>Expires At</th>
                            <th>Telegram ID</th>
                            <th>Is Expired?</th>
                            <th>In Expired List?</th>
                            <th>Whitelisted?</th>
                            <th>Should Remove?</th>
                        </tr>
                    </thead>
                    <tbody>
                        {debug_html}
                    </tbody>
                </table>
            </div>
            
            <div class="alert-info" style="padding: 15px; border-radius: 8px; margin-top: 20px;">
                <h3>🎯 What This Tells Us:</h3>
                <ul style="color: #3b82f6; line-height: 1.6;">
                    <li><strong>Is Expired?</strong> - expires_at &lt; current_time</li>
                    <li><strong>In Expired List?</strong> - Found by get_subscriptions_past_grace_period()</li>
                    <li><strong>Should Remove?</strong> - All conditions met for removal</li>
                </ul>
                <div class="alert-success" style="margin-top: 15px; padding: 10px; border-radius: 6px;">
                    <p><strong>📊 Analysis Summary:</strong></p>
                    <p>• <strong>Total Users:</strong> {len([info for info in debug_info if info.get('found', False)])}</p>
                    <p>• <strong>Active:</strong> {len([info for info in debug_info if info.get('status') == 'active'])}</p>
                    <p>• <strong>Expired (should remove):</strong> {len([info for info in debug_info if info.get('should_be_removed', False)])}</p>
                    <p>• <strong>Already removed:</strong> {len([info for info in debug_info if info.get('status') in ['manually_removed', 'auto_removed']])}</p>
                    <p><strong>🎯 System Status:</strong> All users properly categorized and processed!</p>
                </div>
            </div>
            
            <div style="margin-top: 20px;">
                <a href="/admin/removal" class="button">← Back to Dashboard</a>
                <a href="/admin/removal/run-now" class="button" style="background: #dc2626; color: white;">🚫 Remove These Users Now</a>
            </div>
            """
        ))
        
    except Exception as e:
        logger.error(f"Debug specific users failed: {e}")
        return HTMLResponse(_html_page(
            "Debug Failed", 
            f"""
            <h1>❌ Debug Failed</h1>
            <p>Error: {str(e)}</p>
            <p><a href="/admin/removal">← Back to Dashboard</a></p>
            """
        ))


if __name__ == "__main__":
    main()

# ======================
# 🗑️ Cleanup Routes
# ======================

@app.get("/admin/removal/clear-logs")
async def admin_clear_removal_logs(request: Request):
    """Limpar todos os logs de remoção"""
    _require_admin(request)
    
    try:
        with SessionLocal() as db:
            # Contar logs antes da limpeza
            total_logs_before = db.query(models.RemovalLog).count()
            
            # Deletar todos os logs de remoção
            deleted_count = db.query(models.RemovalLog).delete()
            db.commit()
            
            logger.info(f"🗑️ Admin cleared {deleted_count} removal logs (total before: {total_logs_before})")
            
            body = f"""
            <div style="text-align: center; margin-top: 50px;">
                <h1>🗑️ Logs Limpos com Sucesso</h1>
                <div class="alert-success" style="margin: 30px auto; max-width: 500px;">
                    <h3>✅ Limpeza Concluída</h3>
                    <p><strong>Logs removidos:</strong> {deleted_count}</p>
                    <p><strong>Total antes:</strong> {total_logs_before}</p>
                    <p><strong>Espaço liberado:</strong> Tabela removal_logs limpa</p>
                </div>
                
                <div style="margin-top: 30px;">
                    <a href="/admin/removal" class="button" style="background: var(--color-success);">← Voltar para Dashboard</a>
                    <a href="/admin/data" class="button" style="background: var(--color-info);">📊 Data Viewer</a>
                </div>
                
                <div style="margin-top: 20px; color: var(--text-muted);">
                    <p>💡 <strong>Dica:</strong> Os logs são recriados automaticamente conforme o sistema funciona.</p>
                    <p>🔄 Novos logs aparecerão nas próximas execuções do scheduler.</p>
                </div>
            </div>
            """
            
            return HTMLResponse(_html_page("Logs Limpos", body))
            
    except Exception as e:
        logger.error(f"Error clearing removal logs: {e}")
        
        body = f"""
        <div style="text-align: center; margin-top: 50px;">
            <h1>❌ Erro ao Limpar Logs</h1>
            <div class="alert-error" style="margin: 30px auto; max-width: 500px;">
                <h3>Falha na Limpeza</h3>
                <p><strong>Erro:</strong> {str(e)}</p>
                <p>Pode ser que a tabela não exista ainda ou haja um problema de permissão.</p>
            </div>
            
            <div style="margin-top: 30px;">
                <a href="/admin/removal" class="button">← Voltar para Dashboard</a>
                <a href="/admin/setup-tables" class="button" style="background: var(--color-warning);">�� Setup Tables</a>
            </div>
        </div>
        """
        
        return HTMLResponse(_html_page("Erro na Limpeza", body))

