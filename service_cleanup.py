import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from telegram import Update, ChatMemberUpdated
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from db import SessionLocal
from crud import get_service_cleanup_config, record_service_message_seen

logger = logging.getLogger(__name__)


def _is_join_message(update: Update) -> bool:
    try:
        msg = update.effective_message
        if msg and msg.new_chat_members:
            return True
        # Some joins may arrive as chat_member updates when user joined via link
        cm = update.chat_member
        if isinstance(cm, ChatMemberUpdated):
            old_status = cm.old_chat_member.status
            new_status = cm.new_chat_member.status
            if old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) and new_status in (
                ChatMemberStatus.MEMBER,
            ):
                return True
    except Exception:
        pass
    return False


def _is_leave_message(update: Update) -> bool:
    try:
        msg = update.effective_message
        if msg and msg.left_chat_member:
            return True
        cm = update.chat_member
        if isinstance(cm, ChatMemberUpdated):
            new_status = cm.new_chat_member.status
            if new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return True
    except Exception:
        pass
    return False


async def _send_forward_or_summary(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    forward_target_id: Optional[int],
    update: Update,
    event_type: str,
) -> bool:
    if not forward_target_id:
        return False

    chat = update.effective_chat
    msg = update.effective_message
    ok = False
    # Try to forward/copy the original service message when available
    if msg:
        try:
            await context.bot.copy_message(
                chat_id=forward_target_id,
                from_chat_id=chat.id,
                message_id=msg.message_id,
                protect_content=False,
            )
            logger.info(
                "service_cleanup event=%s action=forward chat_id=%s message_id=%s ok=true",
                event_type,
                chat.id,
                msg.message_id,
            )
            ok = True
        except Exception as e:
            logger.warning(
                "service_cleanup event=%s action=forward ok=false error=%s", event_type, e
            )

    if ok:
        return True

    # Fallback: send structured summary
    try:
        summary_lines = [
            f"[service_cleanup] {event_type.upper()}",
            f"Chat: {chat.title or chat.username or chat.id} (id={chat.id})",
        ]

        # Participants
        if msg and msg.new_chat_members:
            users = [
                f"{u.id} | @{u.username or '-'} | {u.full_name}"
                for u in msg.new_chat_members
            ]
            summary_lines.append("Users: " + "; ".join(users))
        elif msg and msg.left_chat_member:
            u = msg.left_chat_member
            summary_lines.append(f"User: {u.id} | @{u.username or '-'} | {u.full_name}")
        elif update.chat_member:
            u = update.chat_member.new_chat_member.user
            actor = update.chat_member.from_user
            summary_lines.append(f"User: {u.id} | @{u.username or '-'} | {u.full_name}")
            if actor:
                summary_lines.append(
                    f"By: {actor.id} | @{actor.username or '-'} | {actor.full_name}"
                )

        # Timestamp
        summary_lines.append(
            "At: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        )

        await context.bot.send_message(chat_id=forward_target_id, text="\n".join(summary_lines))
        logger.info(
            "service_cleanup event=%s action=send_summary chat_id=%s ok=true",
            event_type,
            chat.id,
        )
        return True
    except Exception as e:
        logger.warning(
            "service_cleanup event=%s action=send_summary ok=false error=%s", event_type, e
        )
        return False


async def _try_delete_message(context: ContextTypes.DEFAULT_TYPE, update: Update, event_type: str) -> bool:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg:
        return False

    # Optional: minimal permission check via chat member status
    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        status = getattr(bot_member, "status", None)
        # In PTB v21, status is ChatMemberStatus enum (ADMINISTRATOR/OWNER/MEMBER/...)
        if status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            logger.warning(
                "service_cleanup event=%s action=skip_no_perm chat_id=%s ok=false reason=not_admin status=%s",
                event_type,
                chat.id,
                status,
            )
            return False
        # If available, check can_delete_messages
        if hasattr(bot_member, "can_delete_messages") and not getattr(bot_member, "can_delete_messages", False):
            logger.warning(
                "service_cleanup event=%s action=skip_no_perm chat_id=%s ok=false reason=no_delete_perm",
                event_type,
                chat.id,
            )
            return False
    except Exception as e:
        logger.debug("service_cleanup permission check failed: %s", e)

    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=msg.message_id)
        logger.info(
            "service_cleanup event=%s action=delete chat_id=%s message_id=%s ok=true",
            event_type,
            chat.id,
            msg.message_id,
        )
        return True
    except Exception as e:
        logger.warning(
            "service_cleanup event=%s action=delete ok=false error=%s", event_type, e
        )
        return False


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight handler that observes join/leave service updates.
    It never stops propagation and only acts when feature flag is enabled.
    """
    # Read config quickly per-update (short DB session); could be cached later
    with SessionLocal() as db:
        cfg = get_service_cleanup_config(db)

    if not cfg.get("enabled", False):
        # disabled; do nothing
        return

    try:
        is_join = _is_join_message(update)
        is_leave = _is_leave_message(update)
        if not (is_join or is_leave):
            return

        event_type = "join" if is_join else "leave"

        # Record seen service message for potential retro cleanup
        try:
            msg = update.effective_message
            chat = update.effective_chat
            if msg and chat:
                with SessionLocal() as db:
                    record_service_message_seen(db, chat_id=chat.id, message_id=msg.message_id, event_type=event_type)
        except Exception:
            pass

        # Schedule deletion ASAP to minimize message visibility
        should_delete = (is_join and cfg.get("remove_on_join", True)) or (
            is_leave and cfg.get("remove_on_leave", True)
        )
        if should_delete and update.effective_message is not None:
            try:
                asyncio.create_task(_try_delete_message(context, update, event_type))
            except Exception:
                # Fallback: if scheduling fails, do it synchronously
                await _try_delete_message(context, update, event_type)

        # Forward/copy or send summary (may fail if message already deleted; we'll fallback)
        await _send_forward_or_summary(
            context,
            forward_target_id=cfg.get("forward_target_id"),
            update=update,
            event_type=event_type,
        )
    except Exception as e:
        logger.warning("service_cleanup action=error error=%s", e)
