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
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, TypeAdapter, ValidationError
from pymongo.database import Database

from app.auth.security import decode_access_token
from app.core.config import settings
from app.db.mongo import get_mongo_db
from app.interview import result_service, service, ws_ticket
from app.interview.result_schemas import InterviewResult
from app.interview.schemas import (
    ControlAction,
    ControlMessage,
    EventSnapshotMessage,
    LandmarkFrameMessage,
    TextAnswerMessage,
    UpstreamMessage,
    VoiceMetricMessage,
    WsTicketOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/interviews', tags=['interview'])

_upstream_adapter: TypeAdapter[UpstreamMessage] = TypeAdapter(UpstreamMessage)

# Bearer 토큰을 직접 처리한다 — 헤더 자체가 없을 때도 401 로 통일하기 위해
# auto_error=False(없으면 403 대신 None 반환)로 받고 아래에서 명시적으로 막는다.
_bearer = HTTPBearer(auto_error=False)

_ticket_auth_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail='유효하지 않은 인증 정보입니다',
    headers={'WWW-Authenticate': 'Bearer'},
)

# 비언어 누적 상한 — 집계는 통계라 최근 N개면 충분하다. 긴 세션에서 무한 누적·
# tuple 복사 비용(특히 event_snapshot 의 base64 image)을 막는 방어선이다.
_MAX_LANDMARKS = 1200  # ~20분 @ 1s
_MAX_EVENTS = 500
_MAX_VOICE = 1200  # ~20분 @ 1s (landmark 와 동일 주기)
# 타이핑 답변(text_answer) 한 건의 최대 길이. 면접 답변은 길어야 수천 자라 넉넉하다.
# 답변은 평가 1회 + 요약(매 턴 누적)으로 LLM 에 들어가므로, 거대한 붙여넣기로
# 빌린 OpenAI 키 토큰을 증폭시키지 못하게 자른다(버리지 않고 앞부분만 — 면접 안 끊김).
_MAX_ANSWER_CHARS = 5000


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
    # 요약(면접 종료)이 끝났는지. 요약 후 들어오는 control(next·answer_end)은 무시해
    # 평가·요약·리포트(LLM)와 결과 저장이 재실행되지 못하게 막는다 — 한 티켓으로
    # 무한히 과금 경로를 재호출하는 비용 남용을 차단한다(빌린 OpenAI 키 보호).
    finished: bool = False
    # 결과 영속화(계약 ④)에 필요한 세션 메타 — 접속 시 1회 확정한다.
    # user_id 는 소비한 티켓에서, company_id·job_title 은 접속 쿼리에서 온다.
    # started_at 은 accept 시각(conducted_at·duration 기준), had_audio 는 mode 판별
    # (답변 중 오디오가 한 번이라도 오면 voice, 아니면 text).
    user_id: str = ''
    company_id: str | None = None
    job_title: str | None = None
    started_at: datetime | None = None
    had_audio: bool = False
    audio_chunks: list[bytes] = field(default_factory=list)
    # 더미 자막 모드에서 현재 답변에 흘린 부분 자막(=오디오 청크) 개수.
    # answer_start·answer_end 마다 0 으로 리셋해 답변별로 토큰 순번을 새로 센다.
    transcript_sent: int = 0
    # 텍스트 모드 답변(타이핑) — text_answer 로 받으면 채운다. answer_end 시 오디오
    # 전사 대신 이 텍스트를 답변으로 쓴다. answer_start/answer_end 마다 비운다.
    typed_answer: str | None = None
    # 부분 자막 모드에서 지금까지 흘려보낸 자막 누적 텍스트. 누적 버퍼 재전사 결과에서
    # 이미 보낸 부분을 빼고 새 꼬리만 보내기 위한 기준. answer_start/answer_end 마다 비운다.
    partial_text: str = ''
    history: tuple[service.Turn, ...] = ()
    # 비언어 신호는 audio_chunks 와 달리 저빈도(landmark ~1s, event 발생 시)라
    # 가변 list 대신 불변 tuple + replace 로 누적해 불변 패턴을 지킨다.
    landmarks: tuple[LandmarkFrameMessage, ...] = ()
    events: tuple[EventSnapshotMessage, ...] = ()
    # 음성 물리지표(voice_metric) — landmark 와 동일하게 저빈도 누적, 요약 시 집계.
    voice_metrics: tuple[VoiceMetricMessage, ...] = ()


