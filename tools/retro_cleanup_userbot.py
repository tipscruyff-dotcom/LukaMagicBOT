import os
import sys
import asyncio
import argparse
from datetime import datetime, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import types as tl


def parse_iso(dt: str) -> Optional[datetime]:
    if not dt:
        return None
    try:
        if dt.endswith("Z"):
            dt = dt[:-1] + "+00:00"
        return datetime.fromisoformat(dt)
    except Exception:
        return None


def is_service_join_leave(msg) -> bool:
    if not isinstance(msg, tl.MessageService):
        return False
    act = msg.action
    return isinstance(act, (
        tl.MessageActionChatAddUser,
        tl.MessageActionChatJoinedByLink,
        tl.MessageActionChatDeleteUser,
    ))


async def run_cleanup(
    api_id: int,
    api_hash: str,
    session: Optional[str],
    chat: str,
    since: Optional[datetime],
    until: Optional[datetime],
    limit: int,
    batch: int,
    dry_run: bool,
):
    session_obj = StringSession(session) if session else None
    client = TelegramClient(session_obj or "user", api_id, api_hash)
    async with client:
        entity = await client.get_entity(chat)
        matched: List[int] = []
        count = 0
        async for msg in client.iter_messages(entity, limit=limit):
            if since and msg.date and msg.date < since:
                continue
            if until and msg.date and msg.date > until:
                continue
            if is_service_join_leave(msg):
                matched.append(msg.id)
        attempted = 0
        deleted_ok = 0
        errors = {}
        if not dry_run:
            for i in range(0, len(matched), batch):
                chunk = matched[i:i+batch]
                attempted += len(chunk)
                try:
                    await client.delete_messages(entity, chunk, revoke=True)
                    deleted_ok += len(chunk)
                except Exception as e:
                    key = str(e.__class__.__name__).lower()
                    errors[key] = errors.get(key, 0) + len(chunk)

        print("--- Retro Cleanup (userbot) ---")
        print("Chat:", chat)
        print("Matched:", len(matched))
        print("Attempted:", attempted)
        print("Deleted OK:", deleted_ok)
        print("Errors:", errors)


def main():
    p = argparse.ArgumentParser(description="Delete old Telegram service messages (join/leave) using a user account (Telethon)")
    p.add_argument("--chat", required=False, default=os.getenv("TARGET_CHAT_ID") or os.getenv("TARGET_CHAT_USERNAME"), help="Chat ID (e.g., -100123..) or @username")
    p.add_argument("--since", default=os.getenv("RC_SINCE", ""), help="ISO8601 start (e.g., 2025-01-01T00:00:00Z)")
    p.add_argument("--until", default=os.getenv("RC_UNTIL", ""), help="ISO8601 end (optional)")
    p.add_argument("--limit", type=int, default=int(os.getenv("RC_LIMIT", "5000")), help="Max messages to scan")
    p.add_argument("--batch", type=int, default=int(os.getenv("RC_BATCH", "100")), help="Delete batch size")
    p.add_argument("--dry-run", action="store_true", help="Only count, do not delete")

    args = p.parse_args()

    api_id = int(os.getenv("TG_API_ID", "0") or 0)
    api_hash = os.getenv("TG_API_HASH") or ""
    session = os.getenv("TG_SESSION")  # optional StringSession

    if not api_id or not api_hash:
        print("Missing TG_API_ID/TG_API_HASH env vars.")
        print("Get them at https://my.telegram.org/apps and set TG_API_ID, TG_API_HASH.")
        sys.exit(1)
    if not args.chat:
        print("--chat not provided and TARGET_CHAT_ID/USERNAME not set")
        sys.exit(1)

    since = parse_iso(args.since)
    until = parse_iso(args.until)

    asyncio.run(run_cleanup(
        api_id=api_id,
        api_hash=api_hash,
        session=session,
        chat=args.chat,
        since=since,
        until=until,
        limit=args.limit,
        batch=args.batch,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
