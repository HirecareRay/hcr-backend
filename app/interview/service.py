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

from app.interview import context, dummy_transcript, llm, nonverbal, stt
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


async def build_main_questions(
    count: int,
    *,
    company_id: str | None = None,
    user_id: str | None = None,
    job_title: str | None = None,
    db: object | None = None,
    mongo: object | None = None,
) -> list[str]:
    """회사·지원자·직무 컨텍스트로 메인 질문을 생성한다(있는 데이터만큼만 개인화).

    company_id·db·mongo 가 주어지면 실제 회사 분석을, user_id·mongo 가 주어지면
    지원자 문서 4종을, job_title 이 오면 지원 직무를 컨텍스트에 합쳐 개인화 질문을
    만든다. 셋 다 비면(회사·지원자·직무 모두 없음) LLM 을 호출하지 않고 곧장 기본
    질문으로 폴백한다 — 불필요한 OpenAI 비용·지연을 막는다. 하나라도 있으면 있는
    것만으로 개인화하고, 개수가 부족하면 기본 질문으로 보충한다.
    """
    company_context = await context.get_company_context(
        db=db, mongo=mongo, company_id=company_id
    )
    user_context = await context.get_user_context(mongo=mongo, user_id=user_id)
    # 직무명은 쿼리스트링으로 들어오는 유일한 무제한 자유 텍스트라 길이를 캡한다 —
    # 빌린 OpenAI 키 비용 남용(거대한 jobTitle 로 프롬프트 토큰 증폭)을 막는 방어선.
    job = (job_title or '').strip()[:100]

    # 빈 컨텍스트 단축 경로 — 회사·지원자·직무가 모두 없으면 LLM 없이 기본 질문.
    if not company_context and not user_context and not job:
        return _ensure_question_count([], count)

    try:
        questions = await llm.generate_main_questions(
            company_context, user_context, job, count
        )
    except RuntimeError as error:
        logger.error('메인 질문 생성 실패, 기본 질문 사용: %s', error)
        questions = []
    return _ensure_question_count(questions, count)


def _ensure_question_count(questions: list[str], count: int) -> list[str]:
    """LLM 질문이 count 에 못 미치면 기본 질문으로 보충한다(중복 제외, 순서 유지).

    LLM 이 빈 결과·부족분을 반환해도 면접이 항상 count 개 질문으로 진행되도록
    안전 기본 질문(FALLBACK_MAIN_QUESTIONS)을 빈 자리에 채워 넣는다(데모 보호).
    """
    filled = list(dict.fromkeys(q for q in questions if q.strip()))
    for fallback in context.FALLBACK_MAIN_QUESTIONS:
        if len(filled) >= count:
            break
        if fallback not in filled:
            filled.append(fallback)
    return filled[:count]


def question_event(question_id: str, text: str) -> QuestionEvent:
    """질문 문자열을 다운스트림 이벤트로 감싼다(TTS 텍스트는 동일)."""
    return QuestionEvent(question_id=question_id, text=text, tts_text=text)


async def transcribe_answer(audio: bytes) -> TranscriptDeltaEvent | None:
    """누적 답변 오디오를 전사해 최종 자막 이벤트로 감싼다(빈 결과면 None)."""
    text = await stt.transcribe_audio(audio)
    if not text:
        return None
    return TranscriptDeltaEvent(delta=text, is_final=True)


def _suffix_delta(previous: str, full: str) -> str:
    """이전에 흘려보낸 자막(previous) 뒤로 새로 늘어난 부분만 반환한다.

    누적 버퍼를 통째로 재전사하면 매번 전체 텍스트가 나오므로, 이미 보낸
    부분을 빼고 새 꼬리만 부분 자막으로 흘린다(프론트는 delta 를 이어 붙인다).
    재전사로 앞부분이 바뀐 드문 경우엔 공통 접두 이후를 보낸다(약간의 글리치 허용).
    """
    if not previous:
        return full
    if full.startswith(previous):
        return full[len(previous):]
    common = 0
    for prev_char, full_char in zip(previous, full):
        if prev_char != full_char:
            break
        common += 1
    return full[common:]


