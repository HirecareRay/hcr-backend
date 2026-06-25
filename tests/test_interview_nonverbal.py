"""비언어 집계 순수함수 단위 테스트 (Phase 4).

프론트(MediaPipe)가 보내는 landmark_frame·event_snapshot 을 받아 누적한 뒤,
시선이탈률·고개흔들림·표정분포·이벤트카운트로 집계하고, 사람이 읽는 피드백
문장과 점수 가감치로 변환하는 경로를 검증한다. 순수함수라 외부 의존성·mock
없이 입력→출력만 본다.

데모 보호 철학: 데이터가 없거나(무음·미장착) None 투성이여도 절대 예외 없이
안전한 기본값으로 우회한다 — LLM/STT 장애 우회와 동일.
"""

from app.interview import nonverbal
from app.interview.schemas import EventSnapshotMessage, LandmarkFrameMessage


def _frame(**kwargs) -> LandmarkFrameMessage:
    return LandmarkFrameMessage(**kwargs)


def _event(name: str) -> EventSnapshotMessage:
    return EventSnapshotMessage(event=name, image='data:,')


# ── aggregate: 빈 입력 / 결측 방어 ────────────────────────────────


def test_aggregate_empty_returns_safe_zero_metrics():
    """프레임·이벤트가 없으면 0 division 없이 안전한 빈 지표."""
    metrics = nonverbal.aggregate((), ())
    assert metrics.frame_count == 0
    assert metrics.gaze_off_ratio == 0.0
    assert metrics.head_movement == 0.0
    assert metrics.expression_dist == {}
    assert metrics.event_counts == {}


def test_aggregate_all_none_fields_do_not_crash():
    """모든 지표가 None 인 프레임만 와도 예외 없이 집계된다(미장착 카메라)."""
    metrics = nonverbal.aggregate((_frame(), _frame()), ())
    assert metrics.frame_count == 2
    assert metrics.gaze_off_ratio == 0.0  # 유효 gaze 표본 없음 → 0
    assert metrics.head_movement == 0.0
    assert metrics.expression_dist == {}


# ── aggregate: 시선이탈률 ─────────────────────────────────────────


def test_aggregate_gaze_off_ratio_counts_out_of_center_frames():
    """중앙 임계 밖(|gaze|>임계) 프레임 비율을 센다 — None 표본은 분모 제외."""
    frames = (
        _frame(gaze_x=0.0, gaze_y=0.0),  # 중앙
        _frame(gaze_x=0.9, gaze_y=0.0),  # 이탈
        _frame(gaze_x=0.0, gaze_y=-0.9),  # 이탈
        _frame(),  # gaze 결측 → 분모에서 제외
    )
    metrics = nonverbal.aggregate(frames, ())
    assert metrics.frame_count == 4
    assert metrics.gaze_off_ratio == 2 / 3  # 유효 3개 중 2개 이탈


# ── aggregate: 고개흔들림 ─────────────────────────────────────────


def test_aggregate_head_movement_zero_when_steady():
    """고개각이 일정하면 흔들림 0."""
    frames = tuple(_frame(head_yaw=10.0, head_pitch=5.0, head_roll=0.0) for _ in range(3))
    metrics = nonverbal.aggregate(frames, ())
    assert metrics.head_movement == 0.0


def test_aggregate_head_movement_positive_when_varied():
    """고개각이 흔들리면 흔들림 > 0."""
    frames = (
        _frame(head_yaw=-30.0),
        _frame(head_yaw=30.0),
        _frame(head_yaw=-30.0),
    )
    metrics = nonverbal.aggregate(frames, ())
    assert metrics.head_movement > 0.0


# ── aggregate: 표정분포 / 이벤트카운트 ────────────────────────────


def test_aggregate_expression_distribution_is_normalized():
    """표정 값별 비율을 합 1.0 으로 정규화한다."""
    frames = (
        _frame(expression='neutral'),
        _frame(expression='neutral'),
        _frame(expression='smile'),
        _frame(),  # 결측은 분모 제외
    )
    metrics = nonverbal.aggregate(frames, ())
    assert metrics.expression_dist == {'neutral': 2 / 3, 'smile': 1 / 3}


def test_aggregate_event_counts_tally_by_name():
    """이벤트는 이름별로 집계한다."""
    events = (_event('gaze_away'), _event('gaze_away'), _event('no_expression'))
    metrics = nonverbal.aggregate((), events)
    assert metrics.event_counts == {'gaze_away': 2, 'no_expression': 1}


# ── describe: 사람이 읽는 피드백 문장 ─────────────────────────────


def test_describe_returns_safe_message_when_no_data():
    """데이터가 없으면 placeholder 가 아닌 '부족' 안내 문구."""
    text = nonverbal.describe(nonverbal.aggregate((), ()))
    assert text
    assert 'Phase' not in text  # 옛 자리표시 잔재 없음


def test_describe_mentions_gaze_when_off_ratio_high():
    """시선이탈이 잦으면 피드백에 시선 관련 언급이 포함된다."""
    frames = (_frame(gaze_x=0.9), _frame(gaze_x=0.9))
    text = nonverbal.describe(nonverbal.aggregate(frames, ()))
    assert '시선' in text


# ── score_penalty: 점수 가감치 ────────────────────────────────────


def test_score_penalty_zero_without_data():
    """데이터가 없으면 점수에 영향을 주지 않는다(0.0)."""
    assert nonverbal.score_penalty(nonverbal.aggregate((), ())) == 0.0


def test_score_penalty_negative_when_gaze_unstable():
    """시선이탈이 심하면 감점(음수)된다."""
    frames = tuple(_frame(gaze_x=0.9) for _ in range(5))
    assert nonverbal.score_penalty(nonverbal.aggregate(frames, ())) < 0.0


def test_score_penalty_bounded():
    """감점은 과도하게 커지지 않도록 하한이 있다(점수 왜곡 방지)."""
    frames = tuple(_frame(gaze_x=1.0) for _ in range(100))
    events = tuple(_event('gaze_away') for _ in range(100))
    penalty = nonverbal.score_penalty(nonverbal.aggregate(frames, events))
    assert penalty >= nonverbal.MAX_PENALTY
