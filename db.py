import os
import pathlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lukabot.db")
connect_args = {}
if DATABASE_URL.startswith("sqlite:///"):
    sqlite_path = DATABASE_URL.replace("sqlite:///", "")
    abs_path = str(pathlib.Path(sqlite_path).resolve())
    DATABASE_URL = f"sqlite:///{abs_path}"
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
Base = declarative_base()

def db_url_info() -> str:
    return engine.url.render_as_string(hide_password=True)


def db_path_info(session=None) -> str:
    """Return the database URL with sensitive info hidden. Accepts an optional Session."""
    try:
        eng = session.get_bind() if session is not None else engine
    except Exception:
        eng = engine
    try:
        return eng.url.render_as_string(hide_password=True)
    except Exception:
        return str(eng.url)


def init_db() -> None:
    """Create database tables if they don't exist."""
    # Import models to register metadata
    import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
