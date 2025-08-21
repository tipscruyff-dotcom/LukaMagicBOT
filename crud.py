import os
from datetime import datetime, timedelta
import logging
from typing import Optional
from sqlalchemy.orm import Session
from models import InviteLog

import models
logger = logging.getLogger(__name__)

PRICE_PLAN_MAP = {
    (os.getenv("PRICE_MONTHLY_ID") or "").strip(): "monthly",
    (os.getenv("PRICE_QUARTERLY_ID") or "").strip(): "quarterly",
    (os.getenv("PRICE_ANNUAL_ID") or "").strip(): "annual",
}
PRICE_PLAN_MAP = {k: v for k, v in PRICE_PLAN_MAP.items() if k}

def map_plan_from_price_id(price_id: str):
    if not price_id:
        return None
    return PRICE_PLAN_MAP.get(price_id.strip())

def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def event_already_processed(db, event_id: str) -> bool:
    """Verifica se evento Stripe já foi processado (idempotência)."""
    return db.query(models.StripeEvent).filter_by(event_id=event_id).first() is not None

def log_event(db, event_id: str) -> None:
    """Registra evento Stripe como processado."""
    db.add(models.StripeEvent(event_id=event_id))
    db.commit()

def get_active_by_email(db, email: str):
    """Busca assinatura ativa por email."""
    return db.query(models.Subscription).filter_by(email=email.lower().strip(), status="active").first()

def get_active_and_not_expired_by_email(db, email: str):
    """Active AND not expired (expires_at is null OR expires_at >= now)."""
    now = datetime.utcnow()
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.email == email.lower().strip(),
            models.Subscription.status == "active",
            ((models.Subscription.expires_at == None) | (models.Subscription.expires_at >= now)),
        )
        .first()
    )

def get_subscription_by_email(db, email: str):
    """Return any subscription record by email regardless of status."""
    return db.query(models.Subscription).filter(models.Subscription.email == email.lower().strip()).first()

def update_full_name_if_empty(db, email: str, full_name: str) -> bool:
    if not email or not full_name:
        return False
    sub = db.query(models.Subscription).filter(models.Subscription.email == email.lower().strip()).first()
    if not sub:
        return False
    if getattr(sub, "full_name", None):
        return False
    sub.full_name = full_name
    try:
        sub.updated_at = datetime.utcnow()
    except Exception:
        pass
    db.commit()
    logger.info("Full name set for email=%s", email)
    return True

def mark_telegram_id(db, email: str, telegram_user_id: str) -> bool:
    if not email or not telegram_user_id:
        return False
    sub = db.query(models.Subscription).filter(models.Subscription.email == email.lower().strip()).first()
    if not sub:
        return False
    sub.telegram_user_id = telegram_user_id
    try:
        sub.updated_at = datetime.utcnow()
    except Exception:
        pass
    db.commit()
    logger.info("Telegram ID set for email=%s", email)
    return True

