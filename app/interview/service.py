"""모의 면접 실시간 WS 비즈니스 로직 — LLM 면접 두뇌(Phase 3).

라우터(WS I/O)와 경계 모듈(llm·stt·context) 사이에서 면접 진행을 조립한다:
회사 컨텍스트로 메인 질문 생성 → 답변 전사 → 평가 토큰 스트림 → 꼬리질문 → 요약.
LLM·STT 장애가 나도 면접이 끊기지 않도록 안전 기본값으로 우회한다(데모 보호).

면접 진행 = B안: `메인질문 → (답변) → 꼬리질문 → (답변) → 다음 메인 → … → 요약`.
메인 질문은 컨텍스트 기반 LLM 생성, 꼬리질문은 직전 답변 기반 LLM 생성이다.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from app.interview import context, dummy_transcript, llm, nonverbal, stt
from app.interview.nonverbal import NonverbalMetrics
from app.interview.personas import Persona, assign_interviewers
from app.interview.result_schemas import ImprovementItem, InterviewResult
from app.interview.schemas import (
    EvalDeltaEvent,
    QuestionEvent,
    SummaryEvent,
    TranscriptDeltaEvent,
)

logger = logging.getLogger(__name__)

# 꼬리질문을 붙일 최소 답변 길이(공백 기준 단어 수). 이보다 짧으면(예: 인사말·
# 이름만 "안녕하세요 박초롱입니다") LLM 을 호출하지 않고 결정론적으로 건너뛴다 —
# 사소한 단어를 붙들고 되묻는 헛질문을 원천 차단하고 불필요한 OpenAI 호출도 막는다.
_MIN_FOLLOW_UP_WORDS = 3


@dataclass(frozen=True)
class MainQuestionSet:
    """생성된 메인 질문과 개인화 여부.

    personalized 는 회사·지원자문서·직무 중 하나라도 컨텍스트가 있어 LLM 개인화
    질문을 만들었는지다. 셋 다 없어 기본질문으로만 채운 '순수 폴백' 면접이면 False —
    이 경우 라우터가 꼬리질문도 붙이지 않아 완전 결정론적·OpenAI 호출 0 기본 면접이
    된다(빌린 키 비용 최소화). 하나라도 있으면 True(꼬리질문 진행).

    personas 는 questions 와 인덱스가 1:1 병렬인 담당 면접관 목록이다(3인 패널
    라운드로빈). personas[i] 가 questions[i] 를 던진 면접관 — 그 질문의 꼬리질문도
    같은 면접관이 이어간다. 순수 폴백 면접에도 채워, 기본질문에도 면접관 배지·목소리를
    붙인다(결정 1). 하위호환을 위해 기본값은 빈 리스트다(mock 이 생략 가능).
    """

    questions: list[str]
    personalized: bool
    personas: list[Persona] = field(default_factory=list)


@dataclass(frozen=True)
class Turn:
    """한 번의 질문-답변-평가 기록. 꼬리질문·요약·결과 스크립트의 입력이 된다.

    category 는 결과 스크립트(ScriptItem.category)용 분류 폴백값이다. WS 진행 경로는
    질문의 분류를 모르므로 기본 'common' 으로 둔다 — 실제 company/job/common 분류는
    결과 조립 시 LLM 이 질문 텍스트를 보고 후분류한다(result_builder 가 LLM 분류를
    우선 적용하고, 없으면 이 값으로 폴백). 추후 메인 질문 생성 시 태깅을 붙이면 그
    값이 폴백으로 쓰인다.
    """

    question: str
    answer: str
    evaluation: str
    category: str = 'common'


async def build_main_questions(
    count: int,
    *,
    company_id: str | None = None,
    user_id: str | None = None,
    job_title: str | None = None,
    db: object | None = None,
    mongo: object | None = None,
) -> MainQuestionSet:
    """회사·지원자·직무 컨텍스트로 메인 질문을 생성한다(있는 데이터만큼만 개인화).

    company_id·db·mongo 가 주어지면 실제 회사 분석을, user_id·mongo 가 주어지면
    지원자 문서 4종을, job_title 이 오면 지원 직무를 컨텍스트에 합쳐 개인화 질문을
    만든다. 셋 다 비면(회사·지원자·직무 모두 없음) LLM 을 호출하지 않고 곧장 기본
    질문으로 폴백한다 — 불필요한 OpenAI 비용·지연을 막는다. 하나라도 있으면 있는
    것만으로 개인화하고, 개수가 부족하면 기본 질문으로 보충한다.

    반환값의 personalized 로 순수 폴백(컨텍스트 전무) 여부를 알려, 라우터가 그 경우
    꼬리질문까지 생략하도록 한다(완전 결정론적 기본 면접, OpenAI 호출 0).
    """
    company_context = await context.get_company_context(
        db=db, mongo=mongo, company_id=company_id
    )
    user_context = await context.get_user_context(mongo=mongo, user_id=user_id)
    # 직무명은 쿼리스트링으로 들어오는 유일한 무제한 자유 텍스트라 길이를 캡한다 —
    # 빌린 OpenAI 키 비용 남용(거대한 jobTitle 로 프롬프트 토큰 증폭)을 막는 방어선.
    job = (job_title or '').strip()[:100]

    # 3인 패널을 슬롯 순서대로 배정한다(Q1=인사, 이후 기술→실무→인사 로테이션).
    # 순수 폴백 면접에도 채워 기본질문에 면접관 배지·목소리를 붙인다(결정 1).
    personas = assign_interviewers(count)

    # 빈 컨텍스트 단축 경로 — 회사·지원자·직무가 모두 없으면 LLM 없이 기본 질문.
    if not company_context and not user_context and not job:
        return MainQuestionSet(
            _ensure_question_count([], count), personalized=False, personas=personas
        )

    try:
        questions = await llm.generate_main_questions(
            company_context, user_context, job, personas
        )
    except RuntimeError as error:
        logger.error('메인 질문 생성 실패, 기본 질문 사용: %s', error)
        questions = []
    return MainQuestionSet(
        _ensure_question_count(questions, count), personalized=True, personas=personas
    )


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


def question_event(
    question_id: str,
    text: str,
    kind: Literal['main', 'follow_up'] = 'main',
    is_last: bool = False,
    persona: Persona | None = None,
) -> QuestionEvent:
    """질문 문자열을 다운스트림 이벤트로 감싼다(TTS 텍스트는 동일).

    kind 는 메인(기본) 질문인지 꼬리질문인지 — 프론트가 흐름을 표시하는 데 쓴다.
    is_last 는 면접의 마지막 질문 여부 — 프론트가 답변 후 버튼을 "결과 보기"로
    바꾸고 그 답변의 next 에서 summary 를 기대한다. persona 는 이 질문을 던진 면접관
    (인사·기술·실무) — 프론트가 질문 배지(role_label)·TTS 목소리(voice)에 쓴다.
    None 이면(mock·구버전 경로) 페르소나 필드를 비운 채 내려간다.
    """
    persona_fields = (
        {
            'persona_id': persona.id,
            'role_label': persona.role_label,
            'voice': persona.voice,
        }
        if persona is not None
        else {}
    )
    return QuestionEvent(
        question_id=question_id,
        text=text,
        tts_text=text,
        kind=kind,
        is_last=is_last,
        **persona_fields,
    )


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


async def generate_follow_up(
    question: str, answer: str, persona: Persona
) -> str | None:
    """직전 답변 기반 꼬리질문 본문을 만든다(빈 답변·부실한 답·생성 실패면 None).

    persona 는 그 메인질문을 던진 면접관 — 꼬리질문도 같은 담당자의 말투를 유지한다.
    None 이면 라우터가 꼬리질문을 건너뛰고 다음 메인 질문으로 넘어간다.

    두 겹으로 억지 꼬리질문을 막는다: (1) 결정론적 전처리 — 답변이 너무 짧으면
    (인사말·이름만) LLM 도 부르지 않고 곧장 None(비용 0). (2) LLM SKIP — 통과한
    답변이라도 파고들 내용이 없으면 LLM 이 SKIP 을 반환하고, 이를 감지해 None 으로 바꾼다.
    """
    if _is_too_thin_for_follow_up(answer):
        return None
    try:
        text = await llm.generate_follow_up(question, answer, persona)
    except RuntimeError as error:
        logger.error('꼬리질문 생성 실패: %s', error)
        return None
    follow_up = text.strip()
    return None if _is_skip_sentinel(follow_up) else follow_up or None


def _is_too_thin_for_follow_up(answer: str) -> bool:
    """꼬리질문을 붙이기엔 답변이 너무 얕은지(빈 답변·인사말·이름만) 결정론적으로 판별.

    공백 기준 단어 수가 _MIN_FOLLOW_UP_WORDS 미만이면 True — "안녕하세요 박초롱입니다"
    같은 자기소개 인사만으로는 파고들 실질 내용이 없다고 보고 LLM 호출 없이 건너뛴다.
    통과한(길이 충분한) 답변의 실질 여부 판단은 LLM SKIP 규칙이 이어서 맡는다.
    """
    return len(answer.split()) < _MIN_FOLLOW_UP_WORDS


def _is_skip_sentinel(text: str) -> bool:
    """LLM 이 '파고들 내용 없음'으로 돌려준 SKIP 신호인지 판별한다.

    모델이 SKIP 을 따옴표·마침표와 함께 낼 수 있어(예: '"SKIP".') 앞뒤 기호를
    벗기고 대소문자 무시로 비교한다. 정상 질문이 우연히 SKIP 으로 시작하는 일은 없다.
    """
    normalized = text.strip().strip('\'"`.!? ').upper()
    return normalized == 'SKIP'


def summary_from_result(
    result: InterviewResult, metrics: NonverbalMetrics
) -> SummaryEvent:
    """결과 리포트(계약 ④)에서 라이브 summary(계약 ②)를 파생한다 — 단일 채점 소스.

    라이브 종합점수는 결과 페이지의 overall.score 를 그대로 쓴다. 두 화면(끝나자마자
    보는 요약·나중에 조회하는 결과 페이지)의 종합점수가 항상 일치하도록, LLM 리포트
    1회를 유일한 채점 소스로 삼는다. 비언어(표정)는 결과 페이지에서 별도 모달로
    점수화하므로 여기 종합점수엔 섞지 않고, 태도 피드백 문장으로만 얹는다(describe).
    """
    return SummaryEvent(
        overall_score=float(result.overall.score),
        language_feedback=result.feedback.answer.summary,
        nonverbal_feedback=_safe_describe(metrics),
        improvements=_improvement_lines(result.improvements),
    )


def fallback_summary(metrics: NonverbalMetrics) -> SummaryEvent:
    """결과 조립이 실패했을 때 내려보내는 안전 기본 요약(면접이 끊기지 않게 — 데모 보호).

    리포트 조립(result_builder)은 예외-안전으로 설계됐지만, 만일의 버그로 조립이
    실패해도 면접이 요약 없이 끊기지 않도록 오케스트레이터가 이 폴백으로 우회한다.
    비언어 문장은 살아 있으면 그대로 얹는다(_safe_describe 가 예외도 흡수).
    """
    return SummaryEvent(
        overall_score=0.0,
        language_feedback='결과 요약을 생성하지 못했습니다.',
        nonverbal_feedback=_safe_describe(metrics),
        improvements=[],
    )


def is_answered(turn: Turn) -> bool:
    """그 턴에 실질 답변이 있는지 — 빈 답변·공백만이면 '무응답'으로 본다.

    답변을 건너뛴(control:next 로 넘긴) 턴은 answer='' 로 기록되므로, 강점·평가를
    이 무응답 턴에서 지어내지 않도록 판별 기준을 한 곳에 둔다.
    """
    return bool(turn.answer.strip())


def has_any_answer(history: tuple[Turn, ...]) -> bool:
    """면접 기록에 실질 답변이 하나라도 있는지(전부 무응답이면 False).

    전부 무응답이면 리포트 LLM 을 호출하지 않고 빈 결과로 우회한다 — 없는 강점·
    점수를 지어내지 않고, 빌린 OpenAI 키도 낭비하지 않는다.
    """
    return any(is_answered(turn) for turn in history)


def format_history(history: tuple[Turn, ...]) -> str:
    """누적 턴을 LLM 요약·리포트 입력용 텍스트로 직렬화한다(요약·결과 공용 공개 헬퍼).

    무응답 턴은 답변을 '(무응답)'으로 명시해, LLM 이 빈 답변을 그럴듯한 답으로
    오해해 강점·점수를 지어내지 않게 한다(정직한 입력 — 근거 없는 평가 차단).
    """
    blocks = [
        f'Q{i}: {turn.question}\nA{i}: {turn.answer.strip() or "(무응답)"}\n평가{i}: {turn.evaluation}'
        for i, turn in enumerate(history, start=1)
    ]
    return '\n\n'.join(blocks)


def _safe_describe(metrics: NonverbalMetrics) -> str:
    """비언어 지표를 사람이 읽는 태도 문장으로 안전 변환한다(실패해도 요약을 막지 않음).

    nonverbal 모듈은 예외를 던지지 않도록 설계됐지만, 만일의 버그로 환산이 실패해도
    최종 요약이 끊기지 않도록 방어한다(예외 시 안내 문구로 우회 — 데모 보호).
    """
    try:
        return nonverbal.describe(metrics)
    except Exception as error:  # noqa: BLE001 - 비언어 실패가 요약을 막지 않게
        logger.error('비언어 문장 생성 실패, 태도 분석 생략: %s', error)
        return '비언어 분석 중 오류가 발생해 태도 분석을 생략했습니다.'


def _improvement_lines(items: list[ImprovementItem]) -> list[str]:
    """결과 리포트의 보완점(area·method)을 라이브 summary 용 한 줄 문자열로 평탄화한다."""
    lines: list[str] = []
    for item in items:
        area = item.area.strip()
        method = item.method.strip()
        if area and method:
            lines.append(f'{area}: {method}')
        elif area or method:
            lines.append(area or method)
    return lines
