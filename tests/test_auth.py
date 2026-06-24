"""인증(회원가입·로그인) 테스트.

라이브 DB 없이 동작한다 — 인메모리 SQLite 로 get_db 의존성을 대체하고,
JWT 시크릿은 테스트용으로 주입한다. 다음을 검증한다:
  - security: 비밀번호 해시·검증, JWT 발급·디코드
  - 엔드포인트: 회원가입 → 로그인 → /auth/me 흐름, 중복·실패 처리
"""

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.db.session import Base, get_db
from app.main import app

# 모델을 메타데이터에 등록하기 위해 import (테이블 생성용)
from app.auth import models as _models  # noqa: F401


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    """모든 테스트에서 JWT 서명이 가능하도록 시크릿을 주입한다."""
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key")


@pytest.fixture
def client():
    """인메모리 SQLite 로 get_db 를 대체한 TestClient."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── security 유닛 테스트 ───────────────────────────────────────────


def test_password_hash_roundtrip():
    hashed = hash_password("super-secret-123")
    assert hashed != "super-secret-123"  # 평문이 그대로 저장되지 않음
    assert verify_password("super-secret-123", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_token_roundtrip():
    token = create_access_token("42")
    assert decode_access_token(token) == "42"


def test_decode_rejects_tampered_token():
    token = create_access_token("42")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token + "tampered")


# ── 엔드포인트 통합 테스트 ─────────────────────────────────────────


def _signup_payload(**over):
    base = {"name": "홍길동", "email": "hong@example.com", "password": "password123"}
    return {**base, **over}


def test_signup_returns_token_and_user(client):
    res = client.post("/auth/signup", json=_signup_payload())
    assert res.status_code == 201
    body = res.json()
    assert body["token"]
    assert body["user"] == {"id": "1", "name": "홍길동", "email": "hong@example.com"}


def test_signup_duplicate_email_conflicts(client):
    client.post("/auth/signup", json=_signup_payload())
    res = client.post("/auth/signup", json=_signup_payload(name="다른사람"))
    assert res.status_code == 409


def test_signup_validates_email_and_password(client):
    bad_email = client.post("/auth/signup", json=_signup_payload(email="not-an-email"))
    assert bad_email.status_code == 422
    short_pw = client.post("/auth/signup", json=_signup_payload(password="short"))
    assert short_pw.status_code == 422


def test_login_success(client):
    client.post("/auth/signup", json=_signup_payload())
    res = client.post(
        "/auth/login", json={"email": "hong@example.com", "password": "password123"}
    )
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "hong@example.com"


def test_login_wrong_password_unauthorized(client):
    client.post("/auth/signup", json=_signup_payload())
    res = client.post(
        "/auth/login", json={"email": "hong@example.com", "password": "wrongpass"}
    )
    assert res.status_code == 401


def test_login_unknown_email_unauthorized(client):
    res = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "password123"}
    )
    assert res.status_code == 401


def test_me_requires_token(client):
    res = client.get("/auth/me")
    assert res.status_code in {401, 403}  # 토큰 없으면 거부


def test_me_returns_current_user(client):
    signup = client.post("/auth/signup", json=_signup_payload())
    token = signup.json()["token"]
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"id": "1", "name": "홍길동", "email": "hong@example.com"}


def test_me_rejects_invalid_token(client):
    res = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert res.status_code == 401