def upsert_subscription_from_checkout_session(db, session: dict) -> bool:
    """
    Create/update subscription from checkout.session with minimal info:
    email, full_name, telegram_user_id, stripe_subscription_id, status.
    Do not extend expires_at here (invoice.paid will do that).
    """
    try:
        if not isinstance(session, dict):
            logger.warning("checkout.session is not a dict")
            return False

        cd = session.get("customer_details") or {}
        email = (cd.get("email") or session.get("customer_email") or "").strip().lower()
        full_name = (cd.get("name") or None)
        if not email:
            logger.warning("checkout.session without email; skipping upsert")
            return False

        # Telegram ID: prefer custom_fields.text.value, fallback numeric.value, fallback metadata.telegram_id
        telegram_id = None
        for fld in (session.get("custom_fields") or []):
            key = (fld.get("key") or "").lower()
            label = ((fld.get("label") or {}).get("custom") or "").lower()
            if "telegram" in key or "telegram" in label:
                if isinstance(fld.get("text"), dict):
                    telegram_id = _digits_only(fld["text"].get("value") or "")
                    if telegram_id:
                        break
                if isinstance(fld.get("numeric"), dict) and not telegram_id:
                    telegram_id = _digits_only(fld["numeric"].get("value") or "")
                    if telegram_id:
                        break
        if not telegram_id:
            md = session.get("metadata") or {}
            if isinstance(md, dict):
                telegram_id = _digits_only(md.get("telegram_id") or "")

        payment_status = session.get("payment_status")
        is_paid = (payment_status == "paid")
        sub_id = session.get("subscription")

        Subscription = models.Subscription
        sub = db.query(Subscription).filter(Subscription.email == email).first()

        if not sub:
            sub = Subscription(
                email=email,
                full_name=full_name,
                telegram_user_id=telegram_id or None,
                stripe_subscription_id=sub_id or None,
                plan_type=None,  # set later by invoice.paid when price.id known
                status="active" if is_paid else "pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(sub)
            db.commit()
            logger.info("Created subscription (checkout.completed): email=%s status=%s tg=%s sub=%s",
                        email, sub.status, telegram_id, sub_id)
        else:
            changed = False
            if full_name and not getattr(sub, "full_name", None):
                sub.full_name = full_name; changed = True
            if telegram_id:
                sub.telegram_user_id = telegram_id; changed = True
            if sub_id and not getattr(sub, "stripe_subscription_id", None):
                sub.stripe_subscription_id = sub_id; changed = True
            if is_paid and sub.status != "active":
                sub.status = "active"; changed = True
            if changed:
                sub.updated_at = datetime.utcnow()
                db.commit()
                logger.info("Updated subscription (checkout.completed): email=%s status=%s tg=%s sub=%s",
                            email, sub.status, telegram_id, sub_id)
        return True
    except Exception as e:
        logger.warning("upsert_subscription_from_checkout_session failed: %s", e, exc_info=True)
        return False

def upsert_subscription_from_invoice(db, invoice: dict) -> bool:
    """
    Make/keep subscription active from invoice, set plan_type via price.id when available,
    and extend expires_at accordingly (30/90/365 days). Always commit + log.
    """
    try:
        if not isinstance(invoice, dict):
            logger.warning("invoice is not a dict")
            return False

        email = (invoice.get("customer_email") or "").strip().lower()
        sub_id = invoice.get("subscription")
        # Try price.id from expanded lines if available
        price_id = None
        try:
            lines = (invoice.get("lines") or {}).get("data") or []
            if lines:
                price = (lines[0].get("price") or {})
                price_id = price.get("id")
        except Exception:
            price_id = None

        plan_type = map_plan_from_price_id(price_id) if price_id else None

        Subscription = models.Subscription
        sub = None
        if email:
            sub = db.query(Subscription).filter(Subscription.email == email).first()
        # fallback by stripe_subscription_id if email missing
        if not sub and sub_id:
            sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).first()

        if not sub:
            # create minimal if nothing exists
            sub = Subscription(
                email=email or "",
                stripe_subscription_id=sub_id or None,
                plan_type=plan_type,
                status="active",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            # expires_at based on plan_type if known
            if plan_type == "monthly":
                sub.expires_at = datetime.utcnow() + timedelta(days=30)
            elif plan_type == "quarterly":
                sub.expires_at = datetime.utcnow() + timedelta(days=90)
            elif plan_type == "annual":
                sub.expires_at = datetime.utcnow() + timedelta(days=365)
            db.add(sub)
            db.commit()
            logger.info("Created subscription (invoice.paid): email=%s plan=%s sub=%s", email, plan_type, sub_id)
        else:
            changed = False
            # set sub id
            if sub_id and not getattr(sub, "stripe_subscription_id", None):
                sub.stripe_subscription_id = sub_id; changed = True
            # status active
            if sub.status != "active":
                sub.status = "active"; changed = True
            # set/keep plan_type
            if plan_type and sub.plan_type != plan_type:
                sub.plan_type = plan_type; changed = True
            # extend expires_at
            if plan_type == "monthly":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=30); changed = True
            elif plan_type == "quarterly":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=90); changed = True
            elif plan_type == "annual":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=365); changed = True

            if changed:
                sub.updated_at = datetime.utcnow()
                db.commit()
                logger.info("Updated subscription (invoice.paid): email=%s plan=%s sub=%s exp=%s",
                            email, plan_type, sub_id, sub.expires_at)
        return True
    except Exception as e:
        logger.warning("upsert_subscription_from_invoice failed: %s", e, exc_info=True)
        return False

# ======================
# Invite control helpers
# ======================

def get_recent_invite_for_email(db, email: str, cooldown_seconds: int) -> Optional[models.InviteLog]:
    """Return the most recent invite for this email within the cooldown window, if any."""
    threshold = datetime.utcnow() - timedelta(seconds=cooldown_seconds)
    return (
        db.query(models.InviteLog)
        .filter(models.InviteLog.email == email.lower().strip(), models.InviteLog.created_at >= threshold)
        .order_by(models.InviteLog.created_at.desc())
        .first()
    )


