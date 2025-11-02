"""
Meta Conversions API (CAPI) integration for server-side Purchase events.

This module sends Purchase events to Meta's Conversions API when Stripe
confirms payment via invoice.paid webhooks.
"""
import os
import time
import hashlib
import json
import asyncio
import logging
from typing import Optional, Tuple

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger("meta_capi")

# Configuration from environment
PIXEL_ID = os.getenv("META_PIXEL_ID")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
TEST_CODE = os.getenv("META_TEST_EVENT_CODE")
DEFAULT_URL = os.getenv("SALES_SITE_URL", "https://lukamagiceurope.com")


def _sha256_norm(s: str) -> str:
    """Normalize and hash email with SHA-256."""
    if not s or not isinstance(s, str):
        return ""
    return hashlib.sha256(s.strip().lower().encode("utf-8")).hexdigest()


async def send_purchase_event(
    email: str,
    value: float,
    currency: str,
    event_time: Optional[int] = None,
    event_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    fbp: Optional[str] = None,
    fbc: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Send a Purchase event to Meta Conversions API.
    
    Args:
        email: Customer email (will be hashed with SHA-256)
        value: Purchase value in real currency (not cents)
        currency: Currency code (will be uppercased)
        event_time: Unix timestamp (defaults to current time)
        event_id: Event ID for deduplication (payment_intent or invoice ID)
        client_ip: Client IP address (optional)
        user_agent: Client user agent (optional)
        fbp: Facebook browser ID (optional)
        fbc: Facebook click ID (optional)
        source_url: Source URL of the purchase (optional)
    
    Returns:
        Tuple of (ok: bool, error_message: Optional[str])
    """
    # Check configuration
    if not PIXEL_ID or not ACCESS_TOKEN:
        return False, "meta_config_missing"
    
    if not httpx:
        return False, "httpx_not_available"
    
    # Prepare payload
    event_time_int = int(event_time or time.time())
    email_hash = _sha256_norm(email) if email else None
    
    user_data = {}
    if email_hash:
        user_data["em"] = [email_hash]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if user_agent:
        user_data["client_user_agent"] = user_agent
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc
    
    payload = {
        "data": [{
            "event_name": "Purchase",
            "event_time": event_time_int,
            "event_source_url": source_url or DEFAULT_URL,
            "action_source": "website",
            "user_data": user_data,
            "custom_data": {
                "currency": (currency or "EUR").upper(),
                "value": round(float(value or 0), 2),
            }
        }]
    }
    
    # Add event_id for deduplication if provided
    if event_id:
        payload["data"][0]["event_id"] = event_id
    
    # Add test_event_code if configured
    params = {"access_token": ACCESS_TOKEN}
    if TEST_CODE:
        payload["test_event_code"] = TEST_CODE
    
    # Send with retry logic (3 attempts with jitter)
    err = None
    async with httpx.AsyncClient(timeout=8.0) as client:
        for attempt in range(3):
            try:
                response = await client.post(
                    f"https://graph.facebook.com/v18.0/{PIXEL_ID}/events",
                    params=params,
                    json=payload
                )
                
                # If not a 5xx error, return immediately
                if response.status_code < 500:
                    ok = 200 <= response.status_code < 300
                    if not ok:
                        error_msg = f"{response.status_code}:{response.text[:200]}"
                        logger.warning(f"Meta CAPI non-2xx response: {error_msg}")
                    return ok, None if ok else error_msg
                
                # 5xx error - will retry
                err = f"{response.status_code}:{response.text[:200]}"
                logger.warning(f"Meta CAPI 5xx error (attempt {attempt + 1}/3): {err}")
                
            except Exception as e:
                err = str(e)
                logger.warning(f"Meta CAPI request exception (attempt {attempt + 1}/3): {err}")
            
            # Wait before retry (with jitter: 0.4s, 0.6s, 0.8s)
            if attempt < 2:
                await asyncio.sleep(0.4 + 0.2 * attempt)
    
    logger.error(f"Meta CAPI failed after 3 attempts: {err}")
    return False, err