async def transcribe_partial(
    audio: bytes, previous: str
) -> TranscriptDeltaEvent | None:
    """누적 버퍼를 재전사해 이전 자막 뒤 새 부분만 부분 자막(isFinal=False)으로 만든다.

    답변 중 주기적으로 호출된다. 전사 실패·새 내용 없음이면 None 을 돌려
    라우터가 건너뛰게 한다 — 부분 전사 실패가 답변 진행을 막지 않는다(데모 보호).
    """
    try:
        full = await stt.transcribe_audio(audio)
    except RuntimeError as error:
        logger.error('부분 전사 실패(건너뜀): %s', error)
        return None
    delta = _suffix_delta(previous, full)
    return TranscriptDeltaEvent(delta=delta, is_final=False) if delta else None


async def finalize_partial(
    audio: bytes, previous: str
) -> tuple[str, TranscriptDeltaEvent]:
    """부분 자막 모드의 answer_end 마무리 — 최종 전사 후 (답변본문, 종료 자막)을 반환.

    누적 버퍼를 한 번 더 전사해 권위 있는 전체 텍스트를 답변으로 쓰고, 이미 흘린
    부분 자막 뒤 남은 꼬리만 final(isFinal=True) 로 보낸다. 전사 실패·빈 결과면
    그동안 흘린 부분 자막(previous)을 답변으로 쓰고 종료 마커만 보낸다(끊김 방지).
    """
    try:
        full = await stt.transcribe_audio(audio)
    except RuntimeError as error:
        logger.error('최종 전사 실패, 부분 자막을 답변으로 사용: %s', error)
        full = ''
    if not full:
        return previous, TranscriptDeltaEvent(delta='', is_final=True)
    return full, TranscriptDeltaEvent(delta=_suffix_delta(previous, full), is_final=True)


def dummy_transcript_partial(index: int) -> TranscriptDeltaEvent:
    """더미 부분 자막 이벤트(isFinal=False) — 오디오 청크 1개당 토큰 1개.

    프론트가 delta 를 이어 붙여 자막이 흐르게 한다(실 STT 호출 없음, 비용 0).
    """
    return TranscriptDeltaEvent(delta=dummy_transcript.token_at(index), is_final=False)


def transcript_final() -> TranscriptDeltaEvent:
    """자막 종료 마커(isFinal=True, 추가 토큰 없음) — 캡션을 닫는 신호."""
    return TranscriptDeltaEvent(delta='', is_final=True)


def text_answer_transcript(text: str) -> TranscriptDeltaEvent:
    """타이핑 답변을 최종 자막으로 감싼다.

    평가·요약 입력(답변 본문)과 화면 자막의 출처를 하나로 맞춘다 — 텍스트 모드도
    음성 모드와 동일하게 자막(final)이 흐른 뒤 평가가 스트리밍되게 한다.
    """
    return TranscriptDeltaEvent(delta=text, is_final=True)


def dummy_answer_text(chunk_count: int) -> str:
    """흘린 더미 토큰을 합친 답변 본문(평가·요약 입력). 청크 0 이면 빈 답변."""
    return dummy_transcript.answer_text(chunk_count)


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
    penalty, nonverbal_feedback = _safe_nonverbal(metrics)
    return SummaryEvent(
        overall_score=_clamp_score(base_score + penalty),
        language_feedback=str(
            data.get('language_feedback') or '평가를 생성하지 못했습니다.'
        ),
        nonverbal_feedback=nonverbal_feedback,
        improvements=[
            str(item) for item in (data.get('improvements') or []) if str(item).strip()
        ],
    )


def _safe_nonverbal(metrics: NonverbalMetrics) -> tuple[float, str]:
    """비언어 지표를 (감점, 피드백 문장)으로 안전 환산한다.

    nonverbal 모듈은 예외를 던지지 않도록 설계됐지만, 만일의 버그로 환산이 실패해도
    최종 요약이 끊기지 않도록 방어한다(예외 시 0 가감·안내 문구로 우회 — 데모 보호).
    """
    try:
        return nonverbal.score_penalty(metrics), nonverbal.describe(metrics)
    except Exception as error:  # noqa: BLE001 - 비언어 실패가 요약을 막지 않게
        logger.error('비언어 환산 실패, 요약은 계속 진행: %s', error)
        return 0.0, '비언어 분석 중 오류가 발생해 태도 분석을 생략했습니다.'


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
