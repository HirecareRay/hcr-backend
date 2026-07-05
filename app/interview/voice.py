"""음성 신호 집계 — voice_metric → 발화 안정도 지표 (계약 ② 음성 트랙).

클라이언트 Web Audio API 가 ~1s 주기로 보내는 물리지표(데시벨·피치·말속도·떨림)를
받아 평균·안정도로 집계하고, 결과 페이지의 음성 ModalFeedback(계약 ④ feedback.voice)
으로 환산한다. 서버 직접 추론(GPU 없는 t3a.medium → OOM)을 피하려고 추론은 브라우저가
하고, 서버는 집계만 한다(자원≈0, 본류 아키텍처와 정합).

정직한 포지셔닝(중요): 음성에서 "감정"을 단정하지 않는다. 측정 가능한 *물리 지표*를
**발화 안정도**로만 환산한다. 라벨·코멘트도 안정/속도 같은 객관 표현만 쓴다.

레이어 원칙: nonverbal.py 와 대칭인 부수효과 없는 순수함수 모듈. 라우터는 누적만,
서비스는 조립만, 집계 수식은 전부 여기에 격리한다. 데모 보호: 입력이 비거나 모든
필드가 None 이어도 예외 없이 안전한 기본값으로 우회한다.
"""

from dataclasses import dataclass
from statistics import mean, pstdev

from app.core.config import settings
from app.interview.result_schemas import FeedbackMetric, ModalFeedback
from app.interview.schemas import VoiceMetricMessage

# 물리지표 필드 — 하나라도 값이 있으면 '신호 있는 프레임'으로 센다(전부 None = 무음/미장착).
_SIGNAL_FIELDS = ('decibel', 'pitch', 'speech_rate', 'tremor')

# 면접 발화의 적정 말속도 구간(WPM). 이 안이면 만점, 벗어날수록 선형 감점.
SPEECH_RATE_MIN = 100.0
SPEECH_RATE_MAX = 150.0
# 적정 구간을 이만큼(WPM) 벗어나면 0 점(너무 느림/빠름).
SPEECH_RATE_TOLERANCE = 60.0
# 피치 표준편차(Hz)를 0~1 불안정도로 정규화하는 기준치.
PITCH_STD_SCALE = 60.0


def _min_voice_frames() -> int:
    """음성 모달을 낼 최소 신호 프레임 수(1 하한 — 0 프레임은 절대 데이터가 아니다).

    설정(interview_min_voice_frames)으로 튜닝한다. 마이크가 잠깐만 켜져 1~2 프레임만
    잡힌 경우를 '데이터 부족'으로 눌러, 표본이 빈약한데 점수가 나오지 않게 한다.
    """
    return max(1, settings.interview_min_voice_frames)


@dataclass(frozen=True)
class VoiceMetrics:
    """집계된 음성 지표(불변). 결측 지표는 None — 해당 metric 을 생략하는 신호."""

    frame_count: int = 0  # 수신한 voice_metric 수(전부 None 인 무음 프레임 포함)
    signal_frames: int = 0  # 그중 물리지표가 실제로 하나라도 있던 프레임 수
    avg_speech_rate: float | None = None
    avg_decibel: float | None = None
    avg_tremor: float | None = None
    pitch_instability: float | None = None  # 0~1, 낮을수록 안정(피치 std 정규화)

    @property
    def has_data(self) -> bool:
        """점수를 낼 만큼 실제 음성 신호가 충분히 쌓였는지.

        frame_count(수신 수)가 아니라 signal_frames(값이 있던 프레임)를 최소 표본
        기준(_min_voice_frames)과 비교한다 — 마이크가 꺼져 전 필드 None 인 프레임만
        오거나 1~2 프레임뿐이면 False. nonverbal 의 detected_frames 판정과 대칭이다.
        """
        return self.signal_frames >= _min_voice_frames()


def aggregate(frames: tuple[VoiceMetricMessage, ...]) -> VoiceMetrics:
    """누적된 voice_metric 프레임을 평균·안정도로 집계한다(결측·빈입력 안전)."""
    return VoiceMetrics(
        frame_count=len(frames),
        signal_frames=sum(1 for frame in frames if _frame_has_signal(frame)),
        avg_speech_rate=_avg(frames, 'speech_rate'),
        avg_decibel=_avg(frames, 'decibel'),
        avg_tremor=_avg(frames, 'tremor'),
        pitch_instability=_pitch_instability(frames),
    )


def _frame_has_signal(frame: VoiceMetricMessage) -> bool:
    """프레임에 물리지표가 하나라도 있는지(전 필드 None = 무음/미장착 = False)."""
    return any(getattr(frame, field) is not None for field in _SIGNAL_FIELDS)