async def _send(websocket: WebSocket, event: BaseModel) -> None:
    """다운스트림 이벤트를 camelCase JSON 으로 송신한다."""
    await websocket.send_json(event.model_dump(by_alias=True))


@router.post(
    '/ws-ticket',
    response_model=WsTicketOut,
    response_model_by_alias=True,
)
async def issue_ws_ticket(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> WsTicketOut:
    """면접 WS 입장용 단기 1회용 티켓을 발급한다(Bearer JWT 필요).

    브라우저 WS 는 헤더를 못 붙이고 JWT 는 httpOnly 쿠키라 JS 로 못 읽으므로,
    입장 직전 이 일반 HTTP 로 티켓을 받아 WS 쿼리(?ticket=...)로만 실어 보낸다.
    JWT 가 없거나 무효면 401(로그에 토큰 평문은 남기지 않는다).

    async 로 둔다 — 내부가 모두 동기·즉시 반환이라 이벤트 루프 단일 스레드에서
    발급·소비(WS 의 consume_ticket)가 직렬화돼 모듈 전역 _store 경합을 피한다.
    """
    if credentials is None:
        raise _ticket_auth_error
    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as error:
        logger.warning('WS 티켓 발급 거부 — JWT 검증 실패: %s', error)
        raise _ticket_auth_error from error
    ticket, expires_in = ws_ticket.issue_ticket(user_id)
    return WsTicketOut(ticket=ticket, expires_in=expires_in)


def _require_user(credentials: HTTPAuthorizationCredentials | None) -> str:
    """Bearer JWT 를 검증해 user_id 를 확정한다(없거나 무효면 401)."""
    if credentials is None:
        raise _ticket_auth_error
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError as error:
        logger.warning('결과 조회 거부 — JWT 검증 실패: %s', error)
        raise _ticket_auth_error from error


_result_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail='면접 결과를 찾을 수 없습니다',
)


# by-id 를 먼저 선언한다 — '/results/by-id/...' 가 '/results/{company_id}' 로
# 잘못 매칭되지 않도록(FastAPI 는 선언 순서대로 경로를 매칭한다).
@router.get(
    '/results/by-id/{result_id}',
    response_model=InterviewResult,
    response_model_by_alias=True,
)
async def get_result_by_id(
    result_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    mongo: Database = Depends(get_mongo_db),
) -> InterviewResult:
    """result_id(세션 식별자)로 결과를 조회한다(로그인·소유자 전용)."""
    user_id = _require_user(credentials)
    result = result_service.get_result_by_id(mongo, user_id, result_id)
    if result is None:
        raise _result_not_found
    return result


@router.get(
    '/results/{company_id}',
    response_model=InterviewResult,
    response_model_by_alias=True,
)
async def get_result_by_company(
    company_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    mongo: Database = Depends(get_mongo_db),
) -> InterviewResult:
    """그 유저의 해당 회사 최신 면접 결과를 조회한다(로그인 전용)."""
    user_id = _require_user(credentials)
    result = result_service.get_result_by_company(mongo, user_id, company_id)
    if result is None:
        raise _result_not_found
    return result


def _get_mongo_db(websocket: WebSocket):
    """lifespan 이 app.state 에 올려둔 MongoDB 핸들을 꺼낸다(없으면 None)."""
    client = getattr(websocket.app.state, 'mongo_client', None)
    return client[settings.mongodb_db_name] if client is not None else None


def _open_db_session(websocket: WebSocket):
    """app.state 세션 팩토리로 MariaDB 세션을 연다(팩토리 없으면 None)."""
    factory = getattr(websocket.app.state, 'session_factory', None)
    return factory() if factory is not None else None


def _read_context_params(websocket: WebSocket) -> tuple[str | None, str | None]:
    """접속 쿼리에서 companyId·jobTitle 을 읽는다(camel·snake 둘 다 허용)."""
    params = websocket.query_params
    company_id = params.get('companyId') or params.get('company_id')
    job_title = params.get('jobTitle') or params.get('job_title')
    return company_id, job_title


