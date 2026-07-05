"""면접 service 오케스트레이션 단위 테스트 (Phase 3).

LLM·STT 장애 시 안전 기본값으로 우회하는 경로와 요약 변환을 검증한다. 경계
모듈(llm·stt)은 mock — 실 OpenAI API 미호출(강사님 키 보호). async 함수는
asyncio.run 으로 실행해 pytest-asyncio 의존성을 피한다.
"""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

from app.core.config import resolve_persona_voice, settings
from app.interview import context, llm, nonverbal, result_builder, service, stt, tts
from app.interview.personas import CULTURE, PRACTICAL, TECH
from app.interview.result_schemas import ResultMeta
from app.interview.schemas import LandmarkFrameMessage


def _result(report: dict, history=(service.Turn('q', 'a', 'e', 'common'),)):
    """report dict + 히스토리로 InterviewResult 를 조립한다(summary_from_result 입력용)."""
    meta = ResultMeta(
        result_id='r1',
        company_id='c1',
        company_name='CJ ENM',
        job_title='마케팅',
        conducted_at='2026-06-29T00:00:00+00:00',
        duration_sec=60,
        mode='voice',
        question_count=len(history),
    )
    return result_builder.build_result(meta=meta, history=history, report=report)


# ── build_main_questions ──────────────────────────────────────────


def test_build_main_questions_uses_llm_result(monkeypatch):
    """컨텍스트가 하나라도 있으면(여기선 직무) LLM 질문을 그대로 쓰고 personalized=True."""
    monkeypatch.setattr(
        llm, 'generate_main_questions', AsyncMock(return_value=['자기소개?', '동기?'])
    )
    result = asyncio.run(service.build_main_questions(2, job_title='백엔드 개발자'))
    assert result.questions == ['자기소개?', '동기?']
    assert result.personalized is True


def test_build_main_questions_empty_context_skips_llm(monkeypatch):
    """회사·지원자·직무가 모두 없으면 LLM 없이 기본 질문 폴백 + personalized=False."""
    monkeypatch.setattr(
        llm,
        'generate_main_questions',
        AsyncMock(side_effect=AssertionError('빈 컨텍스트면 LLM 호출 금지')),
    )
    result = asyncio.run(service.build_main_questions(4))
    assert result.questions == list(context.FALLBACK_MAIN_QUESTIONS)
    # 순수 폴백 — 라우터가 이 값으로 꼬리질문까지 생략한다.
    assert result.personalized is False
    # 폴백 면접에도 3인 패널을 배정한다(결정 1) — 기본질문에도 면접관 배지·목소리.
    assert [p.id for p in result.personas] == [
        'culture_fit', 'tech_pressure', 'practical', 'culture_fit'
    ]


def test_build_main_questions_job_title_only_invokes_llm(monkeypatch):
    """직무만 주어져도 그 직무를 LLM 에 넘겨 개인화 질문을 만든다."""
    captured: dict = {}

    async def _fake_generate(company_context, user_context, job_title, personas):
        captured['job_title'] = job_title
        return ['데이터 파이프라인 경험은?', '자기소개?']

    monkeypatch.setattr(llm, 'generate_main_questions', _fake_generate)
    result = asyncio.run(
        service.build_main_questions(2, job_title='데이터 엔지니어')
    )
    assert captured['job_title'] == '데이터 엔지니어'
    assert result.questions == ['데이터 파이프라인 경험은?', '자기소개?']
    assert result.personalized is True


