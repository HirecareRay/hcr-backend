"""인기기업 순위(trending) 테스트 — repository·service·API.

라이브 DB 없이 동작한다: MariaDB 카운터는 인메모리 SQLite, MongoDB 메타는
mongomock 으로 대체한다. 검증 범위:
  - repository: 조회수 증가(신규/기존)·최근 N 일 윈도우 합산·동률 결정적 정렬
  - service: 순위 보강·콜드스타트 폴백·이름 없는 회사 제외·로고 파생
  - API: GET /rankings/trending camelCase 직렬화·limit 반영
"""

from datetime import date, timedelta

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.mongo import get_mongo_db
from app.db.session import Base, get_db
from app.main import app
from app.ranking import repository, service
from app.ranking.models import CompanyViewDaily

# 모델을 메타데이터에 등록(테이블 생성용)
from app.ranking import models as _ranking_models  # noqa: F401

# 유효한 24자 ObjectId 문자열들(회사 id)
CID_A = "a" * 24
CID_B = "b" * 24
CID_C = "c" * 24


@pytest.fixture
def session_factory():
    """인메모리 SQLite 세션 팩토리(테스트마다 새 엔진). StaticPool 이라 같은 연결을
    공유해 여러 세션이 같은 데이터를 본다 — record_view 가 새 세션을 열어도 검증 가능.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(session_factory):
    """인메모리 SQLite 세션(테스트마다 새로 생성)."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def mongo():
    """mongomock 인메모리 DB(회사 메타)."""
    return mongomock.MongoClient().hcr_test


def _seed_company(
    mongo,
    cid: str,
    name: str,
    industry: str = "미디어",
    website_url: str | None = None,
    logo_url: str | None = None,
) -> None:
    doc = {"_id": ObjectId(cid), "company_name": name, "industry": industry}
    if website_url is not None:
        doc["website_url"] = website_url
    if logo_url is not None:
        doc["logo_url"] = logo_url
    mongo["companies"].insert_one(doc)


# ─── repository: 조회수 카운터 ────────────────────────────────────────
def test_increment_view_creates_then_increments(db):
    today = date(2026, 6, 30)
    repository.increment_view(db, CID_A, today)
    repository.increment_view(db, CID_A, today)
    db.commit()

    row = db.execute(
        select(CompanyViewDaily).where(CompanyViewDaily.company_id == CID_A)
    ).scalar_one()
    assert row.view_count == 2
    assert row.view_date == today


def test_top_company_views_orders_by_total_desc(db):
    today = date(2026, 6, 30)
    for _ in range(3):
        repository.increment_view(db, CID_A, today)
    for _ in range(5):
        repository.increment_view(db, CID_B, today)
    db.commit()

    rows = repository.top_company_views(db, since=today, limit=10)
    assert rows == [(CID_B, 5), (CID_A, 3)]


def test_top_company_views_window_excludes_old(db):
    today = date(2026, 6, 30)
    old = today - timedelta(days=10)
    repository.increment_view(db, CID_A, old)      # 윈도우 밖
    repository.increment_view(db, CID_B, today)    # 윈도우 안
    db.commit()

    since = today - timedelta(days=6)  # 최근 7일
    rows = repository.top_company_views(db, since=since, limit=10)
    assert rows == [(CID_B, 1)]


def test_top_company_views_sums_across_days(db):
    today = date(2026, 6, 30)
    repository.increment_view(db, CID_A, today)
    repository.increment_view(db, CID_A, today - timedelta(days=1))
    repository.increment_view(db, CID_A, today - timedelta(days=2))
    db.commit()

    since = today - timedelta(days=6)
    rows = repository.top_company_views(db, since=since, limit=10)
    assert rows == [(CID_A, 3)]


def test_top_company_views_tie_break_is_deterministic(db):
    today = date(2026, 6, 30)
    # 동률(각 1회) — company_id 오름차순으로 안정 정렬돼야 한다
    for cid in (CID_C, CID_A, CID_B):
        repository.increment_view(db, cid, today)
    db.commit()

    rows = repository.top_company_views(db, since=today, limit=10)
    assert [cid for cid, _ in rows] == [CID_A, CID_B, CID_C]


# ─── service: 순위 조립 ───────────────────────────────────────────────
def test_get_trending_enriches_and_ranks(db, mongo):
    today = date.today()
    for _ in range(2):
        repository.increment_view(db, CID_A, today)
    for _ in range(5):
        repository.increment_view(db, CID_B, today)
    db.commit()
    _seed_company(mongo, CID_A, "회사에이", "미디어")
    _seed_company(mongo, CID_B, "회사비", "금융")

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert [c["rank"] for c in cards] == [1, 2]
    assert cards[0]["company_id"] == CID_B   # 조회수 많은 쪽이 1위
    assert cards[0]["name"] == "회사비"
    assert cards[0]["parent_name"] == "금융"
    assert cards[0]["logo_text"] == "회사비"[:2].upper()
    assert cards[0]["logo_color"].startswith("#") and len(cards[0]["logo_color"]) == 7


def test_get_trending_cold_start_falls_back_to_seed(db, mongo):
    # 조회수 데이터 0건 → 회사 시드로 폴백(빈 피드 방지). 시드가 1개뿐이라 1개.
    _seed_company(mongo, CID_A, "시드회사")
    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert len(cards) == 1
    assert cards[0]["rank"] == 1
    assert cards[0]["company_id"] == CID_A