async def _build_questions(
    websocket: WebSocket, user_id: str, company_id: str | None, job_title: str | None
) -> list[str]:
    """user_id·접속 쿼리·DB 로 개인화 메인 질문을 생성한다.

    user_id 는 핸들러가 입장 티켓을 소비해 확정한 값이다. companyId·jobTitle 은 선택 —
    companyId 가 없으면 회사 컨텍스트 주입만, jobTitle 이 없으면 직무 주입만 생략한다.

    MariaDB 세션은 질문 생성 동안만 열고 곧장 닫는다 — 면접 루프는 DB 가 필요 없어
    긴 세션 내내 풀 커넥션을 점유하지 않게 한다. MongoDB 핸들은 앱 수명이 관리한다.
    """
    mongo = _get_mongo_db(websocket)
    db = _open_db_session(websocket)
    try:
        return await service.build_main_questions(
            settings.interview_main_question_count,
            company_id=company_id,
            user_id=user_id,
            job_title=job_title,
            db=db,
            mongo=mongo,
        )
    finally:
        if db is not None:
            db.close()


@router.websocket('/ws/{session_id}')
async def interview_ws(websocket: WebSocket, session_id: str) -> None:
    """실시간 면접 WS.

    접속 직후 입장 티켓(?ticket=...)을 1회 소비해 user_id 를 확정한다. 티켓이 없거나
    무효·만료·재사용이면 정책 위반(1008)으로 연결을 거절한다 — 면접은 로그인 사용자
    전용이고, 빌린 OpenAI 키 비용 남용을 막는 실질 경계는 프론트 가드가 아니라 여기다.
    인증을 통과하면 회사 분석·지원자 이력서 컨텍스트로 메인 질문을 생성해 첫 질문을
    보낸 뒤, 업스트림 프레임을 분기 처리한다(binary=답변 오디오 누적, text=control/
    landmark/event). 티켓은 POST /interviews/ws-ticket 으로 미리 발급받는다.
    """
    user_id = ws_ticket.consume_ticket(websocket.query_params.get('ticket'))
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    started_at = datetime.now(timezone.utc)
    company_id, job_title = _read_context_params(websocket)
    questions = await _build_questions(websocket, user_id, company_id, job_title)
    first = questions[0]
    session = _WsSession(
        main_questions=tuple(questions),
        current_question=first,
        user_id=user_id,
        company_id=company_id,
        job_title=job_title,
        started_at=started_at,
    )
    await _send(websocket, service.question_event('m0', first))

    try:
        while True:
            raw = await websocket.receive()
            if raw['type'] == 'websocket.disconnect':
                break

            # 바이너리(audio_chunk)는 JSON 스키마가 없는 답변 오디오.
            #  - 더미 모드: 오디오는 쓰지 않으므로 버퍼링하지 않고, 청크마다 부분
            #    자막만 흘려 자막이 흐르게 한다(OpenAI 호출 0, 불필요한 메모리 적재 0).
            #  - 실 모드: answer_end 에 통전사하기 위해 누적한다. 부분 자막 모드면
            #    누적 중 일정 간격으로 재전사해 부분 자막을 흘린다.
            audio = raw.get('bytes')
            if audio is not None:
                # 오디오가 한 번이라도 오면 음성 모드로 본다(결과 meta.mode 판별).
                if settings.interview_dummy_transcript:
                    await _send(
                        websocket,
                        service.dummy_transcript_partial(session.transcript_sent),
                    )
                    session = replace(
                        session,
                        transcript_sent=session.transcript_sent + 1,
                        had_audio=True,
                    )
                else:
                    session = await _accumulate_audio(websocket, session, audio)
                    session = replace(session, had_audio=True)
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