def test_build_main_questions_passes_company_and_user_context_to_llm(monkeypatch):
    """식별자가 주어지면 회사·지원자 컨텍스트를 조회해 LLM 에 함께 넘긴다."""
    captured: dict = {}

    async def _fake_company(db, mongo, company_id):
        captured['company_id'] = company_id
        return '회사명: CJ ENM'

    async def _fake_user(mongo, user_id):
        captured['user_id'] = user_id
        return '[이력서]\n보유 기술: Python'

    async def _fake_generate(company_context, user_context, job_title, personas):
        captured['company_context'] = company_context
        captured['user_context'] = user_context
        captured['job_title'] = job_title
        captured['personas'] = personas
        return ['자기소개?', '파이썬 경험은?']

    monkeypatch.setattr(context, 'get_company_context', _fake_company)
    monkeypatch.setattr(context, 'get_user_context', _fake_user)
    monkeypatch.setattr(llm, 'generate_main_questions', _fake_generate)

    result = asyncio.run(
        service.build_main_questions(
            2,
            company_id='c1',
            user_id='u1',
            job_title='백엔드 개발자',
            db=object(),
            mongo=object(),
        )
    )

    assert result.questions == ['자기소개?', '파이썬 경험은?']
    assert captured['company_id'] == 'c1'
    assert captured['user_id'] == 'u1'
    assert captured['company_context'] == '회사명: CJ ENM'
    assert captured['user_context'] == '[이력서]\n보유 기술: Python'
    assert captured['job_title'] == '백엔드 개발자'
    # count=2 슬롯에 3인 패널이 배정돼 LLM 에 전달되고, 결과에도 병렬로 담긴다.
    assert [p.id for p in captured['personas']] == ['culture_fit', 'tech_pressure']
    assert [p.id for p in result.personas] == ['culture_fit', 'tech_pressure']


def test_build_main_questions_falls_back_on_error(monkeypatch):
    """LLM 장애 시 안전 기본 질문으로 우회한다(면접 안 끊김)."""
    monkeypatch.setattr(
        llm,
        'generate_main_questions',
        AsyncMock(side_effect=RuntimeError('LLM down')),
    )
    result = asyncio.run(service.build_main_questions(4, job_title='백엔드 개발자'))
    assert result.questions == list(context.FALLBACK_MAIN_QUESTIONS)
    # 컨텍스트(직무)는 있었으므로 개인화 면접 — LLM 장애로 질문만 폴백됐다.
    assert result.personalized is True


def test_build_main_questions_falls_back_on_empty(monkeypatch):
    """LLM 이 빈 목록을 주면 기본 질문으로 우회한다(컨텍스트는 있어 personalized=True)."""
    monkeypatch.setattr(llm, 'generate_main_questions', AsyncMock(return_value=[]))
    result = asyncio.run(service.build_main_questions(4, job_title='백엔드 개발자'))
    assert result.questions == list(context.FALLBACK_MAIN_QUESTIONS)
    assert result.personalized is True


def test_build_main_questions_pads_short_result_with_fallback(monkeypatch):
    """LLM 이 count 보다 적게 주면 기본 질문으로 빈 자리를 채운다(중복 제외)."""
    monkeypatch.setattr(
        llm, 'generate_main_questions', AsyncMock(return_value=['회사 맞춤 질문?'])
    )
    result = asyncio.run(service.build_main_questions(3, job_title='백엔드 개발자'))
    questions = result.questions

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


# 꼬리질문 결정론 가드(_MIN_FOLLOW_UP_WORDS)를 통과할 만큼 실질 있는 답변.
_SUBSTANTIVE_ANSWER = '저는 대용량 트래픽을 처리한 경험이 있습니다'


def test_follow_up_empty_answer_returns_none(monkeypatch):
    """답변이 없으면 LLM 없이 꼬리질문을 만들지 않는다(결정론 가드)."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(side_effect=AssertionError('호출 금지'))
    )
    assert asyncio.run(service.generate_follow_up('q', '', CULTURE)) is None


def test_follow_up_thin_answer_skips_without_llm(monkeypatch):
    """인사말·이름만("안녕하세요 박초롱입니다")이면 LLM 도 안 부르고 곧장 None."""
    guard = AsyncMock(side_effect=AssertionError('얕은 답변엔 LLM 호출 금지'))
    monkeypatch.setattr(llm, 'generate_follow_up', guard)
    assert asyncio.run(
        service.generate_follow_up('자기소개', '안녕하세요 박초롱입니다', CULTURE)
    ) is None
    guard.assert_not_awaited()


def test_follow_up_error_returns_none(monkeypatch):
    """꼬리질문 생성 장애는 None — 라우터가 다음 메인으로 우회한다."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(side_effect=RuntimeError('down'))
    )
    assert asyncio.run(
        service.generate_follow_up('q', _SUBSTANTIVE_ANSWER, TECH)
    ) is None


