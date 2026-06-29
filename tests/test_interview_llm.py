"""LLM 면접 두뇌 경계(app/interview/llm.py) 단위 테스트 (Phase 3).

⚠️ 모든 테스트는 OpenAI API 를 호출하지 않는다(키는 강사님 대여분 — 비용 사고
금지). 모킹 경계는 llm._get_client 다 — 가짜 AsyncOpenAI 로 대체한다. async 함수는
asyncio.run 으로 실행해 pytest-asyncio 의존성을 피한다.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.interview import llm


def _fake_chat_client(create: AsyncMock) -> SimpleNamespace:
    """chat.completions.create 만 갖춘 가짜 AsyncOpenAI 클라이언트."""
    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def _completion(content: str) -> SimpleNamespace:
    """비스트리밍 chat 응답 객체(choices[0].message.content)."""
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeStream:
    """stream=True 응답을 흉내내는 async 이터러블(choices[0].delta.content)."""

    def __init__(self, deltas: list[str | None]):
        self._deltas = deltas

    def __aiter__(self):
        async def gen():
            for d in self._deltas:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=d))]
                )

        return gen()


# ── generate_main_questions ───────────────────────────────────────


def test_main_questions_splits_lines_and_limits_count(monkeypatch):
    """줄 단위로 쪼개고 빈 줄을 버리며 count 로 자른다."""
    create = AsyncMock(
        return_value=_completion('자기소개 부탁드립니다\n\n지원 동기는?\n강점은?\n초과 질문')
    )
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    questions = asyncio.run(llm.generate_main_questions('회사 컨텍스트', '', 3))

    assert questions == ['자기소개 부탁드립니다', '지원 동기는?', '강점은?']
    assert create.await_args.kwargs['model'] == 'gpt-4o-mini'  # 저가 모델 고정


def test_main_questions_dedupes_preserving_order(monkeypatch):
    """중복 질문은 입력 순서를 유지하며 한 번만 남긴다."""
    create = AsyncMock(
        return_value=_completion('자기소개 부탁드립니다\n지원 동기는?\n자기소개 부탁드립니다')
    )
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    questions = asyncio.run(llm.generate_main_questions('회사 컨텍스트', '', 4))

    assert questions == ['자기소개 부탁드립니다', '지원 동기는?']


def test_main_questions_api_error_raises_friendly(monkeypatch):
    """외부 API 장애는 내부 스택 노출 없이 RuntimeError 로 변환."""
    create = AsyncMock(side_effect=Exception('upstream 500'))
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    with pytest.raises(RuntimeError, match='면접'):
        asyncio.run(llm.generate_main_questions('ctx', '', 4))


# ── generate_follow_up ────────────────────────────────────────────


def test_follow_up_returns_trimmed_question(monkeypatch):
    """꼬리질문 본문을 다듬어 반환한다."""
    create = AsyncMock(return_value=_completion('  그 경험에서 가장 어려웠던 점은?  '))
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    text = asyncio.run(llm.generate_follow_up('자기소개', '협업을 잘합니다'))

    assert text == '그 경험에서 가장 어려웠던 점은?'


# ── stream_evaluation ─────────────────────────────────────────────


def test_stream_evaluation_yields_token_deltas(monkeypatch):
    """평가를 토큰 델타로 스트리밍하고 빈(None) 델타는 건너뛴다."""
    create = AsyncMock(return_value=_FakeStream(['좋은 ', None, '답변입니다']))
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    async def run() -> list[str]:
        return [d async for d in llm.stream_evaluation('q', 'a')]

    deltas = asyncio.run(run())

    assert deltas == ['좋은 ', '답변입니다']
    assert create.await_args.kwargs['stream'] is True
    assert create.await_args.kwargs['model'] == 'gpt-4o-mini'


def test_stream_evaluation_api_error_raises_friendly(monkeypatch):
    """스트리밍 장애도 RuntimeError 로 변환."""
    create = AsyncMock(side_effect=Exception('boom'))
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    async def run() -> list[str]:
        return [d async for d in llm.stream_evaluation('q', 'a')]

    with pytest.raises(RuntimeError, match='평가'):
        asyncio.run(run())


# ── generate_summary / _parse_summary ─────────────────────────────


def test_generate_summary_parses_json(monkeypatch):
    """JSON 응답을 dict 로 파싱해 반환."""
    payload = '{"overall_score": 82, "language_feedback": "논리적", "improvements": ["결론 강화"]}'
    create = AsyncMock(return_value=_completion(payload))
    monkeypatch.setattr(llm, '_get_client', lambda: _fake_chat_client(create))

    data = asyncio.run(llm.generate_summary('Q1...A1...'))

    assert data['overall_score'] == 82
    assert data['improvements'] == ['결론 강화']


def test_parse_summary_strips_code_fence():
    """```json 코드펜스로 감싸도 파싱한다."""
    text = '```json\n{"overall_score": 70}\n```'
    assert llm._parse_summary(text) == {'overall_score': 70}


def test_parse_summary_invalid_returns_empty():
    """JSON 이 아니면 빈 dict — 호출부가 안전 기본값으로 우회하게 한다."""
    assert llm._parse_summary('죄송합니다, JSON 이 아닙니다') == {}


# ── _get_client ───────────────────────────────────────────────────


def test_get_client_requires_api_key(monkeypatch):
    """키가 없으면 명확히 실패 — 조용한 오작동 방지."""
    monkeypatch.setattr(llm.settings, 'openai_api_key', '')
    monkeypatch.setattr(llm, '_client', None)
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        llm._get_client()
