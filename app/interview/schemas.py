"""모의 면접 실시간 계약 스키마 (Phase 0).

세션 상태머신과 WebSocket/SSE 메시지의 wire 형식을 Pydantic 으로 고정한다.
프론트(features/interview/types/)가 이 스키마를 1:1 미러링하므로, 양쪽
CLAUDE.md "계약 ②" 표와 항상 일치시킨다(한쪽을 고치면 다른쪽도).

표기 규칙 (중요):
  - 업스트림(브라우저 → FastAPI): 브라우저가 보내는 raw snake_case 키를 그대로
    받는다. 따라서 평범한 BaseModel 을 상속한다 — CamelModel 을 쓰면 안 된다
    (alias 가 키를 camel 로 바꿔 파싱이 깨진다).
  - 다운스트림(FastAPI → 브라우저): CamelModel 을 상속해 페이로드 키를 camelCase
    로 직렬화한다. 단 판별 필드 ``type`` 의 "값"(예: "transcript_delta")은
    Literal 문자열이라 alias 영향을 받지 않고 snake 그대로 나간다.

discriminated union: 모든 메시지는 ``type`` 필드로 판별한다. 수신측은
``TypeAdapter(UpstreamMessage)`` / ``TypeAdapter(DownstreamEvent)`` 로 안전하게
모델을 복원한다.

⚠️ audio_chunk(binary, webm/opus)는 JSON 메시지가 아니라 WS binary 프레임이므로
여기 스키마에 없다. WS 핸들러(Phase 1)에서 텍스트/바이너리 프레임으로 분기한다.
"""

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.shared.schema import CamelModel

# ── 세션 상태머신 (FE·BE 공통) ─────────────────────────────────────


class SessionStatus(str, Enum):
    """면접 세션 상태.

    흐름: 대기 → 질문 → 답변중 → 평가 → (다음질문 | 종료) → 요약.
    """

    IDLE = 'idle'
    QUESTION = 'question'
    ANSWERING = 'answering'
    EVALUATING = 'evaluating'
    FINISHED = 'finished'
    SUMMARY = 'summary'


class ControlAction(str, Enum):
    """control 업스트림 메시지의 전이 액션."""

    ANSWER_START = 'answer_start'
    ANSWER_END = 'answer_end'
    NEXT = 'next'


# ── 업스트림 (브라우저 → FastAPI, raw snake_case / CamelModel 금지) ──


class ControlMessage(BaseModel):
    """세션 전이 신호 (답변 시작·종료·다음 질문)."""

    type: Literal['control'] = 'control'
    action: ControlAction


class LandmarkFrameMessage(BaseModel):
    """얼굴 랜드마크 기반 비언어 지표 (주기 ~1s).

    구체 지표는 Phase 4(MediaPipe)에서 확정한다 — 아래는 잠정 필드.
    discriminated union 골격은 안정적이므로 필드 추가는 비-breaking.
    """

    type: Literal['landmark_frame'] = 'landmark_frame'
    gaze_x: float | None = None
    gaze_y: float | None = None
    head_yaw: float | None = None
    head_pitch: float | None = None
    head_roll: float | None = None
    expression: str | None = None


class EventSnapshotMessage(BaseModel):
    """이벤트 발생 시 신호 (시선이탈·무표정 등) — 이벤트 종류·메타만 수신 (이미지 미수신).

    event 는 이벤트 종류, meta 는 이벤트별 부가 정보. 집계는 event 횟수만 사용하므로
    얼굴 스냅샷 이미지는 받지 않는다(대역폭·프라이버시).
    """

    type: Literal['event_snapshot'] = 'event_snapshot'
    event: str
    meta: dict[str, Any] = Field(default_factory=dict)


class TextAnswerMessage(BaseModel):
    """텍스트 모드 답변 (타이핑) — 음성 대신 직접 입력한 답변 본문.

    answer_end 시 이 텍스트를 답변으로 사용한다(오디오 전사 대체).
    """

    type: Literal['text_answer'] = 'text_answer'
    text: str


class VoiceMetricMessage(BaseModel):
    """클라이언트 Web Audio API 가 추출한 음성 물리지표 (주기 ~1s, 답변 중 연속).

    서버 직접 추론(Whisper·SER)은 운영 EC2(t3a.medium, GPU 없음)에서 OOM 위험이라,
    데시벨·피치·말속도·떨림 같은 *물리 지표*만 브라우저에서 뽑아 올려보낸다. "감정"을
    단정하지 않고 **발화 안정도 지표**로만 쓴다(가짜 감정 라벨을 만들지 않는다).

    모든 필드는 선택 — 클라이언트가 추출 가능한 지표만 채워 보낸다. 결측은 집계에서
    제외한다(landmark_frame 과 동일 철학). 단위: decibel(dB), pitch(Hz),
    speech_rate(WPM, 분당 단어), tremor(0~1 떨림 정도).
    """

    type: Literal['voice_metric'] = 'voice_metric'
    decibel: float | None = None
    pitch: float | None = None
    speech_rate: float | None = None
    tremor: float | None = None


UpstreamMessage = Annotated[
    Union[
        ControlMessage,
        LandmarkFrameMessage,
        EventSnapshotMessage,
        TextAnswerMessage,
        VoiceMetricMessage,
    ],
    Field(discriminator='type'),
]
"""업스트림 JSON 메시지 union. audio_chunk(binary)는 제외 — 파일 상단 주석 참고."""


# ── 다운스트림 (FastAPI → 브라우저, CamelModel: snake→camel) ────────


class QuestionEvent(CamelModel):
    """생성된 면접 질문 (+TTS용 텍스트)."""

    type: Literal['question'] = 'question'
    question_id: str
    text: str
    tts_text: str | None = None
    # 메인(기본) 질문인지 직전 답변 기반 꼬리질문인지 — 프론트 배지·흐름 표시용.
    kind: Literal['main', 'follow_up'] = 'main'


class TranscriptDeltaEvent(CamelModel):
    """실시간 자막 토큰 (STT 부분 결과)."""

    type: Literal['transcript_delta'] = 'transcript_delta'
    delta: str
    is_final: bool = False


class EvalDeltaEvent(CamelModel):
    """답변 평가 토큰 스트림 (LLM 생성 중간 토큰)."""

    type: Literal['eval_delta'] = 'eval_delta'
    delta: str


class SummaryEvent(CamelModel):
    """최종 통합 리포트 (언어 + 비언어).

    상세 필드는 Phase 5(통합 리포트)에서 확장 — 아래는 최소 합의셋.
    """

    type: Literal['summary'] = 'summary'
    overall_score: float
    language_feedback: str
    nonverbal_feedback: str
    improvements: list[str] = Field(default_factory=list)


DownstreamEvent = Annotated[
    Union[QuestionEvent, TranscriptDeltaEvent, EvalDeltaEvent, SummaryEvent],
    Field(discriminator='type'),
]
"""다운스트림 이벤트 union. CamelModel 직렬화(by_alias=True)로 프론트에 내려간다."""


# ── WS 입장 티켓 (HTTP 응답, CamelModel: snake→camel) ───────────────


class WsTicketOut(CamelModel):
    """POST /interviews/ws-ticket 응답 — 면접 WS 입장용 단기 1회용 티켓."""

    ticket: str
    expires_in: int  # → JSON: "expiresIn" (초)