def test_follow_up_returns_trimmed_text(monkeypatch):
    """정상 생성 시(실질 있는 답변) 다듬은 본문을 반환."""
    monkeypatch.setattr(
        llm, 'generate_follow_up', AsyncMock(return_value='  더 구체적으로?  ')
    )
    assert asyncio.run(
        service.generate_follow_up('q', _SUBSTANTIVE_ANSWER, PRACTICAL)
    ) == '더 구체적으로?'


def test_follow_up_skip_sentinel_returns_none(monkeypatch):
    """가드를 통과한 답변이라도 LLM 이 SKIP 을 주면 None(억지 꼬리질문 방지)."""
    for sentinel in ('SKIP', ' skip ', '"SKIP".', 'Skip'):
        monkeypatch.setattr(
            llm, 'generate_follow_up', AsyncMock(return_value=sentinel)
        )
        assert asyncio.run(
            service.generate_follow_up('자기소개', _SUBSTANTIVE_ANSWER, CULTURE)
        ) is None


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


# ── summary_from_result (라이브 summary = 결과 리포트에서 파생) ─────────


def test_summary_from_result_uses_report_overall_score():
    """라이브 종합점수는 결과 리포트의 overall.score 를 그대로 쓴다(단일 채점 소스)."""
    result = _result(
        {
            'overall': {'score': 75, 'grade': 'B', 'headline': 'h'},
            'answer_feedback': {'score': 75, 'summary': '논리적입니다', 'metrics': []},
            'improvements': [
                {'area': '결론', 'problem': 'p', 'method': '결론 먼저 제시'},
            ],
        }
    )
    summary = service.summary_from_result(result, nonverbal.NonverbalMetrics())
    assert summary.type == 'summary'
    assert summary.overall_score == 75.0
    assert summary.language_feedback == '논리적입니다'
    assert summary.improvements == ['결론: 결론 먼저 제시']
    assert summary.nonverbal_feedback  # 안내 문구(빈 값 아님)


def test_summary_from_result_improvement_area_only():
    """method 가 비면 보완점은 area 만으로 한 줄을 만든다(area·method 한쪽만 있는 분기)."""
    result = _result(
        {
            'overall': {'score': 70, 'grade': 'B', 'headline': 'h'},
            'answer_feedback': {'score': 70, 'summary': 's', 'metrics': []},
            'improvements': [{'area': '시간 관리', 'problem': 'p', 'method': ''}],
        }
    )
    summary = service.summary_from_result(result, nonverbal.NonverbalMetrics())
    assert summary.improvements == ['시간 관리']  # method 없으면 area 만


def test_summary_from_result_no_nonverbal_penalty_on_score():
    """비언어(시선이탈)가 있어도 종합점수는 리포트 점수 그대로 — 태도는 문장으로만 얹는다."""
    result = _result(
        {
            'overall': {'score': 90, 'grade': 'A', 'headline': 'h'},
            'answer_feedback': {'score': 90, 'summary': '좋음', 'metrics': []},
        }
    )
    frames = tuple(LandmarkFrameMessage(gaze_x=0.9) for _ in range(5))
    metrics = nonverbal.aggregate(frames, ())
    summary = service.summary_from_result(result, metrics)
    assert summary.overall_score == 90.0  # 감점 없음(결과 페이지와 일치)
    assert '시선' in summary.nonverbal_feedback  # 태도는 문장에 반영


def test_summary_from_result_empty_report_uses_builder_default_feedback():
    """빈 리포트(무응답·LLM 실패)면 language_feedback 은 builder 의 정직한 기본 문구다."""
    result = _result({}, history=(service.Turn('자기소개', '', '', 'common'),))
    summary = service.summary_from_result(result, nonverbal.NonverbalMetrics())
    assert summary.language_feedback == '답변 피드백을 생성하지 못했습니다.'


def test_summary_from_result_survives_describe_error(monkeypatch):
    """비언어 문장 생성이 예외를 던져도 요약은 안내 문구로 계속 생성된다."""
    monkeypatch.setattr(nonverbal, 'describe', Mock(side_effect=RuntimeError('boom')))
    result = _result(
        {
            'overall': {'score': 70, 'grade': 'B', 'headline': 'h'},
            'answer_feedback': {'score': 70, 'summary': 'ok', 'metrics': []},
        }
    )
    summary = service.summary_from_result(result, nonverbal.NonverbalMetrics())
    assert summary.overall_score == 70.0  # 리포트 점수 유지(요약 안 끊김)
    assert '오류' in summary.nonverbal_feedback


