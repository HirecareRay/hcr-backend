"""모의 면접 라우터 — 실시간 WebSocket 엔드포인트.

한 면접 세션 = WS 연결 1개. 업스트림(JSON control/landmark/event + binary audio)을
받아 다운스트림 이벤트(question/transcript_delta/eval_delta/summary)를 내려보낸다.
메시지 형식은 app/interview/schemas.py 의 계약을 그대로 사용한다.

Phase 2: binary audio_chunk 는 즉시 응답하지 않고 답변(answer_start~answer_end)
경계로 누적했다가, answer_end 에 한 번에 전사해 transcript_delta 로 내려보낸다.

레이어 원칙: 여기서는 WS I/O·버퍼 경계·메시지 분기만 하고, 전사·이벤트 생성은
service.py 가 한다.
"""

import logging
from dataclasses import dataclass, field, replace

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.interview import service
from app.interview.schemas import ControlAction, ControlMessage, UpstreamMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/interviews', tags=['interview'])

_upstream_adapter: TypeAdapter[UpstreamMessage] = TypeAdapter(UpstreamMessage)


@dataclass(frozen=True)
class _WsSession:
    """연결 스코프 세션 상태 — 한 WS 연결 = 한 코루틴이라 격리가 보장된다.

    audio_chunks 는 현재 답변의 누적 오디오(유일한 가변 버퍼). 상태 전이는
    replace() 로 새 객체를 만들어 불변 패턴을 따른다.
    """

    question_index: int = 0
    audio_chunks: list[bytes] = field(default_factory=list)


async def _send(websocket: WebSocket, event: BaseModel) -> None:
    """다운스트림 이벤트를 camelCase JSON 으로 송신한다."""
    await websocket.send_json(event.model_dump(by_alias=True))


@router.websocket('/ws/{session_id}')
async def interview_ws(websocket: WebSocket, session_id: str) -> None:
    """실시간 면접 WS.

    흐름: 접속 시 첫 질문 → binary 는 답변 오디오로 누적 → control answer_start 면
    버퍼 리셋, answer_end 면 누적분 전사 후 자막·평가 송신, next 면 다음 질문
    (없으면 종료 요약).
    """
    await websocket.accept()
    session = _WsSession()
    await _send(websocket, service.question_at(session.question_index))

    try:
        while True:
            raw = await websocket.receive()
            if raw['type'] == 'websocket.disconnect':
                break

            # 바이너리(audio_chunk)는 JSON 스키마가 없는 답변 오디오 — 누적만 한다
            audio = raw.get('bytes')
            if audio is not None:
                session.audio_chunks.append(audio)
                continue

            text = raw.get('text')
            if text is None:
                continue
            try:
                message = _upstream_adapter.validate_json(text)
            except ValidationError:
                logger.warning('알 수 없는 업스트림 메시지 무시: %s', text[:200])
                continue

            session = await _handle_message(websocket, message, session)
    except WebSocketDisconnect:
        logger.info('면접 WS 종료: session=%s', session_id)


async def _handle_message(
    websocket: WebSocket, message: UpstreamMessage, session: _WsSession
) -> _WsSession:
    """업스트림 메시지를 처리하고 갱신된 세션 상태를 반환한다."""
    # landmark_frame·event_snapshot 은 비언어 지표 — Phase 4 에서 평가에 반영
    if not isinstance(message, ControlMessage):
        return session

    if message.action is ControlAction.ANSWER_START:
        # 새 답변 시작 — 이전 답변의 누적 오디오를 비운다
        return replace(session, audio_chunks=[])

    if message.action is ControlAction.ANSWER_END:
        await _finish_answer(websocket, session)
        return replace(session, audio_chunks=[])

    if message.action is ControlAction.NEXT:
        next_index = session.question_index + 1
        if next_index < service.question_count():
            await _send(websocket, service.question_at(next_index))
            return replace(session, question_index=next_index)
        await _send(websocket, service.final_summary())

    return session


async def _finish_answer(websocket: WebSocket, session: _WsSession) -> None:
    """누적 오디오를 전사해 자막을 내려보내고 평가 스트림을 잇는다.

    빈 버퍼면 전사를 건너뛰고(불필요한 과금 방지), STT 장애 시 WS 를 끊지 않고
    로깅 후 평가만 진행한다.
    """
    audio = b''.join(session.audio_chunks)
    if audio:
        try:
            transcript = await service.transcribe_answer(audio)
            if transcript is not None:
                await _send(websocket, transcript)
        except RuntimeError as error:
            logger.error('답변 전사 실패: %s', error)

    for event in service.eval_feedback():
        await _send(websocket, event)
