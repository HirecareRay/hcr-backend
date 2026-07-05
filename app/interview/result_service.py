"""면접 결과 오케스트레이션 — 요약 시점 영속화 + REST 조회 + 직전 세션 비교.

요약 시점에 LLM 종합 1회로 결과를 조립해 MongoDB 에 저장한다(완성품 저장 — 계약 ④).
REST 는 저장된 완성품을 그대로 조회한다(LLM 재호출 0).

WS summary 이벤트(계약 ② 라이브 채점)도 이 결과에서 파생한다 — 리포트가 유일한 채점
소스라, 끝나자마자 보는 라이브 종합점수와 나중에 조회하는 결과 페이지 종합점수가 항상
일치한다(service.summary_from_result). 저장 실패가 면접 종료·라이브 summary 송신을
막지 않도록 모든 예외를 흡수한다(데모 보호).

레이어 원칙: LLM=llm.py, 변환=result_builder.py, DB=result_repository.py. 여기서는
그 경계들을 조립한다.
"""

import logging
import uuid
from datetime import datetime, timezone

from pymongo.database import Database

from app.interview import (
    llm,
    nonverbal,
    result_builder,
    result_repository,
    service,
    voice,
)
from app.interview.nonverbal import NonverbalMetrics
from app.interview.result_schemas import (
    DeltaDirection,
    InterviewComparison,
    InterviewHistoryItem,
    InterviewHistoryList,
    InterviewMode,
    InterviewResult,
    MetricDelta,
    ResultMeta,
)
from app.interview.schemas import (
    EventSnapshotMessage,
    LandmarkFrameMessage,
    SummaryEvent,
    VoiceMetricMessage,
)
from app.interview.voice import VoiceMetrics

logger = logging.getLogger(__name__)


async def summarize_and_persist(
    *,
    history: tuple[service.Turn, ...],
    landmarks: tuple[LandmarkFrameMessage, ...],
    events: tuple[EventSnapshotMessage, ...],
    voice_frames: tuple[VoiceMetricMessage, ...],
    user_id: str,
    company_id: str | None,
    job_title: str | None,
    mode: InterviewMode,
    started_at: datetime,
    mongo: Database | None,
) -> SummaryEvent:
    """요약 시점 마무리 — 결과 리포트(계약 ④)를 만들어 저장하고, 그걸로 라이브 summary(계약 ②)를 파생한다.

    리포트가 유일한 채점 소스다 — 끝나자마자 보는 라이브 summary 의 종합점수와 나중에
    조회하는 결과 페이지의 overall.score 가 항상 일치하도록, LLM 종합 1회로 두 산출물을
    같은 결과에서 낸다(예전엔 요약·리포트를 각각 LLM 으로 뽑아 두 종합점수가 어긋났다).
    표정·음성은 결과 페이지에서 별도 모달로 점수화하므로 라이브 종합점수엔 섞지 않는다.

    라우터는 WS I/O 만 하도록, 집계·리포트·영속화 오케스트레이션을 여기로 모은다(레이어
    원칙). 집계·저장 실패는 안전 기본값으로 우회한다(면접 종료를 막지 않음 — 데모 보호).
    반환한 SummaryEvent 는 라우터가 그대로 송신한다.
    """
    try:
        metrics = nonverbal.aggregate(landmarks, events)
    except Exception as error:  # noqa: BLE001 - 집계 실패가 요약을 막지 않게
        logger.error('비언어 집계 실패, 빈 지표로 요약 진행: %s', error)
        metrics = NonverbalMetrics()
    voice_metrics = voice.aggregate(voice_frames)
    try:
        result = await _build_result(
            history=history,
            metrics=metrics,
            voice_metrics=voice_metrics,
            user_id=user_id,
            company_id=company_id,
            job_title=job_title,
            mode=mode,
            started_at=started_at,
            mongo=mongo,
        )
    except Exception as error:  # noqa: BLE001 - 결과 조립 실패가 면접 종료를 막지 않게
        logger.error('결과 조립 실패, 기본 요약으로 우회: %s', error)
        return service.fallback_summary(metrics)
    # 저장 가능하면 저장한다(계약 ④). 라이브 summary 는 저장 여부와 무관하게 이 결과로 파생한다.
    _persist(mongo, user_id, company_id, result)
    return service.summary_from_result(result, metrics)


