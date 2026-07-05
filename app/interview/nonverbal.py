"""비언어 신호 집계 — landmark_frame·event_snapshot → 면접 태도 지표 (Phase 4).

프론트(MediaPipe)가 ~1s 주기로 보내는 얼굴 랜드마크와 이벤트(종류·메타)를 받아
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

from app.core.config import settings
from app.interview.result_schemas import FeedbackMetric, ModalFeedback
from app.interview.schemas import EventSnapshotMessage, LandmarkFrameMessage


def _min_expression_frames() -> int:
    """표정 모달을 낼 최소 얼굴 신호 프레임 수(1 하한 — 0 프레임은 절대 데이터가 아니다).

    설정(interview_min_expression_frames)으로 튜닝한다. 카메라가 잠깐만 켜져 1~2 프레임만
    잡힌 경우를 '데이터 부족'으로 눌러, 표본이 빈약한데 자신만만한 점수가 나오지 않게 한다.
    """
    return max(1, settings.interview_min_expression_frames)

# 결과 표정 모달에서 이벤트 1건당 깎는 '주의 집중' 점수(0 하한).
_EVENT_ATTENTION_PENALTY = 5

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

    frame_count: int = 0  # 수신한 landmark_frame 수(얼굴 미검출 빈 프레임 포함)
    detected_frames: int = 0  # 그중 얼굴 신호가 실제로 있던 프레임 수
    gaze_off_ratio: float = 0.0
    head_movement: float = 0.0
    expression_dist: dict[str, float] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        """점수를 낼 만큼 실제 얼굴 신호가 충분히 쌓였는지.

        frame_count(수신 수)가 아니라 detected_frames(얼굴이 잡힌 프레임)를 최소 표본
        기준(_min_expression_frames)과 비교한다. 카메라 가림·미검출로 전 필드 None 인
        빈 프레임만 오면 신호가 0 이라 False. 잠깐 켜져 1~2 프레임만 잡힌 경우도 기준
        미만이면 False — 그래야 to_modal_feedback 이 빈 모달로 정직하게 비우고, 시선이탈
        0%·흔들림 0(또는 표본 1개)을 '완벽'으로 오해해 점수를 가짜로 채우지 않는다.

        이벤트만 있고 얼굴 프레임이 없으면 시선·자세를 낼 근거가 없어(빈 표본은 만점으로
        오해될 뿐) 데이터로 보지 않는다 — 이벤트는 프레임이 충분할 때 주의집중 지표로만 얹는다.
        """
        return self.detected_frames >= _min_expression_frames()


def aggregate(
    frames: tuple[LandmarkFrameMessage, ...],
    events: tuple[EventSnapshotMessage, ...],
) -> NonverbalMetrics:
    """누적된 랜드마크·이벤트를 비언어 지표로 집계한다(결측·빈입력 안전)."""
    return NonverbalMetrics(
        frame_count=len(frames),
        detected_frames=sum(1 for frame in frames if _frame_has_signal(frame)),
        gaze_off_ratio=_gaze_off_ratio(frames),
        head_movement=_head_movement(frames),
        expression_dist=_expression_dist(frames),
        event_counts=dict(Counter(event.event for event in events)),
    )


def _frame_has_signal(frame: LandmarkFrameMessage) -> bool:
    """프레임에 실제 얼굴 신호가 하나라도 있는지(전 필드 None = 미검출 = False).

    프론트가 얼굴 미검출 시에도 방어적으로 빈 프레임을 보낼 수 있으므로(계약상 전 필드
    nullable), 백엔드에서도 '데이터 있음' 판정을 신호 유무로 재확인한다(방어선).
    """
    return any(
        value is not None
        for value in (
            frame.gaze_x,
            frame.gaze_y,
            frame.head_yaw,
            frame.head_pitch,
            frame.head_roll,
            frame.expression,
        )
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


# ── 결과 표정 모달 환산 (계약 ④ feedback.expression) ────────────────


def to_modal_feedback(metrics: NonverbalMetrics) -> ModalFeedback | None:
    """집계 지표를 결과 페이지의 표정 ModalFeedback 으로 환산한다(데이터 없으면 None).

    점수화는 객관적 물리지표(시선 이탈률·고개 흔들림·주의 이벤트)만 한다 — 표정 라벨의
    의미(긍정/부정)는 클라이언트 MediaPipe 정의에 달려 있어 임의 점수화하지 않고, 분포는
    summary 문장(describe)에만 녹인다(가짜 점수를 만들지 않는다). 데이터가 없으면 None 을
    돌려 호출부(result_builder)가 빈 모달로 정직하게 비우게 한다.
    """
    if not metrics.has_data:
        return None
    gaze_score = _ratio_to_score(metrics.gaze_off_ratio)
    posture_score = _ratio_to_score(min(metrics.head_movement, 1.0))
    feedback_metrics = [
        FeedbackMetric(
            label='시선 처리',
            score=gaze_score,
            value=f'이탈 {metrics.gaze_off_ratio:.0%}',
            comment=_score_comment(gaze_score, '시선'),
        ),
        FeedbackMetric(
            label='자세 안정성',
            score=posture_score,
            value=_posture_value(metrics.head_movement),
            comment=_score_comment(posture_score, '자세'),
        ),
    ]
    scores = [gaze_score, posture_score]
    event_total = sum(metrics.event_counts.values())
    if event_total:
        attention = max(0, 100 - event_total * _EVENT_ATTENTION_PENALTY)
        feedback_metrics.append(
            FeedbackMetric(
                label='주의 집중',
                score=attention,
                value=f'주의 이벤트 {event_total}회',
                comment='시선이탈·무표정 등 주의 이벤트가 감지됐습니다.',
            )
        )
        scores.append(attention)
    overall = round(sum(scores) / len(scores))
    return ModalFeedback(score=overall, summary=describe(metrics), metrics=feedback_metrics)


def _ratio_to_score(ratio: float) -> int:
    """이탈/흔들림 비율(0~1, 낮을수록 좋음)을 0~100 점수로 뒤집어 환산한다."""
    return max(0, min(round((1.0 - ratio) * 100), 100))


def _posture_value(head_movement: float) -> str:
    """고개 흔들림(0~1)을 사람이 읽는 안정도 라벨로."""
    if head_movement < 0.3:
        return '안정'
    if head_movement < 0.6:
        return '보통'
    return '흔들림'


def _score_comment(score: int, subject: str) -> str:
    """점수대별 한 줄 코멘트(시선·자세 공용)."""
    if score >= 80:
        return f'{subject}이(가) 안정적이었습니다.'
    if score >= 60:
        return f'{subject}이(가) 다소 흔들렸습니다. 조금 더 안정적으로.'
    return f'{subject} 안정성이 낮았습니다. 의식적으로 가다듬어 보세요.'
