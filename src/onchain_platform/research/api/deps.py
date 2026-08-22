"""FastAPI dependency injection for the Research API (DOC-011 research/api).

Provides:
- `get_session()` — async SQLAlchemy session per request, from Settings.
- `get_settings()` — the one Settings instance (constructed lazily at the
  composition boundary, per DOC-013 § Dependency & Composition).

Deliberately thin: no business logic here, only wiring.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from onchain_platform.platform.config import Settings

_settings: Settings | None = None
_engine: AsyncEngine | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings (lazily constructed once)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _get_engine() -> AsyncEngine:
    """Return the process-wide async engine (lazily constructed once)."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().postgres_dsn)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession each for the current request (scoped per call)."""
    async with AsyncSession(_get_engine(), expire_on_commit=False) as session:
        yield session
