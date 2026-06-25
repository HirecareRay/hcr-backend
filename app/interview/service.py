"""모의 면접 실시간 WS 비즈니스 로직 (Phase 1 — walking skeleton).

지금은 더미 데이터만 생성한다. Phase 2(STT)·3(LLM)·5(통합 리포트)에서 실제
전사·평가·집계로 교체한다. 라우터는 WS I/O 만 담당하고, 다운스트림 이벤트 생성은
여기로 위임한다(레이어 원칙: 라우터에 비즈니스 로직 금지).
"""

from app.interview.schemas import (
    EvalDeltaEvent,
    QuestionEvent,
    SummaryEvent,
    TranscriptDeltaEvent,
)

# 더미 질문 풀 (질문ID, 질문문) — Phase 3에서 LLM 생성으로 교체
_DUMMY_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("q1", "간단히 자기소개 부탁드립니다."),
    ("q2", "최근에 가장 도전적이었던 경험은 무엇이었나요?"),
)


def question_count() -> int:
    """전체 더미 질문 수."""
    return len(_DUMMY_QUESTIONS)


def question_at(index: int) -> QuestionEvent:
    """index 번째 더미 질문 이벤트 (범위를 벗어나면 IndexError)."""
    question_id, text = _DUMMY_QUESTIONS[index]
    return QuestionEvent(question_id=question_id, text=text, tts_text=text)


def answer_feedback() -> list[TranscriptDeltaEvent | EvalDeltaEvent]:
    """답변 종료 시 더미 전사·평가 스트림.

    Phase 2(STT)·3(LLM)에서 실제 토큰 스트림으로 교체한다.
    """
    return [
        TranscriptDeltaEvent(delta="(더미 전사 결과입니다)", is_final=True),
        EvalDeltaEvent(delta="(더미 평가: 답변 구조가 명확합니다)"),
    ]


def audio_ack(byte_count: int) -> TranscriptDeltaEvent:
    """오디오 청크 수신 확인용 더미 자막. Phase 2에서 실제 STT 부분 결과로 교체."""
    return TranscriptDeltaEvent(delta=f"(오디오 {byte_count}바이트 수신)", is_final=False)


def final_summary() -> SummaryEvent:
    """면접 종료 시 더미 통합 리포트. Phase 5에서 실제 집계로 교체."""
    return SummaryEvent(
        overall_score=80.0,
        language_feedback="(더미) 답변이 논리적으로 전개되었습니다.",
        nonverbal_feedback="(더미) 시선 처리가 안정적입니다.",
        improvements=["(더미) 결론을 더 명확히 마무리하기"],
    )
