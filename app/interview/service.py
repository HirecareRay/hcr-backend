"""모의 면접 실시간 WS 비즈니스 로직 — LLM 면접 두뇌(Phase 3).

라우터(WS I/O)와 경계 모듈(llm·stt·context) 사이에서 면접 진행을 조립한다:
회사 컨텍스트로 메인 질문 생성 → 답변 전사 → 평가 토큰 스트림 → 꼬리질문 → 요약.
LLM·STT 장애가 나도 면접이 끊기지 않도록 안전 기본값으로 우회한다(데모 보호).

면접 진행 = B안: `메인질문 → (답변) → 꼬리질문 → (답변) → 다음 메인 → … → 요약`.
메인 질문은 컨텍스트 기반 LLM 생성, 꼬리질문은 직전 답변 기반 LLM 생성이다.
"""

import logging
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.interview import context, llm, nonverbal, stt
from app.interview.nonverbal import NonverbalMetrics
from app.interview.schemas import (
    EvalDeltaEvent,
    QuestionEvent,
    SummaryEvent,
    TranscriptDeltaEvent,
)

logger = logging.getLogger(__name__)

# overall_score 가 비언어 가감 후에도 벗어나면 안 되는 범위.
_SCORE_MIN = 0.0
_SCORE_MAX = 100.0


@dataclass(frozen=True)
class Turn:
    """한 번의 질문-답변-평가 기록. 꼬리질문·요약의 입력이 된다."""

    question: str
    answer: str
    evaluation: str


async def build_main_questions(count: int) -> list[str]:
    """회사 컨텍스트로 메인 질문을 생성한다(LLM 실패 시 안전 기본 질문으로 우회)."""
    company_context = await context.get_company_context()
    try:
        questions = await llm.generate_main_questions(company_context, count)
    except RuntimeError as error:
        logger.error('메인 질문 생성 실패, 기본 질문 사용: %s', error)
        questions = []
    return questions or list(context.FALLBACK_MAIN_QUESTIONS)


def question_event(question_id: str, text: str) -> QuestionEvent:
    """질문 문자열을 다운스트림 이벤트로 감싼다(TTS 텍스트는 동일)."""
    return QuestionEvent(question_id=question_id, text=text, tts_text=text)


async def transcribe_answer(audio: bytes) -> TranscriptDeltaEvent | None:
    """누적 답변 오디오를 전사해 최종 자막 이벤트로 감싼다(빈 결과면 None)."""
    text = await stt.transcribe_audio(audio)
    if not text:
        return None
    return TranscriptDeltaEvent(delta=text, is_final=True)


async def stream_evaluation(
    question: str, answer: str
) -> AsyncIterator[EvalDeltaEvent]:
    """답변 평가를 토큰 단위 EvalDeltaEvent 로 스트리밍한다.

    답변이 비면(무음·인식 실패) 평가를 생략한다 — 없는 답을 평가하지 않는다.
    LLM 장애 시 WS 를 끊지 않고 로깅 후 스트림을 종료한다(데모 보호).
    """
    if not answer:
        return
    try:
        async for delta in llm.stream_evaluation(question, answer):
            yield EvalDeltaEvent(delta=delta)
    except RuntimeError as error:
        logger.error('평가 스트림 실패: %s', error)


async def generate_follow_up(question: str, answer: str) -> str | None:
    """직전 답변 기반 꼬리질문 본문을 만든다(빈 답변·생성 실패면 None).

    None 이면 라우터가 꼬리질문을 건너뛰고 다음 메인 질문으로 넘어간다.
    """
    if not answer:
        return None
    try:
        text = await llm.generate_follow_up(question, answer)
    except RuntimeError as error:
        logger.error('꼬리질문 생성 실패: %s', error)
        return None
    return text.strip() or None


async def build_summary(
    history: tuple[Turn, ...], metrics: NonverbalMetrics | None = None
) -> SummaryEvent:
    """면접 기록과 비언어 지표로 최종 통합 리포트를 만든다.

    LLM 요약 실패 시 안전 기본 요약으로 우회하고, 비언어 지표가 없으면(metrics
    None·빈 집계) 언어 평가만으로 요약한다 — 어느 쪽이 비어도 면접이 끊기지 않는다.
    """
    transcript = _format_history(history)
    try:
        data = await llm.generate_summary(transcript)
    except RuntimeError as error:
        logger.error('요약 생성 실패, 기본 요약 사용: %s', error)
        data = {}
    return _summary_event(data, metrics or NonverbalMetrics())


def _format_history(history: tuple[Turn, ...]) -> str:
    """누적 턴을 LLM 요약 입력용 텍스트로 직렬화한다."""
    blocks = [
        f'Q{i}: {turn.question}\nA{i}: {turn.answer}\n평가{i}: {turn.evaluation}'
        for i, turn in enumerate(history, start=1)
    ]
    return '\n\n'.join(blocks)


def _summary_event(data: dict, metrics: NonverbalMetrics) -> SummaryEvent:
    """LLM 요약 dict 와 비언어 지표를 SummaryEvent 로 안전 변환한다.

    overall_score 는 언어 점수에 비언어 가감치를 더해 0~100 으로 clamp 하고,
    nonverbal_feedback 은 집계를 사람이 읽는 문장으로 채운다(누락 필드는 기본값).
    """
    base_score = _coerce_score(data.get('overall_score'))
    return SummaryEvent(
        overall_score=_clamp_score(base_score + nonverbal.score_penalty(metrics)),
        language_feedback=str(
            data.get('language_feedback') or '평가를 생성하지 못했습니다.'
        ),
        nonverbal_feedback=nonverbal.describe(metrics),
        improvements=[
            str(item) for item in (data.get('improvements') or []) if str(item).strip()
        ],
    )


def _clamp_score(value: float) -> float:
    """점수를 0~100 범위로 제한한다(비언어 가감이 범위를 넘지 않게)."""
    return max(_SCORE_MIN, min(value, _SCORE_MAX))


def _coerce_score(value: object) -> float:
    """점수를 float 으로 강제하되 파싱 불가·비유한(nan/inf)이면 0.0.

    LLM 출력은 신뢰 불가 입력이라 'nan'/'inf' 같은 값이 clamp 를 우회하지
    못하도록 유한성까지 검증한다.
    """
    try:
        score = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0
