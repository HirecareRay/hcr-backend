"""면접 service 오케스트레이션 단위 테스트 (Phase 3).

LLM·STT 장애 시 안전 기본값으로 우회하는 경로와 요약 변환을 검증한다. 경계
모듈(llm·stt)은 mock — 실 OpenAI API 미호출(강사님 키 보호). async 함수는
asyncio.run 으로 실행해 pytest-asyncio 의존성을 피한다.
"""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

from app.interview import context, llm, nonverbal, service, stt
from app.interview.schemas import EventSnapshotMessage, LandmarkFrameMessage


# ── build_main_questions ──────────────────────────────────────────


def test_build_main_questions_uses_llm_result(monkeypatch):
    """컨텍스트가 하나라도 있으면(여기선 직무) LLM 질문을 그대로 사용한다."""
    monkeypatch.setattr(
        llm, 'generate_main_questions', AsyncMock(return_value=['자기소개?', '동기?'])
    )
    questions = asyncio.run(service.build_main_questions(2, job_title='백엔드 개발자'))
    assert questions == ['자기소개?', '동기?']


def test_build_main_questions_empty_context_skips_llm(monkeypatch):
    """회사·지원자·직무가 모두 없으면 LLM 호출 없이 기본 질문으로 폴백한다."""
    monkeypatch.setattr(
        llm,
        'generate_main_questions',
        AsyncMock(side_effect=AssertionError('빈 컨텍스트면 LLM 호출 금지')),
    )
    questions = asyncio.run(service.build_main_questions(4))
    assert questions == list(context.FALLBACK_MAIN_QUESTIONS)


def test_build_main_questions_job_title_only_invokes_llm(monkeypatch):
    """직무만 주어져도 그 직무를 LLM 에 넘겨 개인화 질문을 만든다."""
    captured: dict = {}

    async def _fake_generate(company_context, user_context, job_title, count):
        captured['job_title'] = job_title
        return ['데이터 파이프라인 경험은?', '자기소개?']

    monkeypatch.setattr(llm, 'generate_main_questions', _fake_generate)
    questions = asyncio.run(
        service.build_main_questions(2, job_title='데이터 엔지니어')
    )
    assert captured['job_title'] == '데이터 엔지니어'
    assert questions == ['데이터 파이프라인 경험은?', '자기소개?']


def test_build_main_questions_passes_company_and_user_context_to_llm(monkeypatch):
    """식별자가 주어지면 회사·지원자 컨텍스트를 조회해 LLM 에 함께 넘긴다."""
    captured: dict = {}

    async def _fake_company(db, mongo, company_id):
        captured['company_id'] = company_id
        return '회사명: CJ ENM'

    async def _fake_user(mongo, user_id):
        captured['user_id'] = user_id
        return '[이력서]\n보유 기술: Python'

    async def _fake_generate(company_context, user_context, job_title, count):
        captured['company_context'] = company_context
        captured['user_context'] = user_context
        captured['job_title'] = job_title
        return ['자기소개?', '파이썬 경험은?']

    monkeypatch.setattr(context, 'get_company_context', _fake_company)
    monkeypatch.setattr(context, 'get_user_context', _fake_user)
    monkeypatch.setattr(llm, 'generate_main_questions', _fake_generate)

    questions = asyncio.run(
        service.build_main_questions(
            2,
            company_id='c1',
            user_id='u1',
            job_title='백엔드 개발자',
            db=object(),
            mongo=object(),
        )
    )

    assert questions == ['자기소개?', '파이썬 경험은?']
    assert captured['company_id'] == 'c1'
    assert captured['user_id'] == 'u1'
    assert captured['company_context'] == '회사명: CJ ENM'
    assert captured['user_context'] == '[이력서]\n보유 기술: Python'
    assert captured['job_title'] == '백엔드 개발자'


def test_build_main_questions_falls_back_on_error(monkeypatch):
    """LLM 장애 시 안전 기본 질문으로 우회한다(면접 안 끊김)."""
    monkeypatch.setattr(
        llm,
        'generate_main_questions',
        AsyncMock(side_effect=RuntimeError('LLM down')),
    )
    questions = asyncio.run(service.build_main_questions(4, job_title='백엔드 개발자'))
    assert questions == list(context.FALLBACK_MAIN_QUESTIONS)