def get_recent_invite_for_user(db, telegram_user_id: str, cooldown_seconds: int) -> Optional[models.InviteLog]:
    """Return the most recent invite for this telegram_user_id within the cooldown window, if any."""
    threshold = datetime.utcnow() - timedelta(seconds=cooldown_seconds)
    return (
        db.query(models.InviteLog)
        .filter(models.InviteLog.telegram_user_id == str(telegram_user_id), models.InviteLog.created_at >= threshold)
        .order_by(models.InviteLog.created_at.desc())
        .first()
    )


def log_invite(
    db: Session,
    *,
    email: str,
    telegram_user_id: Optional[str],
    invite_link: str,
    expires_at: Optional[datetime],
    member_limit: int = 1,
    is_temporary: bool = True,
) -> InviteLog:
    entry = InviteLog(
        email=email.lower().strip(),
        telegram_user_id=telegram_user_id,
        invite_link=invite_link,
        member_limit=member_limit,
        is_temporary=is_temporary,
        expires_at=expires_at,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    logger.info(
        "Invite logged: email=%s, user=%s, expires_at=%s, temporary=%s",
        email,
        telegram_user_id,
        expires_at,
        is_temporary,
    )
    return entry


def update_subscription_status(db: Session, stripe_subscription_id: str, status: str) -> bool:
    """Atualiza o status de uma assinatura pelo stripe_subscription_id."""
    try:
        subscription = db.query(models.Subscription).filter_by(
            stripe_subscription_id=stripe_subscription_id
        ).first()
        
        if subscription:
            subscription.status = status
            subscription.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Updated subscription {stripe_subscription_id} status to {status}")
            return True
        else:
            logger.warning(f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error updating subscription status: {e}")
        db.rollback()
        return False


# ======================
# 🚫 Auto Removal Functions
# ======================

def get_expired_subscriptions(db: Session):
    """Buscar assinaturas expiradas que ainda estão ativas"""
    from datetime import datetime
    now = datetime.utcnow()
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.expires_at < now,
            models.Subscription.status == "active"
        )
        .all()
    )


def get_cancelled_subscriptions(db: Session):
    """Buscar assinaturas canceladas que ainda não foram processadas"""
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.status.in_(["cancelled", "canceled"])
        )
        .all()
    )


def is_whitelisted(db: Session, email: str = None, telegram_user_id: str = None) -> bool:
    """Verificar se usuário está na whitelist (por email ou telegram_user_id)"""
    try:
        if telegram_user_id:
            # Primary method: check by telegram_user_id
            return db.query(models.Whitelist).filter_by(telegram_user_id=telegram_user_id).first() is not None
        elif email:
            # Fallback: check by email (for backward compatibility)
            return db.query(models.Whitelist).filter_by(email=email.lower().strip()).first() is not None
        else:
            return False
    except Exception as e:
        # Table doesn't exist yet - assume not whitelisted
        logger.warning(f"Whitelist table doesn't exist yet: {e}")
        return False


def add_to_whitelist(db: Session, telegram_user_id: str, reason: str, added_by: str = "admin", email: str = None) -> bool:
    """Adicionar usuário à whitelist por Telegram ID"""
    try:
        telegram_user_id = telegram_user_id.strip()
        
        # Verificar se já existe
        existing = db.query(models.Whitelist).filter_by(telegram_user_id=telegram_user_id).first()
        if existing:
            logger.warning(f"Telegram ID {telegram_user_id} already in whitelist")
            return False
        
        whitelist_entry = models.Whitelist(
            telegram_user_id=telegram_user_id,
            email=email.lower().strip() if email else None,
            reason=reason,
            added_by=added_by
        )
        db.add(whitelist_entry)
        db.commit()
        logger.info(f"Added Telegram ID {telegram_user_id} to whitelist: {reason}")
        return True
    except Exception as e:
        logger.error(f"Error adding to whitelist: {e}")
        db.rollback()
        return False


def remove_from_whitelist(db: Session, telegram_user_id: str) -> bool:
    """Remover usuário da whitelist por Telegram ID"""
    try:
        telegram_user_id = telegram_user_id.strip()
        whitelist_entry = db.query(models.Whitelist).filter_by(telegram_user_id=telegram_user_id).first()
        if whitelist_entry:
            db.delete(whitelist_entry)
            db.commit()
            logger.info(f"Removed Telegram ID {telegram_user_id} from whitelist")
            return True
        return False
    except Exception as e:
        logger.error(f"Error removing from whitelist: {e}")
        db.rollback()
        return False


