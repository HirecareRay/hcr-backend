"""모의 면접 라우터 — 실시간 WebSocket 엔드포인트 (Phase 1 walking skeleton).

한 면접 세션 = WS 연결 1개. 업스트림(JSON control/landmark/event + binary audio)을
받아 다운스트림 이벤트(question/transcript_delta/eval_delta/summary)를 더미로 왕복한다.
메시지 형식은 app/interview/schemas.py 의 계약을 그대로 사용한다.

레이어 원칙: 여기서는 WS I/O·메시지 분기만 하고, 더미 이벤트 생성은 service.py 가 한다.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.interview import service
from app.interview.schemas import ControlAction, ControlMessage, UpstreamMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interviews", tags=["interview"])

_upstream_adapter: TypeAdapter[UpstreamMessage] = TypeAdapter(UpstreamMessage)


async def _send(websocket: WebSocket, event: BaseModel) -> None:
    """다운스트림 이벤트를 camelCase JSON 으로 송신한다."""
    await websocket.send_json(event.model_dump(by_alias=True))


@router.websocket("/ws/{session_id}")
async def interview_ws(websocket: WebSocket, session_id: str) -> None:
    """실시간 면접 WS — Phase 1 더미 왕복.

    흐름(더미): 접속 시 첫 질문 → control answer_end 면 전사·평가 스트림 →
    control next 면 다음 질문(없으면 종료 요약) → binary 면 수신 확인 자막.
    """
    await websocket.accept()
    question_index = 0
    await _send(websocket, service.question_at(question_index))

    try:
        while True:
            raw = await websocket.receive()
            if raw["type"] == "websocket.disconnect":
                break

            # 바이너리(audio_chunk)는 JSON 스키마가 없으므로 별도 경로로 처리
            audio = raw.get("bytes")
            if audio is not None:
                await _send(websocket, service.audio_ack(len(audio)))
                continue

            text = raw.get("text")
            if text is None:
                continue
            try:
                message = _upstream_adapter.validate_json(text)
            except ValidationError:
                logger.warning("알 수 없는 업스트림 메시지 무시: %s", text[:200])
                continue

            question_index = await _handle_message(websocket, message, question_index)
    except WebSocketDisconnect:
        logger.info("면접 WS 종료: session=%s", session_id)


async def _handle_message(
    websocket: WebSocket, message: UpstreamMessage, question_index: int
) -> int:
    """업스트림 메시지를 처리하고 갱신된 질문 인덱스를 반환한다."""
    # landmark_frame·event_snapshot 은 비언어 지표 — Phase 4 에서 평가에 반영
    if not isinstance(message, ControlMessage):
        return question_index

    if message.action is ControlAction.ANSWER_END:
        for event in service.answer_feedback():
            await _send(websocket, event)
    elif message.action is ControlAction.NEXT:
        next_index = question_index + 1
        if next_index < service.question_count():
            await _send(websocket, service.question_at(next_index))
            return next_index
        await _send(websocket, service.final_summary())
    # answer_start 는 상태 전이 신호일 뿐 — 더미 단계에선 응답 없음
    return question_index
