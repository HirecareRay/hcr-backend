"""직군별 채용공고(홈 카드) 테스트 — job_roles·service·API.

라이브 DB 없이 동작한다: MongoDB 공고는 mongomock 으로 대체한다. 검증 범위:
  - job_roles: 키워드 분류·가중치·동점 우선순위·태그 추출
  - service: 진행중 필터·마감임박 정렬·직군 그룹 유지·근무지 축약
  - API: GET /home/jobs-by-role camelCase 직렬화
"""

from datetime import date

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.db.mongo import get_mongo_db
from app.jobs import job_roles, service
from app.main import app

CID_A = "a" * 24
TODAY = date(2026, 7, 1)


# ─── job_roles: 분류기 ────────────────────────────────────────────────
def test_classify_backend_by_job_name():
    assert job_roles.classify_job_role("백엔드 서버 개발자", "Java, Spring 경험") == "backend"


def test_classify_frontend_by_job_name():
    assert job_roles.classify_job_role("프론트엔드 개발자", "React, TypeScript") == "frontend"


def test_classify_ai_by_job_name():
    assert job_roles.classify_job_role("머신러닝 엔지니어", "PyTorch, LLM 파인튜닝") == "ai"


def test_classify_unmatched_is_etc():
    assert job_roles.classify_job_role("영업 관리직", "고객 응대 및 매출 관리") == "etc"


def test_classify_strong_signal_outweighs_weak():
    # 제목=프론트(가중치3) vs 본문에 서버 1회(가중치1) → 프론트 승
    assert job_roles.classify_job_role("프론트엔드 개발자", "사내 서버 협업") == "frontend"


def test_classify_tie_break_prefers_ai():
    # 제목에 AI·backend 신호가 동점이면 우선순위(ai>backend)로 ai
    assert job_roles.classify_job_role("AI 서버 엔지니어", "") == "ai"


def test_ai_keyword_no_false_positive_on_email():
    # 'ai'/'ml' 이 email·html 안에서 오탐되지 않는다(경계 매칭)
    assert job_roles.classify_job_role("이메일 마케터", "email html 운영") == "etc"


def test_extract_tech_tags_dedup_and_canonical():
    tags = job_roles.extract_tech_tags("Java 와 java, spring, React 사용")
    assert "Java" in tags and "Spring" in tags and "React" in tags
    assert tags.count("Java") == 1  # 중복 제거


def test_extract_tech_tags_capped_at_eight():
    text = "java spring python react vue node aws docker kubernetes redis kafka"
    assert len(job_roles.extract_tech_tags(text)) <= 8


# ─── service: 채용공고 조립 ───────────────────────────────────────────
def _job_doc(oid: str, title: str, job_name: str, deadline, cid: str = CID_A,
             locations=None, employment_type="정규직", preferred=None) -> dict:
    return {
        "_id": ObjectId(oid),
        "company_id": ObjectId(cid),
        "company_name": "테스트회사",
        "posting_title": title,
        "source_url": "https://example.com/job",
        "work_conditions": {"employment_type": employment_type, "deadline": deadline},
        "jobs": [{
            "job_name": job_name,
            "locations": locations or ["서울"],
            "responsibilities": [],
            "preferred_common": preferred or [],
            "tracks": {},
        }],
        "common": {"preferred": []},
    }


@pytest.fixture
def mongo():
    return mongomock.MongoClient().hcr_test


def test_build_jobs_by_role_groups_and_labels(mongo):
    mongo["job_postings"].insert_many([
        _job_doc("1" * 24, "백엔드 개발자", "서버 개발", "2026-07-21", preferred=["Java"]),
        _job_doc("2" * 24, "프론트엔드 개발자", "웹 개발", "2026-07-15", preferred=["React"]),
    ])
    out = service.build_jobs_by_role(mongo, ["backend", "frontend", "ai"], 5, today=TODAY)

    groups = {g["role"]: g for g in out["groups"]}
    assert list(groups) == ["backend", "frontend", "ai"]
    assert groups["backend"]["label"] == "백엔드"
    assert len(groups["backend"]["jobs"]) == 1
    assert groups["backend"]["jobs"][0]["job_role_label"] == "백엔드"
    assert groups["backend"]["jobs"][0]["tags"] == ["Java"]
    assert groups["ai"]["jobs"] == []  # 공고 없어도 그룹 유지


def test_build_jobs_by_role_excludes_expired_and_sorts_by_deadline(mongo):
    mongo["job_postings"].insert_many([
        _job_doc("1" * 24, "백엔드 A", "서버", "2026-06-01"),   # 마감됨(제외)
        _job_doc("2" * 24, "백엔드 B", "서버", "2026-07-21"),   # 진행중
        _job_doc("3" * 24, "백엔드 C", "서버", "2026-07-05"),   # 진행중(더 임박)
        _job_doc("4" * 24, "백엔드 D", "서버", ""),             # 상시(맨 뒤)
    ])
    out = service.build_jobs_by_role(mongo, ["backend"], 5, today=TODAY)
    jobs = out["groups"][0]["jobs"]

    titles = [j["title"] for j in jobs]
    assert titles == ["백엔드 C", "백엔드 B", "백엔드 D"]  # 임박순 + 상시 뒤, 마감건 제외
    assert jobs[0]["deadline"] == "2026-07-05"
    assert jobs[0]["deadline_type"] == "fixed_date"
    assert jobs[-1]["deadline"] is None
    assert jobs[-1]["deadline_type"] == "rolling"


def test_build_jobs_by_role_respects_per_role(mongo):
    mongo["job_postings"].insert_many([
        _job_doc(str(i) * 24, "백엔드", "서버", f"2026-07-{10 + i:02d}") for i in range(1, 6)
    ])
    out = service.build_jobs_by_role(mongo, ["backend"], 2, today=TODAY)
    assert len(out["groups"][0]["jobs"]) == 2


def test_short_location_takes_first_token(mongo):
    mongo["job_postings"].insert_one(
        _job_doc("1" * 24, "백엔드", "서버", "2026-07-21", locations=["서울 서초구 바우뫼로 147"])
    )
    out = service.build_jobs_by_role(mongo, ["backend"], 5, today=TODAY)
    assert out["groups"][0]["jobs"][0]["location"] == "서울"


# ─── API: 직렬화 ──────────────────────────────────────────────────────
@pytest.fixture
def client(mongo):
    app.dependency_overrides[get_mongo_db] = lambda: mongo
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_jobs_by_role_api_camel_case(client, mongo):
    mongo["job_postings"].insert_one(
        _job_doc("1" * 24, "백엔드 개발자", "서버 개발", "2026-12-31", preferred=["Java"])
    )
    res = client.get("/home/jobs-by-role?roles=backend&perRole=3")
    assert res.status_code == 200
    body = res.json()
    job = body["groups"][0]["jobs"][0]
    # camelCase 키 확인
    assert "companyId" in job and "jobRole" in job and "jobRoleLabel" in job
    assert "deadlineType" in job and "employmentType" in job
    assert job["jobRole"] == "backend"