def log_removal_attempt(
    db: Session,
    email: str,
    telegram_user_id: str = None,
    reason: str = "expired",
    status: str = "pending",
    groups_removed_from: list = None,
    error_message: str = None,
    dm_sent: bool = False
) -> models.RemovalLog:
    """Registrar tentativa de remoção"""
    try:
        groups_str = ",".join(map(str, groups_removed_from)) if groups_removed_from else None
        
        removal_log = models.RemovalLog(
            email=email.lower().strip(),
            telegram_user_id=telegram_user_id,
            reason=reason,
            status=status,
            groups_removed_from=groups_str,
            error_message=error_message,
            dm_sent=dm_sent
        )
        db.add(removal_log)
        db.commit()
        logger.info(f"Logged removal attempt for {email}: {status}")
        return removal_log
    except Exception as e:
        # Table doesn't exist yet - log to console instead
        logger.warning(f"RemovalLog table doesn't exist yet, logging to console: {email} - {status}")
        logger.error(f"Error logging removal attempt: {e}")
        return None


def update_removal_log(
    db: Session,
    log_id: int,
    status: str = None,
    groups_removed_from: list = None,
    error_message: str = None,
    dm_sent: bool = None
) -> bool:
    """Atualizar log de remoção"""
    try:
        removal_log = db.query(models.RemovalLog).filter_by(id=log_id).first()
        if not removal_log:
            return False
        
        if status:
            removal_log.status = status
        if groups_removed_from is not None:
            removal_log.groups_removed_from = ",".join(map(str, groups_removed_from))
        if error_message:
            removal_log.error_message = error_message
        if dm_sent is not None:
            removal_log.dm_sent = dm_sent
            
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating removal log: {e}")
        db.rollback()
        return False


def get_recent_removal_logs(db: Session, limit: int = 100):
    """Buscar logs recentes de remoção"""
    try:
        return (
            db.query(models.RemovalLog)
            .order_by(models.RemovalLog.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as e:
        # Table doesn't exist yet - return empty list
        logger.warning(f"RemovalLog table doesn't exist yet: {e}")
        return []


def mark_subscription_processed(db: Session, subscription_id: int, new_status: str = "processed") -> bool:
    """Marcar assinatura como processada"""
    try:
        subscription = db.query(models.Subscription).filter_by(id=subscription_id).first()
        if subscription:
            subscription.status = new_status
            subscription.updated_at = datetime.utcnow()
            db.commit()
            return True
        return False
    except Exception as e:
        logger.error(f"Error marking subscription as processed: {e}")
        db.rollback()
        return False


# ======================
# 📱 Notification System Functions
# ======================

def get_subscriptions_expiring_in_days(db: Session, days: int):
    """Buscar assinaturas que expiram em X dias"""
    from datetime import datetime, timedelta
    
    # Calculate target date range
    now = datetime.utcnow()
    target_date_start = now + timedelta(days=days)
    target_date_end = target_date_start + timedelta(hours=23, minutes=59, seconds=59)
    
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.status == "active",
            models.Subscription.expires_at >= target_date_start,
            models.Subscription.expires_at <= target_date_end,
            models.Subscription.telegram_user_id.isnot(None)
        )
        .all()
    )


def get_subscriptions_in_grace_period(db: Session, grace_period_days: int = 3):
    """Buscar assinaturas expiradas mas ainda no grace period"""
    from datetime import datetime, timedelta
    
    now = datetime.utcnow()
    grace_cutoff = now - timedelta(days=grace_period_days)
    
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.status == "active",
            models.Subscription.expires_at < now,  # Expirada
            models.Subscription.expires_at >= grace_cutoff,  # Mas ainda no grace period
            models.Subscription.telegram_user_id.isnot(None)
        )
        .all()
    )


def get_subscriptions_past_grace_period(db: Session, grace_period_days: int = 3):
    """Buscar assinaturas que passaram do grace period (devem ser removidas)"""
    from datetime import datetime, timedelta
    
    now = datetime.utcnow()
    grace_cutoff = now - timedelta(days=grace_period_days)
    
    return (
        db.query(models.Subscription)
        .filter(
            models.Subscription.status == "active",
            models.Subscription.expires_at < grace_cutoff,  # Expirada há mais de X dias
            models.Subscription.telegram_user_id.isnot(None)
        )
        .all()
    )


def has_notification_been_sent(db: Session, subscription_id: int, notification_type: str) -> bool:
    """Verificar se notificação já foi enviada para esta assinatura"""
    try:
        return (
            db.query(models.NotificationLog)
            .filter_by(
                subscription_id=subscription_id,
                notification_type=notification_type
            )
            .first() is not None
        )
    except Exception as e:
        # Table doesn't exist yet - assume not sent
        logger.warning(f"NotificationLog table doesn't exist yet: {e}")
        return False


