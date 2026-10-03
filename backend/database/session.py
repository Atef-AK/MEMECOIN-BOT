"""
Async SQLAlchemy session management.
Supports PostgreSQL (production) and SQLite (development).
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config.settings import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = settings.db_url

        connect_args = {}
        if settings.is_sqlite:
            connect_args["check_same_thread"] = False

        _engine = create_async_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        logger.info(f"Database engine created: {'SQLite' if settings.is_sqlite else 'PostgreSQL'}")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI: yields an async session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables and perform safe schema migrations. Call on startup."""
    from backend.database.models import Base
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migration for new columns in SQLite
        settings = get_settings()
        if settings.is_sqlite:
            for col_sql in [
                "ALTER TABLE trades ADD COLUMN strategy_name VARCHAR(64) DEFAULT 'Fast Scalper'",
                "ALTER TABLE trades ADD COLUMN exit_decision VARCHAR(256) DEFAULT ''",
            ]:
                try:
                    await conn.execute(text(col_sql))
                except Exception:
                    pass  # Column already exists
    logger.info("Database tables created/verified")


async def close_db() -> None:
    """Close database connections. Call on shutdown."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connections closed")
