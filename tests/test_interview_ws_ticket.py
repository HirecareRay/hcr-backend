"""면접 WS 1회용 티켓 저장소 단위 테스트.

티켓은 면접 WS 입장 전용 단기·1회용 입장권이다. 발급(issue) → 소비(consume)
한 번이면 폐기되고, TTL 이 지나면 무효가 된다. 무효·없는 티켓은 None 으로 우회해
면접이 익명으로 진행되게 한다(연결을 깨지 않음).

⚠️ 시간 의존 검증은 실제 sleep 대신 모듈 클록(_now)을 monkeypatch 해 결정론으로 만든다.
"""

import pytest

from app.interview import ws_ticket


@pytest.fixture(autouse=True)
def _clear_store():
    """테스트 간 티켓 저장소 격리 — 누수된 티켓이 다른 테스트에 영향 주지 않게."""
    ws_ticket.clear()
    yield
    ws_ticket.clear()


def test_issue_returns_opaque_ticket_and_expires_in():
    """발급은 불투명 문자열 티켓과 만료초(expires_in)를 돌려준다."""
    ticket, expires_in = ws_ticket.issue_ticket('42', ttl_seconds=60)

    assert isinstance(ticket, str)
    assert len(ticket) >= 32  # token_urlsafe(32) 는 43자 안팎
    assert '42' not in ticket  # 불투명 — user_id 가 평문으로 새지 않음
    assert expires_in == 60


def test_consume_valid_ticket_returns_user_id():
    """유효한 티켓을 소비하면 발급 시의 user_id 를 돌려준다."""
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    assert ws_ticket.consume_ticket(ticket) == '42'


def test_ticket_is_single_use():
    """같은 티켓을 두 번째 소비하면 None — 1회용이라 즉시 폐기된다."""
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    assert ws_ticket.consume_ticket(ticket) == '42'
    assert ws_ticket.consume_ticket(ticket) is None


def test_expired_ticket_returns_none(monkeypatch):
    """TTL 이 지난 티켓은 None — 만료 후엔 입장 불가."""
    clock = {'t': 1000.0}
    monkeypatch.setattr(ws_ticket, '_now', lambda: clock['t'])

    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)
    clock['t'] += 61  # 만료시각을 지나침

    assert ws_ticket.consume_ticket(ticket) is None


def test_unknown_ticket_returns_none():
    """저장소에 없는 티켓은 None — 위조·오타 우회."""
    assert ws_ticket.consume_ticket('does-not-exist') is None


def test_none_or_empty_ticket_returns_none():
    """티켓이 없으면(쿼리 미첨부) None — 익명 진행 경로."""
    assert ws_ticket.consume_ticket(None) is None
    assert ws_ticket.consume_ticket('') is None


def test_issue_sweeps_expired_unconsumed_tickets(monkeypatch):
    """발급 시 만료된 미소비 티켓이 청소돼 저장소가 무한히 커지지 않는다."""
    clock = {'t': 1000.0}
    monkeypatch.setattr(ws_ticket, '_now', lambda: clock['t'])

    # 소비되지 않을 티켓 3개 발급 후 만료시킨다
    for _ in range(3):
        ws_ticket.issue_ticket('old', ttl_seconds=60)
    assert len(ws_ticket._store) == 3
    clock['t'] += 61

    # 새 발급이 만료분을 청소하므로 저장소엔 새 티켓 1개만 남는다
    ws_ticket.issue_ticket('new', ttl_seconds=60)
    assert len(ws_ticket._store) == 1


def test_default_ttl_uses_settings(monkeypatch):
    """ttl_seconds 를 안 주면 settings 의 기본 TTL 을 쓴다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, 'interview_ws_ticket_ttl_seconds', 90)
    _, expires_in = ws_ticket.issue_ticket('7')

    assert expires_in == 90
