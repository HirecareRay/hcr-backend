"""모의 면접 라우터 — 실시간 WebSocket 엔드포인트.

한 면접 세션 = WS 연결 1개. 업스트림(JSON control/landmark/event + binary audio)을
받아 다운스트림 이벤트(question/transcript_delta/eval_delta/summary)를 내려보낸다.
메시지 형식은 app/interview/schemas.py 의 계약을 그대로 사용한다.

진행(B안 상태머신): 접속 시 컨텍스트 기반 메인 질문을 생성해 첫 질문을 보낸다.
control 메시지로 전이한다 —
  answer_start  : 답변 오디오 버퍼 리셋
  answer_end    : 누적 오디오 전사 → 자막 송신 → 평가 토큰 스트림 → 턴 기록
  next          : (메인 답변 직후) 꼬리질문 1개 → (꼬리 답변 직후) 다음 메인 질문
                  → 메인 소진 시 최종 요약

레이어 원칙: 여기서는 WS I/O·버퍼 경계·상태 전이만 하고, 전사·LLM·이벤트 생성은
service.py 가 한다.
"""

import logging
from dataclasses import dataclass, field, replace

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.config import settings
from app.interview import nonverbal, service
from app.interview.schemas import (
    ControlAction,
    ControlMessage,
    EventSnapshotMessage,
    LandmarkFrameMessage,
    UpstreamMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/interviews', tags=['interview'])

_upstream_adapter: TypeAdapter[UpstreamMessage] = TypeAdapter(UpstreamMessage)

# 비언어 누적 상한 — 집계는 통계라 최근 N개면 충분하다. 긴 세션에서 무한 누적·
# tuple 복사 비용(특히 event_snapshot 의 base64 image)을 막는 방어선이다.
_MAX_LANDMARKS = 1200  # ~20분 @ 1s
_MAX_EVENTS = 500


@dataclass(frozen=True)
class _WsSession:
    """연결 스코프 세션 상태 — 한 WS 연결 = 한 코루틴이라 격리가 보장된다.

    main_questions 는 접속 시 생성한 메인 질문 목록, main_index 는 현재 메인 위치,
    awaiting_followup 은 "다음 next 가 꼬리질문 차례인가". current_question 은 직전에
    보낸 질문(평가·꼬리질문의 기준), history 는 누적 턴(요약 입력). audio_chunks 는
    현재 답변의 누적 오디오(유일한 가변 버퍼). 상태 전이는 replace() 로 새 객체를
    만들어 불변 패턴을 따른다.
    """

    main_questions: tuple[str, ...] = ()
    main_index: int = 0
    awaiting_followup: bool = False
    current_question: str = ''
    audio_chunks: list[bytes] = field(default_factory=list)
    # 더미 자막 모드에서 현재 답변에 흘린 부분 자막(=오디오 청크) 개수.
    # answer_start·answer_end 마다 0 으로 리셋해 답변별로 토큰 순번을 새로 센다.
    transcript_sent: int = 0
    history: tuple[service.Turn, ...] = ()
    # 비언어 신호는 audio_chunks 와 달리 저빈도(landmark ~1s, event 발생 시)라
    # 가변 list 대신 불변 tuple + replace 로 누적해 불변 패턴을 지킨다.
    landmarks: tuple[LandmarkFrameMessage, ...] = ()
    events: tuple[EventSnapshotMessage, ...] = ()


async def _send(websocket: WebSocket, event: BaseModel) -> None:
    """다운스트림 이벤트를 camelCase JSON 으로 송신한다."""
    await websocket.send_json(event.model_dump(by_alias=True))


@router.websocket('/ws/{session_id}')
async def interview_ws(websocket: WebSocket, session_id: str) -> None:
    """실시간 면접 WS.

    접속 시 회사 컨텍스트로 메인 질문을 생성해 첫 질문을 보낸 뒤, 업스트림
    프레임을 분기 처리한다(binary=답변 오디오 누적, text=control/landmark/event).
    """
    await websocket.accept()
    questions = await service.build_main_questions(
        settings.interview_main_question_count
    )
    first = questions[0]
    session = _WsSession(main_questions=tuple(questions), current_question=first)
    await _send(websocket, service.question_event('m0', first))

    try:
        while True:
            raw = await websocket.receive()
            if raw['type'] == 'websocket.disconnect':
                break

            # 바이너리(audio_chunk)는 JSON 스키마가 없는 답변 오디오.
            #  - 더미 모드: 오디오는 쓰지 않으므로 버퍼링하지 않고, 청크마다 부분
            #    자막만 흘려 자막이 흐르게 한다(OpenAI 호출 0, 불필요한 메모리 적재 0).
            #  - 실 모드: answer_end 에 통전사하기 위해 누적만 한다.
            audio = raw.get('bytes')
            if audio is not None:
                if settings.interview_dummy_transcript:
                    await _send(
                        websocket,
                        service.dummy_transcript_partial(session.transcript_sent),
                    )
                    session = replace(
                        session, transcript_sent=session.transcript_sent + 1
                    )
                else:
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
    # 비언어 신호는 다운스트림 응답 없이 세션에 누적만 한다 — 요약 시 집계에 쓰인다.
    # 상한을 넘으면 가장 오래된 것부터 버려 메모리·복사 비용을 묶어둔다.
    if isinstance(message, LandmarkFrameMessage):
        landmarks = (session.landmarks + (message,))[-_MAX_LANDMARKS:]
        return replace(session, landmarks=landmarks)
    if isinstance(message, EventSnapshotMessage):
        events = (session.events + (message,))[-_MAX_EVENTS:]
        return replace(session, events=events)
    if not isinstance(message, ControlMessage):
        return session

    if message.action is ControlAction.ANSWER_START:
        # 새 답변 시작 — 이전 답변의 누적 오디오·자막 토큰 순번을 비운다
        return replace(session, audio_chunks=[], transcript_sent=0)

    if message.action is ControlAction.ANSWER_END:
        return await _finish_answer(websocket, session)

    if message.action is ControlAction.NEXT:
        return await _advance(websocket, session)

    return session


async def _finish_answer(websocket: WebSocket, session: _WsSession) -> _WsSession:
    """답변을 확정해 자막을 닫고 평가를 스트리밍한 뒤 턴을 기록한다.

    답변 텍스트는 모드에 따라 다르게 얻는다 — 더미 모드면 흘린 토큰을 합치고,
    실 모드면 누적 오디오를 전사한다. 빈 답변이면 평가를 건너뛰고(불필요한 과금
    방지), STT 장애 시 WS 를 끊지 않고 로깅 후 평가만 진행한다. 평가 토큰은 누적해
    현재 턴의 평가로 저장한다.
    """
    answer = await _resolve_answer(websocket, session)

    evaluation = ''
    async for event in service.stream_evaluation(session.current_question, answer):
        evaluation += event.delta
        await _send(websocket, event)

    turn = service.Turn(session.current_question, answer, evaluation)
    return replace(
        session,
        audio_chunks=[],
        transcript_sent=0,
        history=session.history + (turn,),
    )


async def _resolve_answer(websocket: WebSocket, session: _WsSession) -> str:
    """모드에 맞게 답변 텍스트를 얻으며 자막을 마무리한다(빈 답변은 '').

    더미 모드는 종료 마커(isFinal=True)를, 실 모드는 통전사 자막(isFinal=True)을 보낸다.
    """
    if settings.interview_dummy_transcript:
        return await _finalize_dummy(websocket, session)
    return await _transcribe(websocket, session)


async def _finalize_dummy(websocket: WebSocket, session: _WsSession) -> str:
    """흘린 더미 토큰을 답변으로 확정하고 종료 마커를 보낸다(청크 없으면 빈 답변).

    실 STT 와 동일 규칙 — 청크가 하나도 없으면 자막·평가를 모두 생략한다.
    """
    if not session.transcript_sent:
        return ''
    await _send(websocket, service.transcript_final())
    return service.dummy_answer_text(session.transcript_sent)


async def _transcribe(websocket: WebSocket, session: _WsSession) -> str:
    """누적 오디오를 전사해 자막을 송신하고 답변 텍스트를 반환한다(빈 답변은 '')."""
    audio = b''.join(session.audio_chunks)
    if not audio:
        return ''
    try:
        transcript = await service.transcribe_answer(audio)
    except RuntimeError as error:
        logger.error('답변 전사 실패: %s', error)
        return ''
    if transcript is None:
        return ''
    await _send(websocket, transcript)
    return transcript.delta


async def _advance(websocket: WebSocket, session: _WsSession) -> _WsSession:
    """다음 전이 — 메인 답변 직후엔 꼬리질문, 꼬리 답변 직후엔 다음 메인/요약."""
    if not session.awaiting_followup:
        followed = await _try_follow_up(websocket, session)
        if followed is not None:
            return followed
    return await _next_main_or_summary(websocket, session)


async def _try_follow_up(
    websocket: WebSocket, session: _WsSession
) -> _WsSession | None:
    """직전 답변 기반 꼬리질문을 보낸다(생성 실패·답변 없으면 None 으로 우회)."""
    if not session.history:
        return None
    last = session.history[-1]
    text = await service.generate_follow_up(last.question, last.answer)
    if not text:
        return None
    await _send(websocket, service.question_event(f'f{session.main_index}', text))
    return replace(session, current_question=text, awaiting_followup=True)


async def _next_main_or_summary(
    websocket: WebSocket, session: _WsSession
) -> _WsSession:
    """다음 메인 질문으로 진행하거나, 메인을 모두 소진했으면 요약을 보낸다."""
    next_index = session.main_index + 1
    if next_index < len(session.main_questions):
        text = session.main_questions[next_index]
        await _send(websocket, service.question_event(f'm{next_index}', text))
        return replace(
            session,
            main_index=next_index,
            awaiting_followup=False,
            current_question=text,
        )
    try:
        metrics = nonverbal.aggregate(session.landmarks, session.events)
    except Exception as error:  # noqa: BLE001 - 집계 실패가 요약을 막지 않게
        logger.error('비언어 집계 실패, 빈 지표로 요약 진행: %s', error)
        metrics = nonverbal.NonverbalMetrics()
    await _send(websocket, await service.build_summary(session.history, metrics))
    return session
