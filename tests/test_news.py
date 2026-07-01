"""홈 뉴스 목록 테스트 — service·API.

라이브 DB 없이 동작한다: MariaDB 뉴스는 인메모리 SQLite 로 대체한다. 검증 범위:
  - service: 최신순 정렬·headline 회사명 제거·기사 중복 제거
  - API: GET /home/news camelCase 직렬화
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.company.models import News
from app.db.session import Base, get_db
from app.main import app
from app.news import service

# 모델을 메타데이터에 등록(테이블 생성용)
from app.company import models as _company_models  # noqa: F401


# ─── service: headline 회사명 제거 ───────────────────────────────────
def test_strip_company_prefix():
    assert service._strip_company_prefix("CJ ENM, 신규 채용 확대", "CJ ENM") == "신규 채용 확대"
    assert service._strip_company_prefix("네이버·AI 투자", "네이버") == "AI 투자"


def test_strip_company_prefix_keeps_when_nothing_left():
    assert service._strip_company_prefix("카카오", "카카오") == "카카오"


def test_strip_company_prefix_no_match():
    assert service._strip_company_prefix("업계 동향 분석", "삼성") == "업계 동향 분석"


# ─── service: 뉴스 조립 ───────────────────────────────────────────────
@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def _seed_news(db, nid, company, title, d, article_id=None):
    db.add(News(
        id=nid, article_id=article_id or nid, company=company, title=title,
        url=f"https://news/{nid}", date=d,
    ))


def test_build_news_list_latest_first_and_stripped(db):
    _seed_news(db, "n1", "CJ ENM", "CJ ENM 채용 확대", date(2026, 2, 25))
    _seed_news(db, "n2", "네이버", "네이버 신사업 발표", date(2026, 3, 1))
    db.commit()

    out = service.build_news_list(db, 10)
    items = out["items"]
    assert [i["company_tag"] for i in items] == ["네이버", "CJ ENM"]  # 최신순
    assert items[0]["headline"] == "신사업 발표"
    assert items[0]["published_at"] == "2026-03-01"


def test_build_news_list_dedups_by_article(db):
    _seed_news(db, "a_0", "회사", "기사 제목", date(2026, 3, 1), article_id="a")
    _seed_news(db, "a_1", "회사", "기사 제목", date(2026, 3, 1), article_id="a")
    db.commit()

    out = service.build_news_list(db, 10)
    assert len(out["items"]) == 1


def test_build_news_list_respects_limit(db):
    for i in range(5):
        _seed_news(db, f"n{i}", "회사", f"제목{i}", date(2026, 3, i + 1))
    db.commit()

    out = service.build_news_list(db, 2)
    assert len(out["items"]) == 2


# ─── API: 직렬화 ──────────────────────────────────────────────────────
@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_news_api_camel_case(client, db):
    _seed_news(db, "n1", "CJ ENM", "CJ ENM 채용 확대", date(2026, 2, 25))
    db.commit()
    res = client.get("/home/news?limit=5")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert "companyTag" in item and "publishedAt" in item
    assert item["companyTag"] == "CJ ENM"
