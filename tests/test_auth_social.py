"""소셜 로그인(카카오·구글·네이버) 테스트.

라이브 DB·provider 없이 동작한다 — 인메모리 SQLite 로 get_db 를 대체하고,
provider 프로필 조회(oauth.fetch_profile)는 monkeypatch 로 가짜 프로필을 준다.
다음을 검증한다:
  - 신규 소셜 유저 find-or-create(첫 로그인=생성, 재로그인=동일 유저)
  - 미설정 provider(503)·미지원 provider(422)·프로필 부족(400)·통신 실패(502)
  - 이미 다른 방법으로 가입된 이메일 충돌(409)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import oauth
from app.auth.oauth import OAuthError, OAuthProfile, OAuthProfileIncomplete
from app.core.config import settings
from app.db.session import Base, get_db
from app.main import app

# 모델을 메타데이터에 등록하기 위해 import (테이블 생성용)
from app.auth import models as _models  # noqa: F401


@pytest.fixture(autouse=True)
def _configure_auth(monkeypatch):
    """JWT 서명 + 카카오 provider 설정을 주입한다(구글·네이버는 미설정으로 둔다)."""
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key")
    monkeypatch.setattr(settings, "kakao_client_id", "test-kakao-id")
    monkeypatch.setattr(settings, "kakao_client_secret", "test-kakao-secret")
    monkeypatch.setattr(
        settings, "kakao_redirect_uri", "http://localhost:3000/api/auth/callback/kakao"
    )


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


def _mock_profile(monkeypatch, profile: OAuthProfile):
    """oauth.fetch_profile 를 고정 프로필을 돌려주도록 대체한다."""
    monkeypatch.setattr(oauth, "fetch_profile", lambda *a, **k: profile)


def _mock_raises(monkeypatch, exc: Exception):
    """oauth.fetch_profile 가 주어진 예외를 던지도록 대체한다."""

    def _boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(oauth, "fetch_profile", _boom)


# ── find-or-create ────────────────────────────────────────────────


def test_social_login_creates_new_user(client, monkeypatch):
    _mock_profile(
        monkeypatch,
        OAuthProfile("kakao", "111", "new@example.com", "카카오유저"),
    )
    res = client.post("/auth/social/kakao", json={"code": "authcode"})
    assert res.status_code == 200
    body = res.json()
    assert body["token"]
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["name"] == "카카오유저"


def test_social_login_reuses_existing_user(client, monkeypatch):
    _mock_profile(
        monkeypatch,
        OAuthProfile("kakao", "111", "new@example.com", "카카오유저"),
    )
    first = client.post("/auth/social/kakao", json={"code": "code1"})
    second = client.post("/auth/social/kakao", json={"code": "code2"})
    assert first.status_code == 200 and second.status_code == 200
    # 같은 소셜 식별자면 새로 만들지 않고 동일 유저를 돌려준다(id 동일).
    assert first.json()["user"]["id"] == second.json()["user"]["id"]


# ── 실패 처리 ─────────────────────────────────────────────────────


def test_social_login_unconfigured_provider_returns_503(client, monkeypatch):
    # google 은 fixture 에서 설정하지 않았다 → 미설정.
    _mock_profile(
        monkeypatch,
        OAuthProfile("google", "gid", "g@example.com", "구글유저"),
    )
    res = client.post("/auth/social/google", json={"code": "authcode"})
    assert res.status_code == 503


def test_social_login_unknown_provider_returns_422(client):
    res = client.post("/auth/social/apple", json={"code": "authcode"})
    assert res.status_code == 422  # Literal 경로 검증 실패


def test_social_login_incomplete_profile_returns_400(client, monkeypatch):
    _mock_raises(monkeypatch, OAuthProfileIncomplete("이메일 동의가 필요합니다"))
    res = client.post("/auth/social/kakao", json={"code": "authcode"})
    assert res.status_code == 400


def test_social_login_provider_error_returns_502(client, monkeypatch):
    _mock_raises(monkeypatch, OAuthError("provider 통신 실패"))
    res = client.post("/auth/social/kakao", json={"code": "authcode"})
    assert res.status_code == 502


def test_social_login_missing_code_returns_422(client):
    res = client.post("/auth/social/kakao", json={})
    assert res.status_code == 422  # code 필수


def test_social_login_email_conflict_returns_409(client, monkeypatch):
    # 먼저 이메일로 가입한 유저와 같은 이메일을 소셜이 들고 오면 자동 연결하지 않고 409.
    client.post(
        "/auth/signup",
        json={"name": "홍길동", "email": "dup@example.com", "password": "password123"},
    )
    _mock_profile(
        monkeypatch,
        OAuthProfile("kakao", "222", "dup@example.com", "카카오유저"),
    )
    res = client.post("/auth/social/kakao", json={"code": "authcode"})
    assert res.status_code == 409
