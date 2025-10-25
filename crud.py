import os
from datetime import datetime, timedelta
import logging
from typing import Optional, Any
from sqlalchemy.orm import Session
from models import InviteLog

import models
logger = logging.getLogger(__name__)

def _build_price_plan_map() -> dict[str, str]:
    plan_lookup: dict[str, str] = {}
    env_map = {
        'monthly': os.getenv('PRICE_MONTHLY_ID') or '',
        'quarterly': os.getenv('PRICE_QUARTERLY_ID') or '',
        'annual': os.getenv('PRICE_ANNUAL_ID') or '',
    }
    for plan_name, raw_ids in env_map.items():
        for price_id in (raw_ids or '').replace(',', ';').split(';'):
            price_id = price_id.strip()
            if price_id:
                plan_lookup[price_id] = plan_name
    return plan_lookup


PRICE_PLAN_MAP = _build_price_plan_map()

def map_plan_from_price_id(price_id: str):
    if not price_id:
        return None
    return PRICE_PLAN_MAP.get(price_id.strip())

def _plan_type_from_recurring(interval: str | None, interval_count) -> Optional[str]:
    interval = (interval or '').lower()
    try:
        interval_count = int(interval_count or 1)
    except Exception:
        interval_count = 1
    if interval == 'month':
        if interval_count == 1:
            return 'monthly'
        if interval_count in (3, 4):
            return 'quarterly'
        if interval_count in (12,):
            return 'annual'
    if interval == 'year':
        return 'annual'
    return None


