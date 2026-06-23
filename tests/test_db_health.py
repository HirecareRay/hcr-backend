"""DB 연결 배선 스모크 테스트.

라이브 DB 없이 동작한다 — .env 미설정(URL 빈 값) 환경에서:
  - /health/db 가 200 으로 degraded 를 알려주는지
  - 세션 의존성이 미설정 시 명확한 에러를 내는지
검증한다.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.mongo import get_mongo_db
from app.db.session import get_db
from app.main import app

client = TestClient(app)


def test_db_health_endpoint_returns_structure():
    res = client.get("/health/db")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["mariadb"], bool)
    assert isinstance(body["mongodb"], bool)


@pytest.mark.skipif(bool(settings.mariadb_url), reason="MARIADB_URL이 설정된 환경")
def test_get_db_raises_without_url():
    """MARIADB_URL 미설정 시 get_db 가 명확한 에러를 던진다."""
    with pytest.raises(RuntimeError, match="MARIADB_URL"):
        next(get_db())


@pytest.mark.skipif(bool(settings.mongodb_uri), reason="MONGODB_URI가 설정된 환경")
def test_get_mongo_db_raises_without_uri():
    """MONGODB_URI 미설정 시 get_mongo_db 가 명확한 에러를 던진다."""
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        get_mongo_db()