async def _accumulate_audio(
    websocket: WebSocket, session: _WsSession, audio: bytes
) -> _WsSession:
    """답변 오디오를 누적하고, 부분 자막 모드면 일정 간격으로 재전사해 자막을 흘린다.

    부분 자막이 꺼져 있으면 누적만 한다(기존 배치 동작). 켜져 있으면 청크가
    설정 간격(every)에 닿을 때마다 누적 버퍼를 재전사해 새 꼬리만 부분 자막으로
    보낸다 — 전사 실패·새 내용 없음이면 조용히 건너뛴다(답변 진행을 막지 않음).
    """
    session.audio_chunks.append(audio)
    if not settings.interview_partial_transcript:
        return session
    if len(session.audio_chunks) % settings.interview_partial_transcript_every != 0:
        return session
    buffer = b''.join(session.audio_chunks)
    event = await service.transcribe_partial(buffer, session.partial_text)
    if event is None:
        return session
    await _send(websocket, event)
    return replace(session, partial_text=session.partial_text + event.delta)


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
    # 음성 물리지표도 다운스트림 응답 없이 누적만 한다(요약 시 발화 안정도로 환산).
    if isinstance(message, VoiceMetricMessage):
        voice_metrics = (session.voice_metrics + (message,))[-_MAX_VOICE:]
        return replace(session, voice_metrics=voice_metrics)
    # 텍스트 모드 답변 — 다운스트림 응답 없이 세션에 저장만 한다(answer_end 에서 사용).
    # 토큰 비용 남용 방지로 상한까지만 저장한다(초과분은 잘라냄, 정상 답변은 안 걸림).
    if isinstance(message, TextAnswerMessage):
        return replace(session, typed_answer=message.text[:_MAX_ANSWER_CHARS])
    if not isinstance(message, ControlMessage):
        return session

    # 요약(종료) 후의 전이는 무시한다 — answer_end(평가 LLM)·next(요약·리포트 LLM +
    # 결과 저장)가 재실행되지 못하게 막아, 한 티켓으로 과금 경로를 무한 재호출하는
    # 비용 남용을 차단한다(데모·빌린 키 보호).
    if session.finished:
        return session

    if message.action is ControlAction.ANSWER_START:
        # 새 답변 시작 — 이전 답변의 누적 오디오·자막 순번·타이핑 답변·부분 자막을 비운다
        return replace(
            session,
            audio_chunks=[],
            transcript_sent=0,
            typed_answer=None,
            partial_text='',
        )

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
        typed_answer=None,
        partial_text='',
        history=session.history + (turn,),
    )


async def _resolve_answer(websocket: WebSocket, session: _WsSession) -> str:
    """모드에 맞게 답변 텍스트를 얻으며 자막을 마무리한다(빈 답변은 '').

    타이핑 답변(text_answer)이 있으면 전사 없이 그 텍스트를 답변으로 쓰고 자막(final)을
    보낸다. 없으면 더미 모드는 종료 마커를, 실 모드는 통전사 자막을 보낸다(둘 다 isFinal=True).
    """
    if session.typed_answer:
        await _send(websocket, service.text_answer_transcript(session.typed_answer))
        return session.typed_answer
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
    """누적 오디오를 전사해 자막을 송신하고 답변 텍스트를 반환한다(빈 답변은 '').

    부분 자막 모드면 최종 전사로 남은 꼬리만 final 자막을 보내고 전체를 답변으로
    쓴다. 아니면(배치) answer_end 에 통전사해 전체 자막(final)을 한 번에 보낸다.
    """
    audio = b''.join(session.audio_chunks)
    if not audio:
        return ''
    if settings.interview_partial_transcript:
        answer, event = await service.finalize_partial(audio, session.partial_text)
        await _send(websocket, event)
        return answer
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
    await _send(
        websocket,
        service.question_event(f'f{session.main_index}', text, kind='follow_up'),
    )
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
    # 집계·요약·영속화는 result_service 가 오케스트레이션한다(라우터는 송신만).
    summary = await result_service.summarize_and_persist(
        history=session.history,
        landmarks=session.landmarks,
        events=session.events,
        voice_frames=session.voice_metrics,
        user_id=session.user_id,
        company_id=session.company_id,
        job_title=session.job_title,
        mode='voice' if session.had_audio else 'text',
        started_at=session.started_at or datetime.now(timezone.utc),
        mongo=_get_mongo_db(websocket),
    )
    await _send(websocket, summary)
    # 종료 표시 — 이후 들어오는 control 은 _handle_message 에서 무시돼 LLM·저장이
    # 재실행되지 않는다(요약·평가·리포트 재호출로 인한 비용 남용 차단).
    return replace(session, finished=True)
