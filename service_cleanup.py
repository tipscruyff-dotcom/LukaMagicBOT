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
        logger.warning("service_cleanup event=%s action=skip_no_message chat_id=%s", event_type, chat.id if chat else "unknown")
        return False

    # Log message details for debugging
    logger.info(
        "service_cleanup event=%s attempting to delete message_id=%s in chat=%s",
        event_type, msg.message_id, chat.id
    )

    # Enhanced permission check with detailed logging
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
        can_delete = getattr(bot_member, "can_delete_messages", False)
        if hasattr(bot_member, "can_delete_messages") and not can_delete:
            logger.warning(
                "service_cleanup event=%s action=skip_no_perm chat_id=%s ok=false reason=no_delete_perm",
                event_type,
                chat.id,
            )
            return False
            
        logger.info(
            "service_cleanup permission check passed: status=%s can_delete=%s", 
            status, can_delete
        )
            
    except Exception as e:
        logger.warning("service_cleanup permission check failed for chat_id=%s: %s", chat.id, e)
        return False

    # Attempt to delete with better error handling
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
        error_msg = str(e).lower()
        if "message to delete not found" in error_msg:
            logger.warning(
                "service_cleanup event=%s action=delete chat_id=%s message_id=%s ok=false reason=message_not_found",
                event_type, chat.id, msg.message_id
            )
        elif "not enough rights" in error_msg or "forbidden" in error_msg:
            logger.warning(
                "service_cleanup event=%s action=delete chat_id=%s message_id=%s ok=false reason=permission_denied",
                event_type, chat.id, msg.message_id
            )
        else:
            logger.warning(
                "service_cleanup event=%s action=delete chat_id=%s message_id=%s ok=false error=%s",
                event_type, chat.id, msg.message_id, e
            )
        return False


async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight handler that observes join/leave service updates.
    It never stops propagation and only acts when feature flag is enabled.
    """
    # Debug log para identificar que o handler foi acionado
    logger.debug("service_cleanup handler triggered, checking configuration")
    
    # Read config quickly per-update (short DB session); could be cached later
    try:
        with SessionLocal() as db:
            cfg = get_service_cleanup_config(db)
            
        logger.debug("service_cleanup config: %s", cfg)

        if not cfg.get("enabled", False):
            # disabled; do nothing
            logger.debug("service_cleanup disabled in config, ignoring update")
            return

        is_join = _is_join_message(update)
        is_leave = _is_leave_message(update)
        
        if not (is_join or is_leave):
            logger.debug("Not a join/leave message, ignoring update")
            return

        event_type = "join" if is_join else "leave"
        logger.info("service_cleanup detected %s event", event_type)

        # Record seen service message for potential retro cleanup
        try:
            msg = update.effective_message
            chat = update.effective_chat
            if msg and chat:
                logger.debug("Recording service message: chat_id=%s message_id=%s", chat.id, msg.message_id)
                with SessionLocal() as db:
                    record_service_message_seen(db, chat_id=chat.id, message_id=msg.message_id, event_type=event_type)
                    logger.debug("Service message recorded successfully")
        except Exception as e:
            logger.warning("Failed to record service message: %s", e)

        # Schedule deletion ASAP to minimize message visibility
        should_delete = (is_join and cfg.get("remove_on_join", True)) or (
            is_leave and cfg.get("remove_on_leave", True)
        )
        
        if should_delete and update.effective_message is not None:
            logger.info("Attempting to delete %s message", event_type)
            try:
                # Mudança importante: esperar a conclusão da exclusão em vez de criar uma tarefa
                # Isso garante que a operação seja concluída antes de prosseguir
                deletion_result = await _try_delete_message(context, update, event_type)
                logger.info("Message deletion result: %s", "success" if deletion_result else "failed")
            except Exception as e:
                logger.error("Error when attempting to delete message: %s", e)
                # Fallback: se falhar com exceção, tente novamente (pode ser um problema temporário)
                try:
                    deletion_result = await _try_delete_message(context, update, event_type)
                    logger.info("Fallback deletion result: %s", "success" if deletion_result else "failed")
                except Exception as e2:
                    logger.error("Fallback deletion also failed: %s", e2)

        # Forward/copy or send summary (may fail if message already deleted; we'll fallback)
        try:
            forward_result = await _send_forward_or_summary(
                context,
                forward_target_id=cfg.get("forward_target_id"),
                update=update,
                event_type=event_type,
            )
            logger.info("Forward/summary result: %s", "success" if forward_result else "failed")
        except Exception as e:
            logger.error("Error forwarding message: %s", e)
            
    except Exception as e:
        logger.error("service_cleanup handler failed: %s", e, exc_info=True)