def test_get_trending_pads_partial_data_with_seed(db, mongo):
    # 실집계가 limit 보다 적으면 시드로 부족분을 채워 limit 개수를 맞춘다.
    today = date.today()
    repository.increment_view(db, CID_A, today)  # 실데이터 1개
    db.commit()
    _seed_company(mongo, CID_A, "실데이터회사")
    _seed_company(mongo, CID_B, "시드비")
    _seed_company(mongo, CID_C, "시드씨")

    cards = service.get_trending(db, mongo, limit=3, window_days=7)
    assert len(cards) == 3
    assert [c["rank"] for c in cards] == [1, 2, 3]      # rank 연속 재부여
    assert cards[0]["company_id"] == CID_A              # 실데이터가 먼저
    ids = [c["company_id"] for c in cards]
    assert len(set(ids)) == 3                           # company_id 중복 없음


def test_get_trending_pad_dedups_seed_overlap(db, mongo):
    # 실데이터 회사가 시드에도 있으면 시드 패딩에서 중복 제거된다.
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(mongo, CID_A, "겹치는회사")  # 실데이터이자 시드
    _seed_company(mongo, CID_B, "시드비")

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    ids = [c["company_id"] for c in cards]
    assert ids == [CID_A, CID_B]              # CID_A 가 두 번 들어가지 않음
    assert [c["rank"] for c in cards] == [1, 2]


def test_get_trending_skips_company_missing_meta(db, mongo):
    today = date.today()
    repository.increment_view(db, CID_A, today)
    repository.increment_view(db, CID_B, today)
    db.commit()
    _seed_company(mongo, CID_B, "회사비")  # CID_A 메타 없음 → 제외, rank 1부터 다시

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert len(cards) == 1
    assert cards[0]["company_id"] == CID_B
    assert cards[0]["rank"] == 1


# ─── service: 로고 URL 산출 ───────────────────────────────────────────
def test_get_trending_logo_url_prefers_curated(db, mongo):
    # 큐레이션 logo_url 이 있으면 website_url 과 무관하게 그대로 쓴다
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(
        mongo,
        CID_A,
        "회사에이",
        website_url="https://www.cj.net",
        logo_url="https://cdn.example.com/a.png",
    )

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert cards[0]["logo_url"] == "https://cdn.example.com/a.png"


def test_get_trending_logo_url_auto_from_website(db, mongo):
    # 큐레이션 없으면 website_url 도메인으로 Google favicon URL 을 만든다
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(mongo, CID_A, "회사에이", website_url="https://www.cj.net")

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert (
        cards[0]["logo_url"]
        == "https://www.google.com/s2/favicons?domain=cj.net&sz=128"
    )


def test_get_trending_logo_url_none_when_no_source(db, mongo):
    # logo_url·website_url 둘 다 없으면 None(프론트 이니셜 원 폴백)
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(mongo, CID_A, "회사에이")

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert cards[0]["logo_url"] is None


def test_get_trending_logo_url_off_when_base_empty(db, mongo, monkeypatch):
    # base 빈 문자열이면 자동 산출을 끈다 — website_url 있어도 None
    monkeypatch.setattr(service.settings, "ranking_logo_cdn_base", "")
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(mongo, CID_A, "회사에이", website_url="https://www.cj.net")

    cards = service.get_trending(db, mongo, limit=5, window_days=7)
    assert cards[0]["logo_url"] is None


def test_record_view_increments_via_own_session(session_factory):
    # record_view 는 팩토리로 새 세션을 열어 +1 하고 커밋·종료한다(BackgroundTasks 안전형)
    service.record_view(session_factory, CID_A)
    service.record_view(session_factory, CID_A)

    check = session_factory()
    try:
        row = check.execute(
            select(CompanyViewDaily).where(CompanyViewDaily.company_id == CID_A)
        ).scalar_one()
        assert row.view_count == 2
    finally:
        check.close()


def test_record_view_swallows_errors(session_factory, monkeypatch):
    # 집계 실패가 리포트 응답을 막지 않아야 한다(예외 삼킴)
    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(repository, "increment_view", boom)
    service.record_view(session_factory, CID_A)  # 예외 전파 없이 끝나면 통과


# ─── API: GET /rankings/trending ──────────────────────────────────────
@pytest.fixture
def client(db, mongo):
    """get_db·get_mongo_db 를 인메모리로 대체한 TestClient."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mongo_db] = lambda: mongo
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_trending_endpoint_returns_camel_case(client, db, mongo):
    today = date.today()
    repository.increment_view(db, CID_A, today)
    db.commit()
    _seed_company(mongo, CID_A, "카멜회사", "미디어")

    res = client.get("/rankings/trending")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list) and len(body) == 1
    card = body[0]
    # camelCase 키로 직렬화돼야 한다(logoUrl 포함)
    assert set(card) == {
        "rank", "companyId", "name", "parentName", "logoText", "logoColor", "logoUrl"
    }
    assert card["companyId"] == CID_A
    assert card["name"] == "카멜회사"


def test_trending_endpoint_respects_limit(client, db, mongo):
    today = date.today()
    for cid, name in ((CID_A, "에이"), (CID_B, "비"), (CID_C, "씨")):
        repository.increment_view(db, cid, today)
        _seed_company(mongo, cid, name)
    db.commit()

    res = client.get("/rankings/trending?limit=2")
    assert res.status_code == 200
    assert len(res.json()) == 2