async def _build_result(
    *,
    history: tuple[service.Turn, ...],
    metrics: NonverbalMetrics,
    voice_metrics: VoiceMetrics | None,
    user_id: str,
    company_id: str | None,
    job_title: str | None,
    mode: InterviewMode,
    started_at: datetime,
    mongo: Database | None,
) -> InterviewResult:
    """LLM 종합 1회로 결과(계약 ④)를 조립한다(저장은 하지 않음 — 라이브 summary 도 이 결과로 파생).

    라이브 summary 와 결과 페이지가 같은 결과를 공유하도록 저장 가능 여부와 무관하게
    만든다. 전부 무응답이면 _generate_report 가 LLM 없이 빈 dict 를 줘 builder 가 정직한
    0점으로 우회한다(없는 강점·점수를 지어내지 않음). LLM 장애도 빈 dict 로 흡수돼 결과가
    끊기지 않는다. 표정 모달은 비언어 집계(metrics)에서, 음성 모달은 음성 집계에서 환산한다.
    """
    report = await _generate_report(history, job_title)
    meta = _build_meta(history, user_id, company_id, job_title, mode, started_at, mongo)
    # 표정·음성 모달은 각 집계에서 환산한다(데이터 없으면 None → builder 가 빈 모달).
    expression_modal = nonverbal.to_modal_feedback(metrics)
    voice_modal = voice.to_modal_feedback(voice_metrics) if voice_metrics else None
    return result_builder.build_result(
        meta=meta,
        history=history,
        report=report,
        expression=expression_modal,
        voice=voice_modal,
    )


def _persist(
    mongo: Database | None,
    user_id: str,
    company_id: str | None,
    result: InterviewResult,
) -> None:
    """결과를 MongoDB 에 저장한다(저장할 곳이 없거나 실패해도 면접 종료를 막지 않는다).

    직전 세션과 비교해 comparison 을 붙여 저장한다. 저장은 결과 페이지(계약 ④) 전용이며,
    실패해도 라이브 summary 송신·면접 종료를 막지 않도록 모든 예외를 흡수한다(데모 보호).
    """
    if mongo is None:
        return
    try:
        _persist_with_comparison(mongo, user_id, company_id, result.meta, result)
    except Exception as error:  # noqa: BLE001 - 영속화 실패가 면접 종료를 막지 않게
        logger.error('면접 결과 영속화 실패(결과 페이지에서 누락될 수 있음): %s', error)


async def _generate_report(
    history: tuple[service.Turn, ...], job_title: str | None
) -> dict:
    """LLM 종합 리포트 dict 를 생성한다(빈 기록·전부 무응답·실패면 빈 dict — 안전 기본값 우회).

    실질 답변이 하나도 없으면(전부 스킵) LLM 을 호출하지 않는다 — 없는 강점·점수를
    지어내지 않고(빈 dict → builder 가 빈 강점·0점으로 정직하게 우회), 빌린 OpenAI
    키도 낭비하지 않는다.
    """
    if not history or not service.has_any_answer(history):
        return {}
    transcript = service.format_history(history)
    try:
        return await llm.generate_report(transcript, (job_title or '').strip())
    except RuntimeError as error:
        logger.error('리포트 생성 실패, 기본값으로 우회: %s', error)
        return {}


def _build_meta(
    history: tuple[service.Turn, ...],
    user_id: str,
    company_id: str | None,
    job_title: str | None,
    mode: InterviewMode,
    started_at: datetime,
    mongo: Database | None,
) -> ResultMeta:
    """결과 메타를 만든다(result_id 신규 발급, duration·회사명 확정).

    mode 는 호출부(라우터)가 이미 'voice'/'text' 로 좁혀 넘기므로 여기서 재정규화하지 않는다.
    """
    now = datetime.now(timezone.utc)
    duration = max(0, int((now - started_at).total_seconds()))
    return ResultMeta(
        result_id=str(uuid.uuid4()),
        company_id=company_id or '',
        company_name=_lookup_company_name(mongo, company_id),
        job_title=(job_title or '').strip(),
        conducted_at=started_at.isoformat(),
        duration_sec=duration,
        mode=mode,
        question_count=len(history),
    )


def _lookup_company_name(mongo: Database | None, company_id: str | None) -> str:
    """회사명을 best-effort 로 조회한다(없거나 실패하면 빈 문자열 — 면접을 막지 않음)."""
    if mongo is None or not company_id:
        return ''
    try:
        from app.company import repository as company_repository

        company = company_repository.find_company(mongo, company_id)
        return str((company or {}).get('company_name') or '').strip()
    except Exception as error:  # noqa: BLE001 - 회사명 조회 실패가 결과를 막지 않게
        logger.error('회사명 조회 실패, 빈 값으로 우회: %s', error)
        return ''


