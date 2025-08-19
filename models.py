from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Boolean, func, Index
from db import Base

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    telegram_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64))
    stripe_session_id: Mapped[str | None] = mapped_column(String(64))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    plan_type: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

Index("ix_subscriptions_email_status", Subscription.email, Subscription.status)

class StripeEvent(Base):
    __tablename__ = "stripe_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class InviteLog(Base):
    __tablename__ = "invite_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    telegram_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invite_link: Mapped[str] = mapped_column(String(512), nullable=False)
    member_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_temporary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime)

Index("ix_invite_logs_email_created", InviteLog.email, InviteLog.created_at)
