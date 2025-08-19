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
        period_end_timestamp = None
        line_description = None
        try:
            lines = (invoice.get("lines") or {}).get("data") or []
            if lines:
                price = (lines[0].get("price") or {})
                price_id = price.get("id")
                # Extract period.end for expiration date
                period = lines[0].get("period") or {}
                period_end_timestamp = period.get("end")
                # Extract description for plan type inference
                line_description = lines[0].get("description", "").lower()
        except Exception:
            price_id = None
            period_end_timestamp = None
            line_description = None

        plan_type = map_plan_from_price_id(price_id) if price_id else None
        
        # If plan_type is still None, try to infer from description
        if not plan_type and line_description:
            if "monthly" in line_description or "month" in line_description:
                plan_type = "monthly"
            elif "quarterly" in line_description or "quarter" in line_description:
                plan_type = "quarterly"
            elif "annual" in line_description or "yearly" in line_description or "year" in line_description:
                plan_type = "annual"

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
            # expires_at based on period.end from Stripe or plan_type if known
            if period_end_timestamp:
                from datetime import timezone
                sub.expires_at = datetime.fromtimestamp(period_end_timestamp, timezone.utc)
            elif plan_type == "monthly":
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
            # extend expires_at - prefer period.end from Stripe, fallback to plan-based calculation
            if period_end_timestamp:
                from datetime import timezone
                new_expires_at = datetime.fromtimestamp(period_end_timestamp, timezone.utc)
                if sub.expires_at != new_expires_at:
                    sub.expires_at = new_expires_at; changed = True
            elif plan_type == "monthly":
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
