"""비언어 신호 집계 — landmark_frame·event_snapshot → 면접 태도 지표 (Phase 4).

프론트(MediaPipe)가 ~1s 주기로 보내는 얼굴 랜드마크와 이벤트 스냅샷을 받아
시선이탈률·고개흔들림·표정분포·이벤트카운트로 집계하고, 사람이 읽는 피드백
문장(describe)과 최종 점수 가감치(score_penalty)로 변환한다.

레이어 원칙: 부수효과 없는 순수함수 모듈(context·llm·stt 와 같은 경계 계층).
라우터는 누적만, 서비스는 조립만, 집계 수식은 전부 여기에 격리한다.

데모 보호: 입력이 비거나 모든 필드가 None 이어도 절대 예외를 던지지 않고
안전한 0 지표로 우회한다 — 비언어가 없다고 면접/요약이 끊기지 않게 한다.
"""

from collections import Counter
from dataclasses import dataclass, field
from statistics import pstdev

from app.interview.schemas import EventSnapshotMessage, LandmarkFrameMessage

# 시선이 화면 중앙에서 이 값을 넘게 벗어나면 "이탈"로 본다(정규화 좌표, 0=중앙).
GAZE_OFF_THRESHOLD = 0.3
# 시선이탈률 100% 일 때의 최대 감점, 고개흔들림·부정 이벤트의 감점 단위.
GAZE_PENALTY_WEIGHT = 12.0
HEAD_PENALTY_WEIGHT = 8.0
EVENT_PENALTY_UNIT = 0.5
# 비언어가 점수를 왜곡하지 않도록 둔 감점 하한(아무리 나빠도 이보다 더 깎지 않음).
MAX_PENALTY = -20.0
# 고개흔들림 표준편차(deg)를 0~1 로 정규화할 때의 기준치.
HEAD_MOVEMENT_SCALE = 30.0
# describe 가 "높음/큼"으로 표현할 비율·움직임 임계(피드백 문구용 — 좌표 임계와 무관).
_FEEDBACK_GAZE_RATIO = 0.3
_FEEDBACK_HEAD_MOVEMENT = 0.3


@dataclass(frozen=True)
class NonverbalMetrics:
    """집계된 비언어 지표(불변). 모든 필드는 안전 기본값을 가진다."""

    frame_count: int = 0
    gaze_off_ratio: float = 0.0
    head_movement: float = 0.0
    expression_dist: dict[str, float] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        """집계할 신호가 하나라도 있었는지."""
        return self.frame_count > 0 or bool(self.event_counts)


def aggregate(
    frames: tuple[LandmarkFrameMessage, ...],
    events: tuple[EventSnapshotMessage, ...],
) -> NonverbalMetrics:
    """누적된 랜드마크·이벤트를 비언어 지표로 집계한다(결측·빈입력 안전)."""
    return NonverbalMetrics(
        frame_count=len(frames),
        gaze_off_ratio=_gaze_off_ratio(frames),
        head_movement=_head_movement(frames),
        expression_dist=_expression_dist(frames),
        event_counts=dict(Counter(event.event for event in events)),
    )


def describe(metrics: NonverbalMetrics) -> str:
    """집계 지표를 사람이 읽는 비언어 피드백 문장으로 변환한다."""
    if not metrics.has_data:
        return '비언어 데이터가 충분하지 않아 태도 분석을 생략했습니다.'

    parts: list[str] = []
    if metrics.gaze_off_ratio >= _FEEDBACK_GAZE_RATIO:
        parts.append(f'시선이 화면을 벗어난 비율이 {metrics.gaze_off_ratio:.0%}로 높습니다')
    else:
        parts.append(f'시선 안정도가 양호합니다(이탈 {metrics.gaze_off_ratio:.0%})')
    if metrics.head_movement >= _FEEDBACK_HEAD_MOVEMENT:
        parts.append('고개 움직임이 다소 큽니다')
    if metrics.expression_dist:
        top = max(metrics.expression_dist, key=lambda label: metrics.expression_dist[label])
        parts.append(f'가장 자주 나타난 표정은 "{top}"입니다')
    if metrics.event_counts:
        summary = ', '.join(f'{name} {count}회' for name, count in metrics.event_counts.items())
        parts.append(f'주요 이벤트: {summary}')
    return '. '.join(parts) + '.'


def score_penalty(metrics: NonverbalMetrics) -> float:
    """비언어 지표를 overall_score 에 더할 가감치로 환산한다(데이터 없으면 0.0)."""
    if not metrics.has_data:
        return 0.0
    penalty = (
        -metrics.gaze_off_ratio * GAZE_PENALTY_WEIGHT
        - min(metrics.head_movement, 1.0) * HEAD_PENALTY_WEIGHT
        - sum(metrics.event_counts.values()) * EVENT_PENALTY_UNIT
    )
    return max(penalty, MAX_PENALTY)


def _gaze_off_ratio(frames: tuple[LandmarkFrameMessage, ...]) -> float:
    """gaze 가 기록된 프레임 중 중앙 임계를 벗어난 비율(유효 표본 없으면 0)."""
    samples = [
        (frame.gaze_x, frame.gaze_y)
        for frame in frames
        if frame.gaze_x is not None or frame.gaze_y is not None
    ]
    if not samples:
        return 0.0
    off = sum(1 for gx, gy in samples if _is_gaze_off(gx) or _is_gaze_off(gy))
    return off / len(samples)


def _is_gaze_off(value: float | None) -> bool:
    """단일 축 시선이 중앙 임계를 벗어났는지(결측은 이탈 아님)."""
    return value is not None and abs(value) > GAZE_OFF_THRESHOLD


def _head_movement(frames: tuple[LandmarkFrameMessage, ...]) -> float:
    """yaw·pitch·roll 표준편차 평균을 0~1 로 정규화한 고개흔들림(표본<2면 0)."""
    axes = (
        [f.head_yaw for f in frames if f.head_yaw is not None],
        [f.head_pitch for f in frames if f.head_pitch is not None],
        [f.head_roll for f in frames if f.head_roll is not None],
    )
    stdevs = [pstdev(values) for values in axes if len(values) >= 2]
    if not stdevs:
        return 0.0
    return min(sum(stdevs) / len(stdevs) / HEAD_MOVEMENT_SCALE, 1.0)


def _expression_dist(frames: tuple[LandmarkFrameMessage, ...]) -> dict[str, float]:
    """표정 값별 비율(합 1.0). 표정이 기록된 프레임만 분모로 센다."""
    labels = [f.expression for f in frames if f.expression is not None]
    if not labels:
        return {}
    total = len(labels)
    return {label: count / total for label, count in Counter(labels).items()}
