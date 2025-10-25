import types
import asyncio
import pytest
from datetime import datetime

from telegram import Update, Message, Chat, User
from telegram.ext import ContextTypes

from service_cleanup import _is_join_message, _is_leave_message


def make_message(update_type: str):
    chat = Chat(id=-100123, type="supergroup")
    user = User(id=111, first_name="Test", is_bot=False)
    now = datetime.utcnow()
    if update_type == "join":
        msg = Message(message_id=5, date=now, chat=chat, from_user=user, new_chat_members=[user])
    elif update_type == "leave":
        msg = Message(message_id=6, date=now, chat=chat, from_user=user, left_chat_member=user)
    else:
        msg = Message(message_id=7, date=now, chat=chat, from_user=user, text="hi")
    return Update(update_id=1, message=msg)


def test_is_join_message_true():
    upd = make_message("join")
    assert _is_join_message(upd) is True
    assert _is_leave_message(upd) is False


def test_is_leave_message_true():
    upd = make_message("leave")
    assert _is_leave_message(upd) is True
    assert _is_join_message(upd) is False


def test_is_join_leave_false_for_normal_message():
    upd = make_message("normal")
    assert _is_join_message(upd) is False
    assert _is_leave_message(upd) is False
