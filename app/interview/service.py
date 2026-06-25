"""모의 면접 실시간 WS 비즈니스 로직.

질문 풀·요약은 아직 더미다(Phase 3 LLM·Phase 5 통합 리포트에서 교체). 전사는
Phase 2 에서 실제 STT 로 교체됐다 — answer_end 시 누적 오디오를 stt 모듈로 전사한다.
라우터는 WS I/O 만 담당하고, 다운스트림 이벤트 생성은 여기로 위임한다(레이어 원칙).
"""

from app.interview import stt
from app.interview.schemas import (
    EvalDeltaEvent,
    QuestionEvent,
    SummaryEvent,
    TranscriptDeltaEvent,
)

# 더미 질문 풀 (질문ID, 질문문) — Phase 3에서 LLM 생성으로 교체
_DUMMY_QUESTIONS: tuple[tuple[str, str], ...] = (
    ('q1', '간단히 자기소개 부탁드립니다.'),
    ('q2', '최근에 가장 도전적이었던 경험은 무엇이었나요?'),
)


def question_count() -> int:
    """전체 더미 질문 수."""
    return len(_DUMMY_QUESTIONS)


def question_at(index: int) -> QuestionEvent:
    """index 번째 더미 질문 이벤트 (범위를 벗어나면 IndexError)."""
    question_id, text = _DUMMY_QUESTIONS[index]
    return QuestionEvent(question_id=question_id, text=text, tts_text=text)


async def transcribe_answer(audio: bytes) -> TranscriptDeltaEvent | None:
    """누적 답변 오디오를 전사해 최종 자막 이벤트로 감싼다.

    전사 결과가 비면(무음·인식 실패) None 을 반환해 빈 자막을 내려보내지 않는다.
    Phase 2.5 에서 실시간 부분결과(is_final=False) 스트리밍으로 확장한다.
    """
    text = await stt.transcribe_audio(audio)
    if not text:
        return None
    return TranscriptDeltaEvent(delta=text, is_final=True)


def eval_feedback() -> list[EvalDeltaEvent]:
    """답변 종료 시 더미 평가 스트림 — Phase 3(LLM)에서 실제 토큰 스트림으로 교체."""
    return [EvalDeltaEvent(delta='(더미 평가: 답변 구조가 명확합니다)')]


def final_summary() -> SummaryEvent:
    """면접 종료 시 더미 통합 리포트. Phase 5에서 실제 집계로 교체."""
    return SummaryEvent(
        overall_score=80.0,
        language_feedback='(더미) 답변이 논리적으로 전개되었습니다.',
        nonverbal_feedback='(더미) 시선 처리가 안정적입니다.',
        improvements=['(더미) 결론을 더 명확히 마무리하기'],
    )