def test_build_main_questions_falls_back_on_empty(monkeypatch):
    """LLM 이 빈 목록을 주면 기본 질문으로 우회한다."""
    monkeypatch.setattr(llm, 'generate_main_questions', AsyncMock(return_value=[]))
    questions = asyncio.run(service.build_main_questions(4, job_title='백엔드 개발자'))
    assert questions == list(context.FALLBACK_MAIN_QUESTIONS)


def test_build_main_questions_pads_short_result_with_fallback(monkeypatch):
    """LLM 이 count 보다 적게 주면 기본 질문으로 빈 자리를 채운다(중복 제외)."""
    monkeypatch.setattr(
        llm, 'generate_main_questions', AsyncMock(return_value=['회사 맞춤 질문?'])
    )
    questions = asyncio.run(service.build_main_questions(3, job_title='백엔드 개발자'))

    assert len(questions) == 3
    assert questions[0] == '회사 맞춤 질문?'  # LLM 질문이 앞에 온다
    # 나머지는 기본 질문에서 보충되며 중복은 없다
    assert len(set(questions)) == 3
    assert all(q in (('회사 맞춤 질문?',) + context.FALLBACK_MAIN_QUESTIONS) for q in questions)


# ── stream_evaluation ─────────────────────────────────────────────


def test_stream_evaluation_empty_answer_yields_nothing(monkeypatch):
    """빈 답변은 평가하지 않는다(LLM 호출도 안 함)."""
    monkeypatch.setattr(
        llm, 'stream_evaluation', AsyncMock(side_effect=AssertionError('호출 금지'))
    )

    async def run() -> list:
        return [e async for e in service.stream_evaluation('q', '')]

    assert asyncio.run(run()) == []


def test_stream_evaluation_wraps_deltas_in_events(monkeypatch):
    """LLM 토큰을 EvalDeltaEvent 로 감싼다."""

    async def _gen(question: str, answer: str) -> AsyncIterator[str]:
        for d in ['좋', '은 답변']:
            yield d

    monkeypatch.setattr(llm, 'stream_evaluation', _gen)

    async def run() -> list:
        return [e async for e in service.stream_evaluation('q', 'a')]

    events = asyncio.run(run())
    assert [e.type for e in events] == ['eval_delta', 'eval_delta']
    assert [e.delta for e in events] == ['좋', '은 답변']


def test_stream_evaluation_swallows_llm_error(monkeypatch):
    """평가 중 LLM 장애는 WS 를 끊지 않고 스트림을 조용히 끝낸다."""

    async def _boom(question: str, answer: str) -> AsyncIterator[str]:
        raise RuntimeError('eval down')
        yield  # pragma: no cover

    monkeypatch.setattr(llm, 'stream_evaluation', _boom)

    async def run() -> list:
        return [e async for e in service.stream_evaluation('q', 'a')]

    assert asyncio.run(run()) == []


# ── generate_follow_up ────────────────────────────────────────────


def test_follow_up_empty_answer_returns_none(monkeypatch):
    """답변이 없으면 꼬리질문을 만들지 않는다."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(side_effect=AssertionError('호출 금지'))
    )
    assert asyncio.run(service.generate_follow_up('q', '')) is None


def test_follow_up_error_returns_none(monkeypatch):
    """꼬리질문 생성 장애는 None — 라우터가 다음 메인으로 우회한다."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(side_effect=RuntimeError('down'))
    )
    assert asyncio.run(service.generate_follow_up('q', 'a')) is None


