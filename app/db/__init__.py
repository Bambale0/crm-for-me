"""Database engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def create_engine(url: str | None = None):
    """Create an async engine for the given (or configured) URL."""
    url = url or get_settings().database_url
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Module-level defaults, overridden in tests.
engine = create_engine()
session_factory = create_session_factory(engine)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-style dependency yielding an async session."""
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope():
    """Async session with commit/rollback semantics for bot handlers."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
