"""Shared pytest fixtures: in-memory SQLite async engine/session.

Production runs on PostgreSQL via asyncpg; tests use SQLite (aiosqlite) with a
single shared connection (StaticPool) so schema and data stay visible across the
session's lifetime without requiring a running database.
"""

from __future__ import annotations

import os

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.models  # noqa: F401  (ensure all models are imported for metadata)
from app.db.base import Base


@pytest_asyncio.fixture
async def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        if not url.startswith("postgresql+asyncpg://") or not url.rsplit("/", 1)[-1].startswith(
            "crm_test"
        ):
            raise RuntimeError(
                "TEST_DATABASE_URL must point to a dedicated PostgreSQL crm_test* database"
            )
        engine = create_async_engine(url, poolclass=NullPool)
        # Schema is installed with Alembic before pytest, never create_all here.
        async with engine.begin() as conn:
            tables = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
