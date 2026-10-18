from __future__ import annotations
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_size=10, max_overflow=20, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """One session per request. Mutating endpoints commit explicitly (see app.idempotency.run_mutation);
    anything not committed is rolled back when the session closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