def infer_plan_type_from_price(price: dict | None) -> Optional[str]:
    if not isinstance(price, dict):
        return None
    recurring = price.get('recurring') or {}
    plan_type = _plan_type_from_recurring(recurring.get('interval'), recurring.get('interval_count'))
    if plan_type:
        return plan_type
    metadata = price.get('metadata') or {}
    meta_plan = (metadata.get('plan_type') or metadata.get('tier') or '').strip().lower()
    if meta_plan in {'monthly', 'quarterly', 'annual'}:
        return meta_plan
    nickname = (price.get('nickname') or '').lower()
    if 'monthly' in nickname or 'mensal' in nickname:
        return 'monthly'
    if 'quarter' in nickname or 'trimes' in nickname:
        return 'quarterly'
    if 'annual' in nickname or 'anual' in nickname or 'year' in nickname:
        return 'annual'
    return None

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

    normalized_email = email.lower().strip()
    new_id = str(telegram_user_id).strip()
    if not new_id:
        return False

    sub = db.query(models.Subscription).filter(models.Subscription.email == normalized_email).first()
    if not sub:
        return False

    current_id = getattr(sub, "telegram_user_id", None)
    if current_id:
        current_id_str = str(current_id).strip()
        if current_id_str == new_id:
            logger.info("Telegram ID already linked for email=%s", normalized_email)
            return True
        logger.warning("Telegram ID mismatch for email=%s (existing=%s, requested=%s)",
                       normalized_email, current_id_str, new_id)
        return False

    sub.telegram_user_id = new_id
    try:
        sub.updated_at = datetime.utcnow()
    except Exception:
        pass
    db.commit()
    logger.info("Telegram ID set for email=%s", normalized_email)
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
        md = session.get("metadata") or {}
        if not telegram_id and isinstance(md, dict):
            telegram_id = _digits_only(md.get("telegram_id") or "")

        plan_type_hint = None
        if isinstance(md, dict):
            raw_plan = (md.get("plan_type") or md.get("plan") or '').strip().lower()
            if raw_plan in {"monthly", "quarterly", "annual"}:
                plan_type_hint = raw_plan

        payment_status = session.get("payment_status")
        is_paid = (payment_status == "paid")
        sub_id = session.get("subscription")

        Subscription = models.Subscription
        sub = db.query(Subscription).filter(Subscription.email == email).first()

        # If Stripe metadata didn't include a plan hint, reuse existing plan
        if not plan_type_hint and sub and getattr(sub, "plan_type", None):
            plan_type_hint = sub.plan_type

        if not sub:
            sub = Subscription(
                email=email,
                full_name=full_name,
                telegram_user_id=telegram_id or None,
                stripe_subscription_id=sub_id or None,
                plan_type=plan_type_hint or "pending",  # Fallback to "pending" if not available
                status="active" if is_paid else "pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(sub)
            db.commit()
            logger.info("Created subscription (checkout.completed): email=%s status=%s tg=%s sub=%s plan=%s",
                        email, sub.status, telegram_id, sub_id, sub.plan_type)
        else:
            changed = False
            if full_name and not getattr(sub, "full_name", None):
                sub.full_name = full_name; changed = True
            if telegram_id:
                sub.telegram_user_id = telegram_id; changed = True
            if sub_id and not getattr(sub, "stripe_subscription_id", None):
                sub.stripe_subscription_id = sub_id; changed = True
            if plan_type_hint and sub.plan_type != plan_type_hint:
                sub.plan_type = plan_type_hint; changed = True
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
        price_id = None
        price: dict[str, Any] = {}
        lines = (invoice.get("lines") or {}).get("data") or []
        if lines:
            line0 = lines[0]
            raw_price = line0.get("price")
            if isinstance(raw_price, dict):
                price = raw_price
                price_id = raw_price.get("id")
            elif isinstance(raw_price, str):
                price_id = raw_price
                price = {"id": raw_price}
            price_details = (line0.get("pricing") or {}).get("price_details") or {}
            if isinstance(price_details, dict):
                if not price_id:
                    price_id = price_details.get("id") or price_details.get("price")
                if price_id and (not price or len(price) <= 1):
                    price = {**price_details, "id": price_id}
            if price_id and (not price or len(price) <= 1):
                try:
                    import stripe  # type: ignore
                    if getattr(stripe, "api_key", None):
                        fetched_price = stripe.Price.retrieve(price_id)  # type: ignore[attr-defined]
                        if isinstance(fetched_price, dict):
                            price = dict(fetched_price)
                except Exception as fetch_err:
                    logger.debug("Could not retrieve price metadata for %s: %s", price_id, fetch_err)

        plan_type = map_plan_from_price_id(price_id) if price_id else None
        if not plan_type:
            plan_type = infer_plan_type_from_price(price)
        if not plan_type and lines:
            plan_obj = lines[0].get("plan") or {}
            plan_type = _plan_type_from_recurring(plan_obj.get("interval"), plan_obj.get("interval_count"))
            if not plan_type:
                nickname = str(plan_obj.get("nickname") or "").lower()
                if "monthly" in nickname or "mensal" in nickname:
                    plan_type = "monthly"
                elif "quarter" in nickname or "trimes" in nickname:
                    plan_type = "quarterly"
                elif "annual" in nickname or "anual" in nickname or "year" in nickname:
                    plan_type = "annual"

        customer_name = (invoice.get("customer_name") or (invoice.get("customer_details") or {}).get("name") or "").strip()
        invoice_metadata = invoice.get("metadata") or {}
        telegram_meta = ""
        if isinstance(invoice_metadata, dict):
            telegram_meta = _digits_only(invoice_metadata.get("telegram_id") or "")

        Subscription = models.Subscription
        sub = None
        if email:
            sub = db.query(Subscription).filter(Subscription.email == email).first()
        if not sub and sub_id:
            sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).first()

        if sub and not plan_type and getattr(sub, "plan_type", None):
            plan_type = sub.plan_type

        if not sub:
            sub = Subscription(
                email=email or "",
                full_name=customer_name or None,
                telegram_user_id=telegram_meta or None,
                stripe_subscription_id=sub_id or None,
                plan_type=plan_type or "unknown",  # Fallback to "unknown" if not detected
                status="active",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            if plan_type == "monthly":
                sub.expires_at = datetime.utcnow() + timedelta(days=30)
            elif plan_type == "quarterly":
                sub.expires_at = datetime.utcnow() + timedelta(days=90)
            elif plan_type == "annual":
                sub.expires_at = datetime.utcnow() + timedelta(days=365)
            else:
                # Default to 30 days if plan not recognized
                sub.expires_at = datetime.utcnow() + timedelta(days=30)
                logger.warning("Unknown plan_type '%s' for %s, defaulting to 30 days", plan_type, email)
            db.add(sub)
            db.commit()
            logger.info("Created subscription (invoice.paid): email=%s plan=%s sub=%s", email, plan_type or "unknown", sub_id)
        else:
            changed = False
            if sub_id and not getattr(sub, "stripe_subscription_id", None):
                sub.stripe_subscription_id = sub_id
                changed = True
            if sub.status != "active":
                sub.status = "active"
                changed = True
            # Update plan_type if available and different (including replacing "pending" or "unknown")
            if plan_type and (sub.plan_type != plan_type or sub.plan_type in ("pending", "unknown")):
                sub.plan_type = plan_type
                changed = True
            if customer_name and not getattr(sub, "full_name", None):
                sub.full_name = customer_name
                changed = True
            if telegram_meta and not getattr(sub, "telegram_user_id", None):
                sub.telegram_user_id = telegram_meta
                changed = True
            if plan_type == "monthly":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=30)
                changed = True
            elif plan_type == "quarterly":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=90)
                changed = True
            elif plan_type == "annual":
                base = sub.expires_at or datetime.utcnow()
                sub.expires_at = max(base, datetime.utcnow()) + timedelta(days=365)
                changed = True
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
        logger.warning(f"Could not fetch removal logs: {e}")
        return []


