"""모의 면접 실시간 계약 스키마 테스트 (Phase 0).

세션 상태머신과 WS/SSE 메시지 스키마의 직렬화·역직렬화 계약을 고정한다.
핵심 회귀 방지 포인트:
  - 업스트림(브라우저→서버): raw snake_case 키 그대로 파싱 (camel 변환 금지)
  - 다운스트림(서버→브라우저): 페이로드 키는 camelCase 직렬화, 단 type 판별값은 snake 유지
  - discriminated union: type 필드로 올바른 모델 복원, 잘못된 type 은 거부
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.interview.schemas import (
    ControlAction,
    ControlMessage,
    DownstreamEvent,
    EvalDeltaEvent,
    EventSnapshotMessage,
    LandmarkFrameMessage,
    QuestionEvent,
    SessionStatus,
    SummaryEvent,
    TranscriptDeltaEvent,
    UpstreamMessage,
)

upstream_adapter: TypeAdapter = TypeAdapter(UpstreamMessage)
downstream_adapter: TypeAdapter = TypeAdapter(DownstreamEvent)


# ── 상태머신 Enum ──────────────────────────────────────────────────


def test_session_status_values_match_contract():
    """CLAUDE.md 계약: idle→question→answering→evaluating→finished→summary."""
    assert {s.value for s in SessionStatus} == {
        "idle",
        "question",
        "answering",
        "evaluating",
        "finished",
        "summary",
    }


def test_control_action_values_match_contract():
    assert {a.value for a in ControlAction} == {"answer_start", "answer_end", "next"}


# ── 업스트림: raw snake_case 키 그대로 파싱 ─────────────────────────


def test_upstream_control_parses_raw_snake_keys():
    msg = upstream_adapter.validate_python(
        {"type": "control", "action": "answer_start"}
    )
    assert isinstance(msg, ControlMessage)
    assert msg.action is ControlAction.ANSWER_START


def test_upstream_landmark_frame_parses():
    msg = upstream_adapter.validate_python(
        {"type": "landmark_frame", "gaze_x": 0.1, "head_yaw": -5.0}
    )
    assert isinstance(msg, LandmarkFrameMessage)
    assert msg.gaze_x == 0.1
    assert msg.head_yaw == -5.0


def test_upstream_event_snapshot_parses():
    """이미지 없이 종류·메타만 보내도 검증을 통과해야 한다(프론트가 image 를 안 보냄)."""
    msg = upstream_adapter.validate_python(
        {
            "type": "event_snapshot",
            "event": "gaze_away",
            "meta": {"duration_ms": 1200},
        }
    )
    assert isinstance(msg, EventSnapshotMessage)
    assert msg.event == "gaze_away"
    assert msg.meta == {"duration_ms": 1200}


def test_upstream_does_not_use_camel_alias():
    """업스트림은 브라우저가 보내는 raw snake 키를 그대로 받아야 한다.

    camelCase 키(headYaw)로는 들어오지 않으며(무시됨), snake 키만 채워진다.
    """
    msg = upstream_adapter.validate_python({"type": "landmark_frame", "headYaw": 9.9})
    assert isinstance(msg, LandmarkFrameMessage)
    assert msg.head_yaw is None  # camel 키는 매핑되지 않음


def test_upstream_rejects_unknown_type():
    with pytest.raises(ValidationError):
        upstream_adapter.validate_python({"type": "audio_chunk", "data": "x"})


# ── 다운스트림: 페이로드 camel 직렬화 + type 값 snake 유지 ──────────


def test_downstream_question_serializes_to_camel():
    event = QuestionEvent(question_id="q1", text="자기소개 해주세요", tts_text="자기소개")
    dumped = event.model_dump(by_alias=True)
    assert dumped["questionId"] == "q1"
    assert dumped["ttsText"] == "자기소개"
    assert dumped["type"] == "question"  # 판별값은 snake 유지
    assert dumped["kind"] == "main"  # 기본은 메인 질문
    assert dumped["isLast"] is False  # 기본은 마지막 질문 아님


def test_downstream_question_kind_follow_up():
    event = QuestionEvent(
        question_id="f0", text="그때 어떤 갈등이 있었나요", kind="follow_up"
    )
    dumped = event.model_dump(by_alias=True)
    assert dumped["kind"] == "follow_up"


def test_downstream_question_is_last_serializes_to_camel():
    """마지막 질문은 is_last=True → 프론트로 isLast 로 나간다(버튼 '결과 보기' 전환용)."""
    event = QuestionEvent(question_id="m3", text="마지막 한마디 부탁드립니다", is_last=True)
    dumped = event.model_dump(by_alias=True)
    assert dumped["isLast"] is True


def test_downstream_transcript_delta_serializes_to_camel():
    event = TranscriptDeltaEvent(delta="안녕", is_final=False)
    dumped = event.model_dump(by_alias=True)
    assert dumped["isFinal"] is False
    assert dumped["type"] == "transcript_delta"  # 값은 snake 유지


def test_downstream_summary_serializes_to_camel():
    event = SummaryEvent(
        overall_score=82.5,
        language_feedback="논리적이나 결론이 약함",
        nonverbal_feedback="시선 처리 양호",
        improvements=["결론 강화", "속도 조절"],
    )
    dumped = event.model_dump(by_alias=True)
    assert dumped["overallScore"] == 82.5
    assert dumped["languageFeedback"] == "논리적이나 결론이 약함"
    assert dumped["nonverbalFeedback"] == "시선 처리 양호"
    assert dumped["improvements"] == ["결론 강화", "속도 조절"]
    assert dumped["type"] == "summary"


# ── discriminated union round-trip ─────────────────────────────────


def test_downstream_round_trip_restores_type():
    """camel 직렬화 → 역검증 시 동일 모델로 복원된다 (populate_by_name)."""
    original = EvalDeltaEvent(delta="구체적인 사례가 좋습니다")
    dumped = original.model_dump(by_alias=True)
    restored = downstream_adapter.validate_python(dumped)
    assert isinstance(restored, EvalDeltaEvent)
    assert restored.delta == "구체적인 사례가 좋습니다"


def test_downstream_rejects_unknown_type():
    with pytest.raises(ValidationError):
        downstream_adapter.validate_python({"type": "nope", "delta": "x"})


def test_upstream_round_trip_restores_type():
    original = ControlMessage(action=ControlAction.NEXT)
    restored = upstream_adapter.validate_python(original.model_dump())
    assert isinstance(restored, ControlMessage)
    assert restored.action is ControlAction.NEXT
