"""MariaDB 연결 헬퍼 (SQLAlchemy) — mongo.py 미러.

프로세스당 엔진 하나 공유. 라우트에서 getEngine().connect() 로 쓴다.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.core.config import settings

_engine: Engine | None = None


def getEngine() -> Engine:
    """설정된 MARIADB_URL 로 엔진 생성(최초 1회) 후 재사용."""
    global _engine
    if _engine is None:
        if not settings.mariadbUrl:
            raise RuntimeError("MARIADB_URL is not configured")
        _engine = create_engine(settings.mariadbUrl, pool_pre_ping=True)
    return _engine


def disposeEngine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
