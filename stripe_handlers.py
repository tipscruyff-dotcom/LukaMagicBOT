import logging
from typing import Dict, Any, Optional, Tuple
try:
    import stripe
except ImportError:
    stripe = None
from sqlalchemy.orm import Session
from crud import (
    event_already_processed,
    log_event,
    upsert_subscription_from_checkout_session,
    upsert_subscription_from_invoice,
    update_subscription_status,
    mark_telegram_id
)

logger = logging.getLogger("stripe_handlers")

def _extract_email_and_name(session: dict) -> Tuple[Optional[str], Optional[str]]:
    cd = (session or {}).get("customer_details") or {}
    email = (cd.get("email") or (session or {}).get("customer_email") or None)
    name = cd.get("name") if isinstance(cd, dict) else None
    if isinstance(email, str): email = email.strip().lower()
    if isinstance(name, str): name = name.strip()
    return email, name

def _is_valid_telegram_id(telegram_id: str) -> bool:
    """Valida se o ID do Telegram é válido (mínimo 6 dígitos)"""
    if not telegram_id:
        return False
    digits = "".join(c for c in telegram_id if c.isdigit())
    # IDs do Telegram geralmente têm 7-10 dígitos, mas vamos aceitar 6+ para ser seguro
    return len(digits) >= 6

def _extract_telegram_id_from_session(session: dict) -> Optional[str]:
    # TEXT first
    for fld in (session or {}).get("custom_fields") or []:
        key = (fld.get("key") or "").lower()
        label = ((fld.get("label") or {}).get("custom") or "").lower()
        if "telegram" in key or "telegram" in label:
            txt = fld.get("text")
            if isinstance(txt, dict):
                raw = (txt.get("value") or "")
                if isinstance(raw, str) and raw.strip():
                    d = "".join(c for c in raw if c.isdigit())
                    if d and _is_valid_telegram_id(d): return d
    # NUMERIC fallback
    for fld in (session or {}).get("custom_fields") or []:
        key = (fld.get("key") or "").lower()
        label = ((fld.get("label") or {}).get("custom") or "").lower()
        if "telegram" in key or "telegram" in label:
            num = fld.get("numeric")
            if isinstance(num, dict):
                raw = (num.get("value") or "")
                if isinstance(raw, str) and raw.strip():
                    d = "".join(c for c in raw if c.isdigit())
                    if d and _is_valid_telegram_id(d): return d
    # metadata fallback
    md = (session or {}).get("metadata") or {}
    if isinstance(md, dict):
        raw = md.get("telegram_id")
        if isinstance(raw, str) and raw.strip():
            d = "".join(c for c in raw if c.isdigit())
            if d and _is_valid_telegram_id(d): return d
    return None

def _map_stripe_status(stripe_status: str) -> str:
    status_map = {
        "active": "active",
        "trialing": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "unpaid": "canceled",
        "incomplete": "pending",
        "incomplete_expired": "canceled",
    }
    return status_map.get(stripe_status, "pending")

async def process_stripe_webhook_event(db: Session, event: dict) -> bool:
    event_id = event.get("id")
    event_type = event.get("type")
    if event_already_processed(db, event_id):
        logger.info(f"Event {event_id} already processed, skipping")
        return True
    try:
        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            mode = session.get("mode")
            if mode != "subscription":
                logger.info("Skipped non-subscription checkout session (mode=%s, id=%s)", mode, session.get("id"))
                # Enriquecimento opcional:
                email, full_name = _extract_email_and_name(session)
                tg = _extract_telegram_id_from_session(session)
                if email:
                    try:
                        from crud import update_full_name_if_empty, mark_telegram_id
                        if full_name:
                            update_full_name_if_empty(db, email, full_name)
                        if tg:
                            mark_telegram_id(db, email, tg)
                    except Exception as e:
                        logger.warning("Non-subscription enrich failed: %s", e)
                return True
            try:
                from crud import upsert_subscription_from_checkout_session
                ok = upsert_subscription_from_checkout_session(db, session)
                if not ok:
                    logger.warning("Checkout upsert returned False; invoice.paid will finalize.")
            except Exception as e:
                logger.warning("Checkout upsert raised; continuing 200 to Stripe: %s", e, exc_info=True)
            # mark processed only after successful branch handling
            try:
                log_event(db, event_id)
            except Exception as e:
                logger.warning("Failed to log processed event %s: %s", event_id, e)
            return True
        elif event_type == "invoice.paid":
            # IMPORTANTE: Processar APENAS invoice.paid para evitar duplicação
            # Stripe envia tanto "invoice.paid" quanto "invoice.payment_succeeded"
            # para o mesmo pagamento, o que causava duplicação do período
            invoice = event["data"]["object"]
            try:
                from crud import upsert_subscription_from_invoice
                ok = upsert_subscription_from_invoice(db, invoice)
                if not ok:
                    logger.warning("Invoice upsert returned False")
            except Exception as e:
                logger.warning("Invoice upsert raised; continuing 200 to Stripe: %s", e, exc_info=True)
            # idempotency: record as processed to avoid extending twice if both events arrive
            try:
                log_event(db, event_id)
            except Exception as e:
                logger.warning("Failed to log processed event %s: %s", event_id, e)
            return True
        elif event_type == "invoice.payment_succeeded":
            # IMPORTANTE: Ignorar este evento para evitar duplicação de período
            # O evento "invoice.paid" já processa o pagamento e adiciona o tempo
            # Este evento é redundante e causava o bug de dobrar o período (1 mês → 2 meses)
            logger.info(f"Skipping invoice.payment_succeeded (already processed via invoice.paid): {event_id}")
            try:
                log_event(db, event_id)  # Marcar como processado para não reprocessar
            except Exception as e:
                logger.warning("Failed to log processed event %s: %s", event_id, e)
            return True
        elif event_type == "customer.subscription.updated":
            subscription_obj = event["data"]["object"]
            stripe_sub_id = subscription_obj.get("id")
            status = subscription_obj.get("status")
            our_status = _map_stripe_status(status)
            logger.info(f"Updating subscription {stripe_sub_id}: status={our_status}")
            success = update_subscription_status(db, stripe_sub_id, our_status)
            if not success:
                logger.warning(f"Failed to update subscription status for {stripe_sub_id}")
            try:
                log_event(db, event_id)
            except Exception as e:
                logger.warning("Failed to log processed event %s: %s", event_id, e)
            return True  # Sempre retorna True para não derrubar webhook
        elif event_type == "customer.subscription.deleted":
            subscription_obj = event["data"]["object"]
            stripe_sub_id = subscription_obj.get("id")
            logger.info(f"Subscription deleted: {stripe_sub_id}")
            success = update_subscription_status(db, stripe_sub_id, "canceled")
            if not success:
                logger.warning(f"Failed to update subscription status for {stripe_sub_id}")
            try:
                log_event(db, event_id)
            except Exception as e:
                logger.warning("Failed to log processed event %s: %s", event_id, e)
            return True  # Sempre retorna True para não derrubar webhook
        else:
            logger.info(f"Unhandled event type: {event_type}")
    except Exception as e:
        logger.error(f"Error processing event {event_id} ({event_type}): {e}", exc_info=True)
    return False