def test_summary_from_result_zero_when_no_answers():
    """전부 무응답이면 빈 리포트 → 종합점수 0 으로 정직하게 파생한다."""
    result = _result({}, history=(service.Turn('자기소개', '', '', 'common'),))
    summary = service.summary_from_result(result, nonverbal.NonverbalMetrics())
    assert summary.overall_score == 0.0
    assert summary.improvements == []


# ── 무응답 판별·직렬화 ─────────────────────────────────────────────────


def test_has_any_answer_detects_real_answers():
    """공백만·빈 답변은 무응답으로, 실제 답변이 하나라도 있으면 True."""
    assert not service.has_any_answer(())
    assert not service.has_any_answer(
        (service.Turn('q', '', '', 'common'), service.Turn('q', '  ', '', 'common'))
    )
    assert service.has_any_answer(
        (service.Turn('q', '', '', 'common'), service.Turn('q', '답변함', 'e', 'common'))
    )


def test_format_history_marks_unanswered_turns():
    """무응답 턴은 '(무응답)'으로 명시해 LLM 이 빈 답변을 오해하지 않게 한다."""
    history = (
        service.Turn('q1', '', '', 'common'),
        service.Turn('q2', '실제 답변', 'e', 'common'),
    )
    text = service.format_history(history)
    assert 'A1: (무응답)' in text
    assert 'A2: 실제 답변' in text


# ── warm_question_audio (TTS 선합성) ──────────────────────────────
# 질문 음성을 프론트의 POST /tts 전에 미리 합성해 캐시에 채운다 → 그 요청이 즉시 히트.
# tts._post 를 mock 해 실 ElevenLabs·크레딧을 쓰지 않는다(정규화도 꺼 결정적).


def test_warm_question_audio_fills_cache(monkeypatch):
    """선합성하면 같은 (text, persona) 로 뒤이은 synthesize 가 재합성 없이 캐시 히트한다."""
    tts._cache.clear()
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'k')
    monkeypatch.setattr(settings, 'interview_tts_normalize', False)  # ffmpeg 없이 결정적
    calls = 0

    async def _post(text, voice_id, voice_settings=None):
        nonlocal calls
        calls += 1
        return b'audio-bytes'

    monkeypatch.setattr(tts, '_post', _post)
    # 선합성(라우터가 질문 전송 전/중에 하는 것)
    asyncio.run(service.warm_question_audio('자기소개 부탁드립니다', 'tech_pressure'))
    assert calls == 1
    # 프론트 POST /tts 가 하는 것과 동일한 경로 — 같은 담당 목소리로 재요청
    voice = resolve_persona_voice('tech_pressure')
    out = asyncio.run(
        tts.synthesize('자기소개 부탁드립니다', voice.voice_id, voice.as_voice_settings())
    )
    assert out == b'audio-bytes'
    assert calls == 1  # 재합성 없이 캐시 히트


def test_warm_question_audio_noop_when_disabled(monkeypatch):
    """TTS 비활성이면 합성하지 않는다(빌린 크레딧 과금 0)."""
    tts._cache.clear()
    monkeypatch.setattr(settings, 'interview_tts_enabled', False)
    called = False

    async def _post(*_args, **_kwargs):
        nonlocal called
        called = True
        return b'x'

    monkeypatch.setattr(tts, '_post', _post)
    asyncio.run(service.warm_question_audio('질문', 'tech_pressure'))
    assert called is False


def test_warm_question_audio_swallows_errors(monkeypatch):
    """합성이 실패해도 예외를 삼켜 면접 흐름을 막지 않는다(fire-and-forget 안전)."""
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'k')

    async def _boom(*_args, **_kwargs):
        raise RuntimeError('합성 실패')

    monkeypatch.setattr(tts, 'synthesize', _boom)
    # 예외가 밖으로 새지 않으면 통과(None 반환)
    assert asyncio.run(service.warm_question_audio('질문', 'tech_pressure')) is None