def describe(metrics: VoiceMetrics) -> str:
    """집계 지표를 사람이 읽는 발화 안정도 문장으로 변환한다(감정 단정 금지)."""
    if not metrics.has_data:
        return '음성 데이터가 충분하지 않아 발화 분석을 생략했습니다.'
    parts: list[str] = []
    if metrics.avg_speech_rate is not None:
        parts.append(f'평균 말속도는 약 {metrics.avg_speech_rate:.0f} WPM 입니다')
    if metrics.pitch_instability is not None:
        level = '안정적입니다' if metrics.pitch_instability < 0.4 else '다소 흔들렸습니다'
        parts.append(f'목소리 높낮이는 {level}')
    if metrics.avg_tremor is not None and metrics.avg_tremor >= 0.4:
        parts.append('목소리 떨림이 다소 감지됐습니다')
    return ('. '.join(parts) + '.') if parts else '발화 안정도 지표가 양호합니다.'


def to_modal_feedback(metrics: VoiceMetrics) -> ModalFeedback | None:
    """집계 지표를 결과 페이지의 음성 ModalFeedback 으로 환산한다(데이터 없으면 None).

    측정된 물리지표만 metric 으로 만든다 — 클라이언트가 안 보낸 지표는 생략한다(가짜로
    채우지 않는다). 점수는 발화 안정도(속도 적정성·음정 안정·떨림 적음)로만 환산한다.
    """
    if not metrics.has_data:
        return None
    feedback_metrics: list[FeedbackMetric] = []
    if metrics.avg_speech_rate is not None:
        score = _speech_rate_score(metrics.avg_speech_rate)
        feedback_metrics.append(
            FeedbackMetric(
                label='말 속도',
                score=score,
                value=f'{metrics.avg_speech_rate:.0f} WPM',
                comment=_speech_rate_comment(metrics.avg_speech_rate),
            )
        )
    if metrics.pitch_instability is not None:
        score = _stability_score(metrics.pitch_instability)
        feedback_metrics.append(
            FeedbackMetric(
                label='음정 안정',
                score=score,
                value='안정' if score >= 70 else '흔들림',
                comment='목소리 높낮이가 일정할수록 안정적으로 들립니다.',
            )
        )
    if metrics.avg_tremor is not None:
        score = _stability_score(metrics.avg_tremor)
        feedback_metrics.append(
            FeedbackMetric(
                label='발화 안정(떨림)',
                score=score,
                value='안정' if score >= 70 else '떨림 감지',
                comment='떨림이 적을수록 차분하게 전달됩니다.',
            )
        )
    if not feedback_metrics:
        return None
    overall = round(mean(m.score for m in feedback_metrics))
    return ModalFeedback(score=overall, summary=describe(metrics), metrics=feedback_metrics)


def _avg(frames: tuple[VoiceMetricMessage, ...], field: str) -> float | None:
    """프레임에서 해당 필드의 비결측 값 평균(없으면 None)."""
    values = [
        value for value in (getattr(frame, field) for frame in frames) if value is not None
    ]
    return mean(values) if values else None


def _pitch_instability(frames: tuple[VoiceMetricMessage, ...]) -> float | None:
    """피치 표준편차를 0~1 불안정도로 정규화한다(표본<2면 None — 판단 불가)."""
    pitches = [frame.pitch for frame in frames if frame.pitch is not None]
    if len(pitches) < 2:
        return None
    return min(pstdev(pitches) / PITCH_STD_SCALE, 1.0)


def _speech_rate_score(rate: float) -> int:
    """말속도가 적정 구간이면 100, 벗어난 만큼 0 까지 선형 감점한다."""
    if SPEECH_RATE_MIN <= rate <= SPEECH_RATE_MAX:
        return 100
    gap = SPEECH_RATE_MIN - rate if rate < SPEECH_RATE_MIN else rate - SPEECH_RATE_MAX
    return max(0, round(100 - gap / SPEECH_RATE_TOLERANCE * 100))


def _speech_rate_comment(rate: float) -> str:
    """말속도 적정성 코멘트(객관 표현만)."""
    if rate < SPEECH_RATE_MIN:
        return '말속도가 다소 느립니다. 조금 더 또렷하고 경쾌하게.'
    if rate > SPEECH_RATE_MAX:
        return '말속도가 다소 빠릅니다. 핵심에서 천천히.'
    return '말속도가 적정 구간입니다.'


def _stability_score(instability: float) -> int:
    """불안정도(0~1, 낮을수록 좋음)를 0~100 안정 점수로 뒤집는다."""
    return max(0, min(round((1.0 - instability) * 100), 100))