def get_recent_removal_logs_admin(db: Session, limit: int = 30):
    """Versão otimizada para admin - busca apenas os logs mais recentes"""
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


# ======================
# Settings helpers
# ======================

DEFAULT_SETTINGS = {
    "service_cleanup.enabled": "false",
    "service_cleanup.remove_on_join": "true",
    "service_cleanup.remove_on_leave": "true",
    "service_cleanup.forward_target_id": "",
    # retroactive cleanup defaults (disabled by default)
    "service_cleanup.history_enabled": "false",
    "service_cleanup.history_scope": "all",  # all | specific
    "service_cleanup.history_group_id": "",
    "service_cleanup.history_since": "",  # ISO8601
    "service_cleanup.history_batch_size": "500",
}


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch setting from DB; if missing, fall back to environment and then defaults.

    This allows initial configuration via .env without having to pre-populate the DB.
    """
    try:
        row = db.query(models.Setting).filter_by(key=key).first()
        if row is not None:
            return row.value
        # Not in DB: check environment override (supports keys with dots via python-dotenv)
        import os
        env_val = os.getenv(key)
        if env_val is not None:
            return env_val
        return DEFAULT_SETTINGS.get(key, default)
    except Exception:
        # Table may not exist yet — still try environment before defaults
        try:
            import os
            env_val = os.getenv(key)
            if env_val is not None:
                return env_val
        except Exception:
            pass
        return DEFAULT_SETTINGS.get(key, default)


def set_setting(db: Session, key: str, value: Optional[str]) -> bool:
    try:
        row = db.query(models.Setting).filter_by(key=key).first()
        if row is None:
            row = models.Setting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to set setting {key}: {e}")
        db.rollback()
        return False


def get_service_cleanup_config(db: Session) -> dict:
    """Return normalized service cleanup config with proper types and defaults."""
    def to_bool(v: Optional[str], default: bool) -> bool:
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    enabled = to_bool(get_setting(db, "service_cleanup.enabled"), False)
    remove_on_join = to_bool(get_setting(db, "service_cleanup.remove_on_join"), True)
    remove_on_leave = to_bool(get_setting(db, "service_cleanup.remove_on_leave"), True)
    forward_target_id_raw = get_setting(db, "service_cleanup.forward_target_id", "") or ""

    # sanitize numeric id if possible
    forward_target_id = None
    try:
        forward_target_id = int(str(forward_target_id_raw).strip()) if str(forward_target_id_raw).strip() else None
    except Exception:
        forward_target_id = None

    return {
        "enabled": enabled,
        "remove_on_join": remove_on_join,
        "remove_on_leave": remove_on_leave,
        "forward_target_id": forward_target_id,
    }


# ---- Retroactive cleanup helpers ----
def get_service_cleanup_history_config(db: Session) -> dict:
    def to_bool(v: Optional[str], default: bool) -> bool:
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    hist_enabled = to_bool(get_setting(db, "service_cleanup.history_enabled"), False)
    scope = (get_setting(db, "service_cleanup.history_scope", "all") or "all").strip().lower()
    gid_raw = get_setting(db, "service_cleanup.history_group_id", "") or ""
    since = (get_setting(db, "service_cleanup.history_since", "") or "").strip()
    bsize_raw = get_setting(db, "service_cleanup.history_batch_size", "500") or "500"
    try:
        gid = int(gid_raw.strip()) if str(gid_raw).strip() else None
    except Exception:
        gid = None
    try:
        bsize = max(1, min(5000, int(str(bsize_raw).strip() or "500")))
    except Exception:
        bsize = 500
    return {
        "history_enabled": hist_enabled,
        "history_scope": scope if scope in ("all", "specific") else "all",
        "history_group_id": gid,
        "history_since": since,
        "history_batch_size": bsize,
    }


def set_service_cleanup_history_config(
    db: Session,
    *,
    history_enabled: bool,
    history_scope: str,
    history_group_id: Optional[int],
    history_since: str,
    history_batch_size: int,
) -> bool:
    ok = True
    ok &= set_setting(db, "service_cleanup.history_enabled", "true" if history_enabled else "false")
    ok &= set_setting(db, "service_cleanup.history_scope", history_scope or "all")
    ok &= set_setting(db, "service_cleanup.history_group_id", str(history_group_id or ""))
    ok &= set_setting(db, "service_cleanup.history_since", history_since or "")
    ok &= set_setting(db, "service_cleanup.history_batch_size", str(history_batch_size))
    return ok


def record_service_message_seen(db: Session, *, chat_id: int, message_id: int, event_type: str) -> None:
    from models import ServiceMessageSeen
    try:
        # unique by chat_id + message_id
        exists = (
            db.query(ServiceMessageSeen)
            .filter(ServiceMessageSeen.chat_id == chat_id, ServiceMessageSeen.message_id == message_id)
            .first()
        )
        if exists:
            return
        row = ServiceMessageSeen(chat_id=chat_id, message_id=message_id, event_type=event_type)
        db.add(row)
        db.commit()
    except Exception as e:
        logger.debug(f"record_service_message_seen failed: {e}")


def retro_delete_service_messages(
    db: Session,
    *,
    application,  # telegram Application to access bot
    scope: str = "all",
    group_id: Optional[int] = None,
    since_iso: str = "",
    batch_size: int = 500,
) -> dict:
    """Attempt to delete previously seen service messages according to the filters.
    Returns a summary dict.
    """
    from models import ServiceMessageSeen
    from telegram.constants import ChatMemberStatus
    from datetime import datetime

    summary = {
        "matched": 0,
        "attempted": 0,
        "deleted_ok": 0,
        "errors": {},
    }

    try:
        q = db.query(ServiceMessageSeen).filter(ServiceMessageSeen.deleted == False)  # noqa: E712
        if scope == "specific" and group_id:
            q = q.filter(ServiceMessageSeen.chat_id == group_id)
        if since_iso:
            try:
                since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                q = q.filter(ServiceMessageSeen.seen_at >= since_dt)
            except Exception:
                pass
        rows = q.order_by(ServiceMessageSeen.seen_at.asc()).limit(batch_size).all()
        summary["matched"] = len(rows)
        bot = application.bot
        for r in rows:
            summary["attempted"] += 1
            err_reason = None
            try:
                cm = bot.get_chat_member  # sync wrapper not allowed; use async via application
            except Exception:
                pass
            # We must use async APIs; this helper is intended to be called from a FastAPI route using loop.run_until_complete
            try:
                # Permission check
                bot_member = application.create_task(bot.get_chat_member(r.chat_id, bot.id))  # type: ignore
            except Exception:
                bot_member = None
            try:
                if bot_member:
                    bm = application.loop.run_until_complete(bot_member)  # type: ignore
                    status = getattr(bm, "status", None)
                    if status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                        err_reason = "not_admin"
                        raise RuntimeError("not_admin")
                    if hasattr(bm, "can_delete_messages") and not getattr(bm, "can_delete_messages", False):
                        err_reason = "no_delete_perm"
                        raise RuntimeError("no_delete_perm")
                # Delete
                fut = bot.delete_message(chat_id=r.chat_id, message_id=r.message_id)
                application.loop.run_until_complete(fut)  # type: ignore
                r.deleted = True
                r.delete_ok = True
                r.last_error = None
                db.commit()
                summary["deleted_ok"] += 1
                continue
            except Exception as de:
                # Determine reason
                msg = str(de).lower()
                if not err_reason:
                    if "message to delete not found" in msg or "message can't be deleted" in msg or "message to delete not found" in msg:
                        err_reason = "not_found"
                    elif "too many requests" in msg or "flood" in msg:
                        err_reason = "flood_wait"
                    else:
                        err_reason = "other"
                r.deleted = True
                r.delete_ok = False
                r.last_error = err_reason
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                summary["errors"][err_reason] = summary["errors"].get(err_reason, 0) + 1
        return summary
    except Exception as e:
        logger.error(f"retro_delete_service_messages failed: {e}")
        return summary


def set_service_cleanup_config(
    db: Session,
    *,
    enabled: bool,
    remove_on_join: bool,
    remove_on_leave: bool,
    forward_target_id: Optional[int],
) -> bool:
    ok = True
    ok &= set_setting(db, "service_cleanup.enabled", "true" if enabled else "false")
    ok &= set_setting(db, "service_cleanup.remove_on_join", "true" if remove_on_join else "false")
    ok &= set_setting(db, "service_cleanup.remove_on_leave", "true" if remove_on_leave else "false")
    ok &= set_setting(db, "service_cleanup.forward_target_id", str(forward_target_id or ""))
    return ok



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


def get_subscriptions_in_grace_period_admin(db: Session, grace_period_days: int = 3, limit: int = 50):
    """Versão otimizada para admin - APENAS para visualização com limite"""
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
        .order_by(models.Subscription.expires_at.desc())
        .limit(limit)
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


def get_subscriptions_past_grace_period_admin(db: Session, grace_period_days: int = 3, limit: int = 50):
    """Versão otimizada para admin - APENAS para visualização com limite"""
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
        .order_by(models.Subscription.expires_at.desc())
        .limit(limit)
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
