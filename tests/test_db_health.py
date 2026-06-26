"""DB 연결 배선 스모크 테스트.

라이브 DB 없이 동작한다:
  - /health/db 가 200 으로 ok/degraded 구조를 돌려주는지
  - 세션 의존성이 자원 미설정 시 명확한 에러를 내는지
검증한다.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db.mongo import get_mongo_db
from app.db.session import get_db
from app.main import app


def _request_without_db() -> SimpleNamespace:
    """app.state 에 DB 자원이 없는 가짜 요청 (미연결 환경 모사)."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def test_db_health_endpoint_returns_structure():
    """lifespan 을 태운 채 /health/db 가 정해진 구조로 응답한다."""
    with TestClient(app) as client:
        res = client.get("/health/db")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["mariadb"], bool)
    assert isinstance(body["mongodb"], bool)


def test_get_db_raises_without_factory():
    """세션 팩토리 미설정 시 get_db 가 명확한 에러를 던진다."""
    with pytest.raises(RuntimeError, match="MARIADB_URL"):
        next(get_db(_request_without_db()))


def test_get_mongo_db_raises_without_client():
    """Mongo 클라이언트 미설정 시 get_mongo_db 가 명확한 에러를 던진다."""
    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        get_mongo_db(_request_without_db())
