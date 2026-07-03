"""result_repository 통합 테스트 — mongomock 인메모리 MongoDB (실 DB·네트워크 불필요).

저장→조회 정렬(최신 세션·직전 세션), 회사·소유자 필터, created_at 주입, result_id
unique 인덱스 멱등성을 실제 pymongo 인터페이스로 검증한다(service 테스트는 repository
를 mock 하므로, 쿼리·인덱스의 실제 동작은 여기서만 확인된다).
"""

from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from app.interview import result_repository


@pytest.fixture
def db():
    """mongomock 인메모리 DB 핸들(테스트마다 새 클라이언트)."""
    return mongomock.MongoClient().interview_test


class _IncrementingClock:
    """호출마다 1초씩 증가하는 결정론적 시계 — created_at 정렬 테스트의 타이 제거.

    실제 datetime.now() 는 두 저장이 같은 마이크로초에 걸리면 정렬이 모호해져
    플래키하다. repository.datetime 을 이걸로 바꿔 저장 순서를 시간순으로 고정한다.
    """

    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self, tz=None) -> datetime:
        self._t = self._t + timedelta(seconds=1)
        return self._t


@pytest.fixture
def monotonic_clock(monkeypatch):
    """repository 의 created_at 타임스탬프를 단조 증가로 고정한다."""
    monkeypatch.setattr(result_repository, 'datetime', _IncrementingClock())


def _doc(result_id: str, user_id: str, company_id: str) -> dict:
    return {
        'result_id': result_id,
        'user_id': user_id,
        'company_id': company_id,
        'conducted_at': '2026-06-29T00:00:00+00:00',
        'result': {'meta': {'result_id': result_id}, 'overall': {'score': 70}},
    }


def test_save_then_find_by_id_roundtrip(db):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    found = result_repository.find_by_id(db, 'r1')
    assert found is not None
    assert found['user_id'] == 'u1'
    assert 'created_at' in found  # 저장 계층이 created_at 을 찍는다


def test_find_by_id_none_when_missing(db):
    assert result_repository.find_by_id(db, 'nope') is None


def test_find_latest_by_company_returns_most_recent(db, monotonic_clock):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    result_repository.save_session_result(db, _doc('r2', 'u1', 'c1'))
    latest = result_repository.find_latest_by_company(db, 'u1', 'c1')
    assert latest['result_id'] == 'r2'  # created_at 내림차순 → 나중 저장이 최신


def test_find_latest_by_company_scoped_to_user_and_company(db):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    result_repository.save_session_result(db, _doc('r2', 'other', 'c1'))
    result_repository.save_session_result(db, _doc('r3', 'u1', 'c2'))
    latest = result_repository.find_latest_by_company(db, 'u1', 'c1')
    assert latest['result_id'] == 'r1'  # 다른 유저·다른 회사 세션은 제외


def test_find_latest_by_user_across_companies(db, monotonic_clock):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    result_repository.save_session_result(db, _doc('r2', 'u1', 'c2'))
    latest = result_repository.find_latest_by_user(db, 'u1')
    assert latest['result_id'] == 'r2'  # 회사 무관, 가장 최근(직전 세션 비교용)


def test_count_by_user(db):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    result_repository.save_session_result(db, _doc('r2', 'u1', 'c2'))
    result_repository.save_session_result(db, _doc('r3', 'other', 'c1'))
    assert result_repository.count_by_user(db, 'u1') == 2
    assert result_repository.count_by_user(db, 'nobody') == 0


def test_result_id_unique_index(db):
    result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
    with pytest.raises(DuplicateKeyError):
        result_repository.save_session_result(db, _doc('r1', 'u1', 'c1'))