def _persist_with_comparison(
    mongo: Database,
    user_id: str,
    company_id: str | None,
    meta: ResultMeta,
    result: InterviewResult,
) -> None:
    """직전 세션과 비교해 comparison 을 붙이고 저장한다(호출부가 예외를 흡수한다).

    저장 전에 직전 세션(현재 미저장)을 조회해 비교한다 — find_latest_by_user 는 아직
    이번 세션을 포함하지 않으므로 '직전'이 된다. attempt_count 는 이번을 포함한 누적 수.
    """
    previous = result_repository.find_latest_by_user(mongo, user_id)
    attempt_count = result_repository.count_by_user(mongo, user_id) + 1
    comparison = _build_comparison(previous, result, attempt_count)
    result = result.model_copy(update={'comparison': comparison})
    result_repository.save_session_result(
        mongo,
        {
            'result_id': meta.result_id,
            'user_id': user_id,
            'company_id': company_id or '',
            'conducted_at': meta.conducted_at,
            'result': result.model_dump(by_alias=False),
        },
    )


def _build_comparison(
    previous: dict | None, current: InterviewResult, attempt_count: int
) -> InterviewComparison | None:
    """직전 세션 결과와 현재 결과의 점수를 비교한다(직전 없으면 None — 첫 면접)."""
    if not previous or not isinstance(previous.get('result'), dict):
        return None
    prev = previous['result']
    # 종합·답변은 항상 비교한다(언어 평가는 늘 산출된다). 표정·음성은 카메라·마이크를
    # 안 켜면 '데이터 없음(빈 모달, score=0)'이라, 0 을 실제 0점으로 오해해 "점수가
    # 떨어졌다"고 왜곡하지 않도록 양쪽 다 데이터가 있을 때만 비교에 넣는다.
    deltas = [_delta('종합', _score(prev, 'overall'), current.overall.score)]
    if current.feedback.expression.metrics and _prev_modal_has_data(prev, 'expression'):
        deltas.append(
            _delta('표정', _feedback_score(prev, 'expression'), current.feedback.expression.score)
        )
    if current.feedback.voice.metrics and _prev_modal_has_data(prev, 'voice'):
        deltas.append(
            _delta('음성', _feedback_score(prev, 'voice'), current.feedback.voice.score)
        )
    deltas.append(
        _delta('답변', _feedback_score(prev, 'answer'), current.feedback.answer.score)
    )
    return InterviewComparison(
        previous_result_id=str(prev.get('meta', {}).get('result_id') or ''),
        previous_date=str(prev.get('meta', {}).get('conducted_at') or ''),
        attempt_count=attempt_count,
        deltas=deltas,
        summary=_comparison_summary(deltas),
    )


def _delta(label: str, previous: int, current: int) -> MetricDelta:
    """이전·현재 점수로 변화 지표를 만든다(direction 은 부호와 일치)."""
    diff = current - previous
    direction: DeltaDirection = 'up' if diff > 0 else 'down' if diff < 0 else 'same'
    return MetricDelta(
        label=label, previous=previous, current=current, delta=diff, direction=direction
    )


def _comparison_summary(deltas: list[MetricDelta]) -> str:
    """변화 지표를 규칙 기반 한 줄 총평으로(LLM 없이 정직하게)."""
    ups = [d.label for d in deltas if d.direction == 'up']
    downs = [d.label for d in deltas if d.direction == 'down']
    parts: list[str] = []
    if ups:
        parts.append(f'{"·".join(ups)} 점수가 올랐습니다')
    if downs:
        parts.append(f'{"·".join(downs)} 점수는 떨어졌습니다')
    if not parts:
        return '직전 연습과 점수 변화가 크지 않습니다.'
    return ', '.join(parts) + '.'


def _score(result: dict, key: str) -> int:
    """저장된 result dict 에서 overall 등 점수 1개를 안전하게 읽는다."""
    section = result.get(key)
    if isinstance(section, dict):
        try:
            return max(0, min(int(section.get('score', 0)), 100))
        except (TypeError, ValueError):
            return 0
    return 0


def _feedback_score(result: dict, modal: str) -> int:
    """저장된 result dict 의 feedback.<modal>.score 를 안전하게 읽는다."""
    feedback = result.get('feedback')
    if isinstance(feedback, dict):
        return _score(feedback, modal)
    return 0


