"""면접 결과 REST 조회 엔드포인트 통합 테스트 (계약 ④).

GET /interviews/results/{companyId}·/results/by-id/{resultId} 의 인증(로그인 전용)·
소유권(남의 결과 차단)·404·정상 조회를 TestClient 로 검증한다. DB·JWT 는 의존성
오버라이드와 monkeypatch 로 대체한다(속도·결정성·네트워크 독립성).
"""

from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.db.mongo import get_mongo_db
from app.interview import result_builder, result_repository, service
from app.interview.result_schemas import ResultMeta
from app.main import app

client = TestClient(app)


def _result_dump(company_id='c1', overall=78):
    meta = ResultMeta(
        result_id='r1',
        company_id=company_id,
        company_name='CJ ENM',
        job_title='마케팅',
        conducted_at='2026-06-29T00:00:00+00:00',
        duration_sec=120,
        mode='voice',
        question_count=1,
    )
    report = {'overall': {'score': overall, 'grade': 'B+', 'headline': 'h'}}
    result = result_builder.build_result(
        meta=meta, history=(service.Turn('q', 'a', 'e', 'common'),), report=report
    )
    return result.model_dump(by_alias=False)


def _auth_as(monkeypatch, user_id='u1'):
    """JWT 검증을 우회해 항상 user_id 를 반환하게 한다(실 토큰 불필요)."""
    monkeypatch.setattr(
        'app.interview.router.decode_access_token', lambda token: user_id
    )
    app.dependency_overrides[get_mongo_db] = lambda: object()


def _teardown():
    app.dependency_overrides.clear()


def test_get_result_by_company_returns_camel_json(monkeypatch):
    _auth_as(monkeypatch, 'u1')
    monkeypatch.setattr(
        result_repository,
        'find_latest_by_company',
        Mock(return_value={'user_id': 'u1', 'result': _result_dump(overall=78)}),
    )
    try:
        res = client.get(
            '/interviews/results/c1', headers={'Authorization': 'Bearer x'}
        )
    finally:
        _teardown()
    assert res.status_code == 200
    body = res.json()
    assert body['meta']['companyId'] == 'c1'  # camelCase 직렬화
    assert body['overall']['score'] == 78


def test_get_result_requires_auth(monkeypatch):
    # 인증 헤더 없음 → 401 (mongo 의존성은 오버라이드해 통과시키고 인증만 검증)
    app.dependency_overrides[get_mongo_db] = lambda: object()
    try:
        res = client.get('/interviews/results/c1')
    finally:
        _teardown()
    assert res.status_code == 401


def test_get_result_404_when_missing(monkeypatch):
    _auth_as(monkeypatch, 'u1')
    monkeypatch.setattr(
        result_repository, 'find_latest_by_company', Mock(return_value=None)
    )
    try:
        res = client.get(
            '/interviews/results/c1', headers={'Authorization': 'Bearer x'}
        )
    finally:
        _teardown()
    assert res.status_code == 404


def test_get_result_by_id_rejects_non_owner(monkeypatch):
    _auth_as(monkeypatch, 'intruder')
    monkeypatch.setattr(
        result_repository,
        'find_by_id',
        Mock(return_value={'user_id': 'owner', 'result': _result_dump()}),
    )
    try:
        res = client.get(
            '/interviews/results/by-id/r1', headers={'Authorization': 'Bearer x'}
        )
    finally:
        _teardown()
    assert res.status_code == 404  # 소유자가 아니면 없는 것처럼 차단


def test_get_result_by_id_returns_owner_result(monkeypatch):
    _auth_as(monkeypatch, 'owner')
    monkeypatch.setattr(
        result_repository,
        'find_by_id',
        Mock(return_value={'user_id': 'owner', 'result': _result_dump()}),
    )
    try:
        res = client.get(
            '/interviews/results/by-id/r1', headers={'Authorization': 'Bearer x'}
        )
    finally:
        _teardown()
    assert res.status_code == 200
    assert res.json()['meta']['resultId'] == 'r1'