def test_follow_up_returns_trimmed_text(monkeypatch):
    """정상 생성 시 다듬은 본문을 반환."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(return_value='  더 구체적으로?  ')
    )
    assert asyncio.run(service.generate_follow_up('q', 'a')) == '더 구체적으로?'


# ── transcribe_answer (회귀 가드) ─────────────────────────────────


def test_transcribe_answer_wraps_text(monkeypatch):
    monkeypatch.setattr(stt, 'transcribe_audio', AsyncMock(return_value='협업합니다'))
    event = asyncio.run(service.transcribe_answer(b'audio'))
    assert event.type == 'transcript_delta'
    assert event.delta == '협업합니다'
    assert event.is_final is True


def test_transcribe_answer_empty_returns_none(monkeypatch):
    monkeypatch.setattr(stt, 'transcribe_audio', AsyncMock(return_value=''))
    assert asyncio.run(service.transcribe_answer(b'audio')) is None


# ── build_summary ─────────────────────────────────────────────────


def test_build_summary_maps_llm_fields(monkeypatch):
    """LLM dict 를 SummaryEvent 로 매핑한다(비언어 미제공 시 점수 가감 없음)."""
    monkeypatch.setattr(
        llm,
        'generate_summary',
        AsyncMock(
            return_value={
                'overall_score': 75,
                'language_feedback': '논리적입니다',
                'improvements': ['결론 보강', ''],
            }
        ),
    )
    summary = asyncio.run(service.build_summary((service.Turn('q', 'a', 'e'),)))
    assert summary.overall_score == 75.0  # 비언어 데이터 없음 → 가감 0
    assert summary.language_feedback == '논리적입니다'
    assert summary.improvements == ['결론 보강']  # 빈 항목 제거
    assert summary.nonverbal_feedback  # placeholder 가 아닌 안내 문구
    assert 'Phase' not in summary.nonverbal_feedback


def test_build_summary_applies_nonverbal_penalty(monkeypatch):
    """비언어 지표(시선이탈)가 있으면 점수를 깎고 피드백에 반영한다."""
    monkeypatch.setattr(
        llm,
        'generate_summary',
        AsyncMock(return_value={'overall_score': 90, 'language_feedback': '좋음'}),
    )
    frames = tuple(
        LandmarkFrameMessage(gaze_x=0.9) for _ in range(5)
    )
    metrics = nonverbal.aggregate(frames, ())
    summary = asyncio.run(service.build_summary((), metrics))
    assert summary.overall_score < 90.0  # 시선이탈 감점 반영
    assert '시선' in summary.nonverbal_feedback


def test_build_summary_clamps_score_to_zero(monkeypatch):
    """비언어 감점이 커도 점수는 0 미만으로 내려가지 않는다."""
    monkeypatch.setattr(
        llm,
        'generate_summary',
        AsyncMock(return_value={'overall_score': 5, 'language_feedback': 'x'}),
    )
    frames = tuple(LandmarkFrameMessage(gaze_x=1.0) for _ in range(10))
    events = tuple(
        EventSnapshotMessage(event='gaze_away') for _ in range(50)
    )
    metrics = nonverbal.aggregate(frames, events)
    summary = asyncio.run(service.build_summary((), metrics))
    assert summary.overall_score == 0.0


def test_build_summary_survives_nonverbal_error(monkeypatch):
    """비언어 환산이 예외를 던져도 요약은 0 가감·안내 문구로 계속 생성된다."""
    monkeypatch.setattr(
        llm,
        'generate_summary',
        AsyncMock(
            return_value={'overall_score': 70, 'language_feedback': 'ok', 'improvements': []}
        ),
    )
    monkeypatch.setattr(nonverbal, 'score_penalty', Mock(side_effect=RuntimeError('boom')))

    summary = asyncio.run(service.build_summary((), nonverbal.NonverbalMetrics()))

    assert summary.type == 'summary'
    assert summary.overall_score == 70.0  # 감점 0 으로 우회(요약 안 끊김)
    assert '오류' in summary.nonverbal_feedback


def test_build_summary_falls_back_on_error(monkeypatch):
    """요약 LLM 장애 시 안전 기본 요약(score 0)을 반환한다."""
    monkeypatch.setattr(
        llm, 'generate_summary', AsyncMock(side_effect=RuntimeError('down'))
    )
    summary = asyncio.run(service.build_summary(()))
    assert summary.overall_score == 0.0
    assert summary.language_feedback  # 비어 있지 않은 안내 문구
    assert summary.improvements == []


def test_build_summary_coerces_bad_score(monkeypatch):
    """LLM 이 점수를 문자열로 줘도 float 으로 강제, 파싱 불가면 0.0."""
    monkeypatch.setattr(
        llm, 'generate_summary', AsyncMock(return_value={'overall_score': 'N/A'})
    )
    summary = asyncio.run(service.build_summary(()))
    assert summary.overall_score == 0.0


def test_build_summary_rejects_non_finite_score(monkeypatch):
    """LLM 이 'nan'/'inf' 를 줘도 clamp 를 우회하지 못하고 0.0 으로 막힌다."""
    for bad in ('nan', 'inf', '-inf'):
        monkeypatch.setattr(
            llm, 'generate_summary', AsyncMock(return_value={'overall_score': bad})
        )
        summary = asyncio.run(service.build_summary(()))
        assert summary.overall_score == 0.0
