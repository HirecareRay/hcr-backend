"""음성 집계 순수함수 단위 테스트 (계약 ② 음성 트랙 / 계약 ④ feedback.voice).

클라이언트 Web Audio API 가 보내는 voice_metric(데시벨·피치·말속도·떨림)을 받아
평균·안정도로 집계하고 발화 안정도 ModalFeedback 으로 환산하는 경로를 검증한다.
순수함수라 mock 없이 입력→출력만 본다. 데이터 없음·결측은 안전 기본값으로 우회한다.

정직성: 음성에서 "감정"을 단정하지 않고 물리지표 기반 안정도만 만든다 —
측정 안 된 지표는 metric 에서 생략한다(가짜로 채우지 않는다).
"""

from app.interview import voice
from app.interview.schemas import VoiceMetricMessage


def _vm(**kwargs) -> VoiceMetricMessage:
    return VoiceMetricMessage(**kwargs)


def test_aggregate_empty_has_no_data():
    metrics = voice.aggregate(())
    assert metrics.has_data is False
    assert metrics.avg_speech_rate is None


def test_aggregate_averages_only_non_none():
    frames = (_vm(speech_rate=100.0), _vm(speech_rate=140.0), _vm(decibel=60.0))
    metrics = voice.aggregate(frames)
    assert metrics.frame_count == 3
    assert metrics.avg_speech_rate == 120.0  # None 인 프레임은 제외
    assert metrics.avg_decibel == 60.0


def test_modal_none_when_no_data():
    assert voice.to_modal_feedback(voice.aggregate(())) is None


def test_speech_rate_in_range_full_score():
    frames = tuple(_vm(speech_rate=120.0) for _ in range(3))  # 적정 구간
    modal = voice.to_modal_feedback(voice.aggregate(frames))
    rate = next(m for m in modal.metrics if m.label == '말 속도')
    assert rate.score == 100
    assert 'WPM' in rate.value


def test_speech_rate_too_fast_lowers_score():
    frames = tuple(_vm(speech_rate=200.0) for _ in range(3))  # 너무 빠름
    modal = voice.to_modal_feedback(voice.aggregate(frames))
    rate = next(m for m in modal.metrics if m.label == '말 속도')
    assert rate.score < 100


def test_modal_skips_missing_metrics():
    """말속도만 보내면 말속도 metric 만 만든다(없는 지표는 가짜로 채우지 않음)."""
    frames = tuple(_vm(speech_rate=120.0) for _ in range(3))
    modal = voice.to_modal_feedback(voice.aggregate(frames))
    labels = {m.label for m in modal.metrics}
    assert labels == {'말 속도'}  # 음정·떨림은 데이터 없어 생략


def test_pitch_instability_needs_two_samples():
    assert voice.aggregate((_vm(pitch=200.0),)).pitch_instability is None
    metrics = voice.aggregate((_vm(pitch=200.0), _vm(pitch=205.0)))
    assert metrics.pitch_instability is not None
    assert 0.0 <= metrics.pitch_instability <= 1.0


def test_stable_pitch_high_score():
    frames = tuple(_vm(pitch=200.0) for _ in range(5))  # 변동 없음 → 안정
    modal = voice.to_modal_feedback(voice.aggregate(frames))
    pitch = next(m for m in modal.metrics if m.label == '음정 안정')
    assert pitch.score == 100


def test_describe_has_no_emotion_claim():
    """발화 안정도 문장은 객관 표현만 — '감정' 같은 단정 라벨을 쓰지 않는다."""
    frames = tuple(_vm(speech_rate=120.0, pitch=200.0, tremor=0.1) for _ in range(3))
    text = voice.describe(voice.aggregate(frames))
    assert '감정' not in text
    assert 'WPM' in text