def log_notification(
    db: Session,
    email: str,
    telegram_user_id: str,
    notification_type: str,
    subscription_id: int,
    expires_at: datetime,
    message_sent: bool = True,
    error_message: str = None
) -> bool:
    """Registrar notificação enviada"""
    try:
        notification_log = models.NotificationLog(
            email=email.lower().strip(),
            telegram_user_id=telegram_user_id,
            notification_type=notification_type,
            subscription_id=subscription_id,
            expires_at=expires_at,
            message_sent=message_sent,
            error_message=error_message
        )
        db.add(notification_log)
        db.commit()
        logger.info(f"Logged notification for {email}: {notification_type}")
        return True
    except Exception as e:
        # Table doesn't exist yet - log to console
        logger.warning(f"NotificationLog table doesn't exist, logging to console: {email} - {notification_type}")
        logger.error(f"Error logging notification: {e}")
        return False


def get_recent_notifications(db: Session, limit: int = 100):
    """Buscar notificações recentes"""
    try:
        return (
            db.query(models.NotificationLog)
            .order_by(models.NotificationLog.sent_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as e:
        # Table doesn't exist yet - return empty list
        logger.warning(f"NotificationLog table doesn't exist yet: {e}")
        return []


# ======================
# 🧹 Database Cleanup Functions
# ======================

def cleanup_old_stripe_events(db: Session, days_old: int = 30) -> int:
    """Limpar eventos Stripe antigos"""
    from datetime import datetime, timedelta
    
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        old_events = (
            db.query(models.StripeEvent)
            .filter(models.StripeEvent.received_at < cutoff_date)
            .all()
        )
        
        count = len(old_events)
        if count > 0:
            for event in old_events:
                db.delete(event)
            db.commit()
            logger.info(f"🧹 Cleaned {count} old Stripe events (older than {days_old} days)")
        
        return count
        
    except Exception as e:
        logger.error(f"Error cleaning old Stripe events: {e}")
        db.rollback()
        return 0


def cleanup_old_invite_logs(db: Session, days_old: int = 7) -> int:
    """Limpar logs de convites antigos"""
    from datetime import datetime, timedelta
    
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        old_invites = (
            db.query(models.InviteLog)
            .filter(models.InviteLog.created_at < cutoff_date)
            .all()
        )
        
        count = len(old_invites)
        if count > 0:
            for invite in old_invites:
                db.delete(invite)
            db.commit()
            logger.info(f"🧹 Cleaned {count} old invite logs (older than {days_old} days)")
        
        return count
        
    except Exception as e:
        logger.error(f"Error cleaning old invite logs: {e}")
        db.rollback()
        return 0


def cleanup_old_removal_logs(db: Session, days_old: int = 30) -> int:
    """Limpar logs de remoção antigos"""
    from datetime import datetime, timedelta
    
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        old_removals = (
            db.query(models.RemovalLog)
            .filter(models.RemovalLog.created_at < cutoff_date)
            .all()
        )
        
        count = len(old_removals)
        if count > 0:
            for removal in old_removals:
                db.delete(removal)
            db.commit()
            logger.info(f"🧹 Cleaned {count} old removal logs (older than {days_old} days)")
        
        return count
        
    except Exception as e:
        logger.error(f"Error cleaning old removal logs: {e}")
        db.rollback()
        return 0


def cleanup_old_notification_logs(db: Session, days_old: int = 30) -> int:
    """Limpar logs de notificação antigos"""
    from datetime import datetime, timedelta
    
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        old_notifications = (
            db.query(models.NotificationLog)
            .filter(models.NotificationLog.sent_at < cutoff_date)
            .all()
        )
        
        count = len(old_notifications)
        if count > 0:
            for notification in old_notifications:
                db.delete(notification)
            db.commit()
            logger.info(f"🧹 Cleaned {count} old notification logs (older than {days_old} days)")
        
        return count
        
    except Exception as e:
        logger.error(f"Error cleaning old notification logs: {e}")
        db.rollback()
        return 0


def get_database_stats(db: Session) -> dict:
    """Obter estatísticas do banco de dados"""
    try:
        stats = {
            'subscriptions': db.query(models.Subscription).count(),
            'stripe_events': db.query(models.StripeEvent).count(),
            'invite_logs': db.query(models.InviteLog).count(),
        }
        
        # Try new tables
        try:
            stats['removal_logs'] = db.query(models.RemovalLog).count()
        except:
            stats['removal_logs'] = 'N/A'
            
        try:
            stats['whitelist'] = db.query(models.Whitelist).count()
        except:
            stats['whitelist'] = 'N/A'
            
        try:
            stats['notification_logs'] = db.query(models.NotificationLog).count()
        except:
            stats['notification_logs'] = 'N/A'
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        return {}
