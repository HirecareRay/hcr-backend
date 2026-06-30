"""면접 WS Origin 검증 테스트 — CSWSH(Cross-Site WebSocket Hijacking) 방어.

WS 엔 CORS 가 적용되지 않으므로 서버가 핸드셰이크의 Origin 헤더를 직접 본다.
검사 순서는 ① Origin → ② 티켓 소비 → ③ accept 라, 허용되지 않은 출처는 티켓 소비·
질문 생성(LLM)·과금 경로에 닿기 전에 1008 로 끊긴다.

  - 단위: ws_origin.is_allowed_origin 의 운영(목록 설정)·개발(미설정) 모드 판정
  - 통합: 실제 WS 핸드셰이크에서 허용/거절과 '거절 시 티켓 미소비·LLM 미진입' 보장

⚠️ llm·service 는 mock — 실 OpenAI API 미호출(강사님 키 보호).
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.interview import service, ws_origin, ws_ticket
from app.main import app

client = TestClient(app)

_PROD_ORIGINS = 'https://hcr.example.com,https://www.hcr.example.com'


@pytest.fixture(autouse=True)
def _clear_ticket_store():
    """테스트 간 티켓 저장소 격리 — 누수된 티켓이 다른 테스트에 영향 주지 않게."""
    ws_ticket.clear()
    yield
    ws_ticket.clear()


# ── 단위: Origin 판정 (ws_origin) ──────────────────────────────────────


def test_dev_mode_when_list_empty(monkeypatch):
    """허용 목록이 비어 있으면 개발 모드로 본다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', '')
    assert ws_origin.is_dev_mode() is True


def test_dev_mode_allows_localhost_and_missing_origin(monkeypatch):
    """개발 모드: 로컬 프론트와 Origin 없음(테스트 도구)을 허용, 그 외는 거절."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', '')
    assert ws_origin.is_allowed_origin('http://localhost:3000') is True
    assert ws_origin.is_allowed_origin('http://127.0.0.1:3000') is True
    assert ws_origin.is_allowed_origin(None) is True  # 비브라우저 도구
    assert ws_origin.is_allowed_origin('https://evil.com') is False


def test_prod_mode_allows_only_listed_origins(monkeypatch):
    """운영(목록 설정): 목록에 정확히 일치하는 출처만 허용."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    assert ws_origin.is_dev_mode() is False
    assert ws_origin.is_allowed_origin('https://hcr.example.com') is True
    assert ws_origin.is_allowed_origin('https://www.hcr.example.com') is True
    assert ws_origin.is_allowed_origin('https://evil.com') is False
    assert ws_origin.is_allowed_origin('http://localhost:3000') is False  # 운영선 로컬 불가


def test_prod_mode_rejects_missing_origin(monkeypatch):
    """운영: Origin 헤더가 없으면(비브라우저 우회) 거절 — 브라우저는 항상 보낸다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    assert ws_origin.is_allowed_origin(None) is False


def test_parse_trims_and_drops_blanks(monkeypatch):
    """콤마 구분 파싱은 공백 trim·빈 값 제거를 한다."""
    monkeypatch.setattr(
        settings,
        'interview_allowed_origins',
        '  https://a.com , , https://b.com ,',
    )
    assert ws_origin.is_allowed_origin('https://a.com') is True
    assert ws_origin.is_allowed_origin('https://b.com') is True
    assert ws_origin.is_allowed_origin('') is False  # 빈 항목은 허용 출처가 아님


def test_warn_once_if_dev_mode_logs_once(monkeypatch, caplog):
    """개발 모드(목록 미설정)면 1회만 경고하고, 이후 호출은 침묵한다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', '')
    monkeypatch.setattr(ws_origin, '_dev_mode_warned', False)

    with caplog.at_level('WARNING'):
        ws_origin.warn_once_if_dev_mode()
        ws_origin.warn_once_if_dev_mode()  # 두 번째는 침묵

    dev_warnings = [r for r in caplog.records if 'INTERVIEW_ALLOWED_ORIGINS' in r.message]
    assert len(dev_warnings) == 1


def test_warn_once_if_dev_mode_silent_in_prod(monkeypatch, caplog):
    """운영(목록 설정)이면 경고하지 않는다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    monkeypatch.setattr(ws_origin, '_dev_mode_warned', False)

    with caplog.at_level('WARNING'):
        ws_origin.warn_once_if_dev_mode()

    assert not [r for r in caplog.records if 'INTERVIEW_ALLOWED_ORIGINS' in r.message]


# ── 통합: WS 핸드셰이크 Origin 검사 ────────────────────────────────────


def _stub_questions(monkeypatch) -> AsyncMock:
    """build_main_questions 를 mock 해 유효 연결이 첫 질문까지 진행하게 한다(LLM 미호출)."""
    build = AsyncMock(return_value=['자기소개를 부탁드립니다', '강점은?'])
    monkeypatch.setattr(service, 'build_main_questions', build)
    return build


def test_ws_allows_listed_origin(monkeypatch):
    """운영 목록에 있는 Origin 으로 연결하면 정상적으로 첫 질문을 받는다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    _stub_questions(monkeypatch)
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    with client.websocket_connect(
        f'/interviews/ws/s1?ticket={ticket}',
        headers={'origin': 'https://hcr.example.com'},
    ) as ws:
        data = ws.receive_json()

    assert data['type'] == 'question'
    assert data['questionId'] == 'm0'


def test_ws_rejects_disallowed_origin_before_consuming_ticket(monkeypatch):
    """허용 안 된 Origin 은 1008 로 거절되고, 티켓·LLM 경로에 닿지 않는다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    build = _stub_questions(monkeypatch)
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f'/interviews/ws/s1?ticket={ticket}',
            headers={'origin': 'https://evil.com'},
        ) as ws:
            ws.receive_json()

    assert exc.value.code == 1008
    build.assert_not_awaited()  # origin 거절 → 질문 생성(LLM) 미진입
    # ① Origin → ② 티켓 순서라 티켓은 소비되지 않는다 — 같은 티켓이 여전히 유효.
    assert ws_ticket.consume_ticket(ticket) == '42'


def test_ws_prod_rejects_missing_origin(monkeypatch):
    """운영 모드에서 Origin 헤더 없는 연결(비브라우저 우회)은 거절한다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', _PROD_ORIGINS)
    build = _stub_questions(monkeypatch)
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f'/interviews/ws/s1?ticket={ticket}') as ws:
            ws.receive_json()

    assert exc.value.code == 1008
    build.assert_not_awaited()


def test_ws_dev_mode_allows_localhost_origin(monkeypatch):
    """개발 모드(목록 미설정) + localhost Origin 은 정상 동작한다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', '')
    _stub_questions(monkeypatch)
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    with client.websocket_connect(
        f'/interviews/ws/s1?ticket={ticket}',
        headers={'origin': 'http://localhost:3000'},
    ) as ws:
        data = ws.receive_json()

    assert data['questionId'] == 'm0'


def test_ws_dev_mode_allows_missing_origin(monkeypatch):
    """개발 모드 + Origin 없음(TestClient 기본)도 정상 — 기존 WS 테스트가 안 깨진다."""
    monkeypatch.setattr(settings, 'interview_allowed_origins', '')
    _stub_questions(monkeypatch)
    ticket, _ = ws_ticket.issue_ticket('42', ttl_seconds=60)

    with client.websocket_connect(f'/interviews/ws/s1?ticket={ticket}') as ws:
        data = ws.receive_json()

    assert data['questionId'] == 'm0'
