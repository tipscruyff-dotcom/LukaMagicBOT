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


class RemovalLog(Base):
    """Log de remoções automáticas de usuários"""
    __tablename__ = "removal_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    telegram_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)  # "expired", "cancelled", etc.
    status: Mapped[str] = mapped_column(String(50), nullable=False)   # "success", "failed", "not_found", "dm_sent", etc.
    groups_removed_from: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Lista de grupos
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dm_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

Index("ix_removal_logs_email_created", RemovalLog.email, RemovalLog.created_at)
Index("ix_removal_logs_status", RemovalLog.status)


class Whitelist(Base):
    """Lista de usuários protegidos contra remoção automática"""
    __tablename__ = "whitelist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)  # Chave principal
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Opcional
    reason: Mapped[str] = mapped_column(String(255), nullable=False)  # Motivo da proteção
    added_by: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Quem adicionou
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

Index("ix_whitelist_telegram_id", Whitelist.telegram_user_id)


class NotificationLog(Base):
    """Log de notificações de expiração enviadas"""
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    telegram_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "7_days", "3_days", "1_day", "expired"
    subscription_id: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)  # Data de expiração da assinatura
    sent_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    message_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

Index("ix_notification_logs_email_type", NotificationLog.email, NotificationLog.notification_type)
Index("ix_notification_logs_subscription", NotificationLog.subscription_id, NotificationLog.notification_type)
