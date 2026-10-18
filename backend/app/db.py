import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///./nascon.db")


def init_db(url: str | None = None) -> None:
    global engine, SessionLocal
    url = url or database_url()
    kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10, "pool_timeout": 5}
    if url.startswith("sqlite"):
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    engine = create_engine(url, **kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)


def session():
    if SessionLocal is None:
        init_db()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