def _prev_modal_has_data(result: dict, modal: str) -> bool:
    """저장된 직전 결과의 feedback.<modal> 에 실제 지표(metrics)가 있었는지.

    빈 모달(데이터 미수신)은 metrics 가 비어 score=0 이다 — 이 0 을 실제 0점으로
    오해해 비교에 넣지 않도록, 지표가 있는 모달만 비교 대상으로 본다.
    """
    feedback = result.get('feedback')
    if isinstance(feedback, dict):
        section = feedback.get(modal)
        if isinstance(section, dict):
            return bool(section.get('metrics'))
    return False


def get_result_by_company(
    mongo: Database, user_id: str, company_id: str
) -> InterviewResult | None:
    """그 유저의 해당 회사 최신 결과를 복원한다(없으면 None)."""
    document = result_repository.find_latest_by_company(mongo, user_id, company_id)
    return _restore(document)


def get_result_by_id(
    mongo: Database, user_id: str, result_id: str
) -> InterviewResult | None:
    """result_id 로 결과를 복원한다(없거나 소유자가 아니면 None — 남의 결과 차단)."""
    document = result_repository.find_by_id(mongo, result_id)
    if document is None or document.get('user_id') != user_id:
        return None
    return _restore(document)


_GENERAL_COMPANY_ID = 'general'
_GENERAL_COMPANY_NAME = '일반 면접'


def list_session_history(mongo: Database, user_id: str) -> InterviewHistoryList:
    """그 유저의 모든 세션을 최신순 카드 목록으로 요약한다(마이페이지 면접 기록).

    저장된 완성품(result.meta+overall)에서 카드에 필요한 필드만 뽑는다 — 새 계산·LLM
    재호출 0(계약 ④). 손상돼 요약할 수 없는 문서는 건너뛴다(하나가 목록 전체를 깨지
    않게). 기록이 없으면 items=[]·total=0 (빈 목록은 정상 — 404 아님).
    """
    documents = result_repository.find_all_by_user(mongo, user_id)
    items = [
        item for item in (_to_history_item(document) for document in documents)
        if item is not None
    ]
    return InterviewHistoryList(items=items, total=len(items))


def _to_history_item(document: dict) -> InterviewHistoryItem | None:
    """저장 문서 1건을 카드 요약으로 변환한다(형식 오류면 None — 목록에서 제외).

    회사 미지정(company_id 빈값) 세션은 'general'·'일반 면접'으로 라벨링해 프론트
    카드가 회사 없는 일반 면접도 표시하게 한다.
    """
    result = document.get('result')
    if not isinstance(result, dict):
        return None
    meta = result.get('meta')
    overall = result.get('overall')
    if not isinstance(meta, dict) or not isinstance(overall, dict):
        return None
    try:
        # 일반 면접(회사 미지정)의 판정 기준은 company_id 부재다 — id 가 비면 회사명이
        # 남아 있어도 함께 'general'·'일반 면접'으로 라벨링해 카드 표기를 일관화한다.
        company_id = str(meta.get('company_id') or '').strip()
        company_name = str(meta.get('company_name') or '').strip()
        is_general = not company_id
        return InterviewHistoryItem(
            result_id=str(meta.get('result_id') or ''),
            company_id=company_id or _GENERAL_COMPANY_ID,
            company_name=_GENERAL_COMPANY_NAME if is_general else company_name,
            job_title=str(meta.get('job_title') or ''),
            conducted_at=str(meta.get('conducted_at') or ''),
            mode=meta.get('mode', 'text'),
            score=int(overall.get('score', 0)),
            grade=str(overall.get('grade') or ''),
            headline=str(overall.get('headline') or ''),
            question_count=int(meta.get('question_count', 0)),
        )
    except Exception as error:  # noqa: BLE001 - 손상된 문서가 목록을 깨지 않게
        logger.error('세션 기록 요약 실패, 항목 제외: %s', error)
        return None


def _restore(document: dict | None) -> InterviewResult | None:
    """저장 문서의 result 필드를 InterviewResult 로 복원한다(형식 오류면 None)."""
    if not document or not isinstance(document.get('result'), dict):
        return None
    try:
        return InterviewResult(**document['result'])
    except Exception as error:  # noqa: BLE001 - 손상된 문서가 조회를 깨지 않게
        logger.error('저장된 결과 복원 실패: %s', error)
        return None
