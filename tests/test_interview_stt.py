"""STT 모듈·service 전사 함수 단위 테스트 (Phase 2).

⚠️ 모든 테스트는 OpenAI API 를 호출하지 않는다(키는 강사님 대여분 — 비용 사고
금지). 모킹 경계는 app/interview/stt.py 다: stt 테스트는 _get_client 를, service
테스트는 stt.transcribe_audio 를 대체한다. async 함수는 asyncio.run 으로 실행해
pytest-asyncio 의존성을 피한다.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.interview import service, stt


def _fake_client(create: AsyncMock) -> SimpleNamespace:
    """audio.transcriptions.create 만 갖춘 가짜 AsyncOpenAI 클라이언트."""
    transcriptions = SimpleNamespace(create=create)
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))


# ── stt.transcribe_audio ──────────────────────────────────────────


def test_transcribe_empty_returns_empty_without_api(monkeypatch):
    """빈 오디오는 API 호출 없이 빈 문자열 — 불필요한 과금 방지."""

    def _boom() -> None:
        raise AssertionError('빈 입력에서 클라이언트를 만들면 안 됨')

    monkeypatch.setattr(stt, '_get_client', _boom)
    assert asyncio.run(stt.transcribe_audio(b'')) == ''


def test_transcribe_calls_openai_and_returns_text(monkeypatch):
    """누적 오디오를 gpt-4o-mini-transcribe 로 전사하고 텍스트를 다듬어 반환."""
    create = AsyncMock(return_value=SimpleNamespace(text='  안녕하세요  '))
    monkeypatch.setattr(stt, '_get_client', lambda: _fake_client(create))

    text = asyncio.run(stt.transcribe_audio(b'webm-opus-bytes'))

    assert text == '안녕하세요'
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs['model'] == 'gpt-4o-mini-transcribe'  # 최저가 모델 고정
    assert kwargs['file'].name.endswith('.webm')  # 포맷을 파일명으로 알림


def test_transcribe_api_error_raises_friendly(monkeypatch):
    """외부 API 장애는 내부 스택 노출 없이 사용자 친화 메시지로 변환."""
    create = AsyncMock(side_effect=Exception('upstream 500'))
    monkeypatch.setattr(stt, '_get_client', lambda: _fake_client(create))

    with pytest.raises(RuntimeError, match='전사'):
        asyncio.run(stt.transcribe_audio(b'x'))


def test_get_client_requires_api_key(monkeypatch):
    """키가 없으면 명확히 실패 — 조용한 오작동 방지."""
    monkeypatch.setattr(stt.settings, 'openai_api_key', '')
    monkeypatch.setattr(stt, '_client', None)
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        stt._get_client()


# ── service.transcribe_answer ─────────────────────────────────────


def test_transcribe_answer_wraps_text_in_final_event(monkeypatch):
    """전사 텍스트를 TranscriptDeltaEvent(is_final=True) 로 감싼다."""
    monkeypatch.setattr(
        stt, 'transcribe_audio', AsyncMock(return_value='제 강점은 협업입니다')
    )

    event = asyncio.run(service.transcribe_answer(b'audio'))

    assert event.type == 'transcript_delta'
    assert event.delta == '제 강점은 협업입니다'
    assert event.is_final is True


def test_transcribe_answer_empty_text_returns_none(monkeypatch):
    """전사 결과가 비면 빈 자막을 내려보내지 않도록 None 을 반환."""
    monkeypatch.setattr(stt, 'transcribe_audio', AsyncMock(return_value=''))

    assert asyncio.run(service.transcribe_answer(b'audio')) is None
