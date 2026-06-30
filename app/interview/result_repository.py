"""면접 결과 영속화 — MongoDB `interview_sessions` 컬렉션 접근(쿼리만).

결과는 회사가 아니라 "세션" 단위다 — result_id(uuid)가 1급 식별자. WS 세션은 연결별
인메모리라 끊기면 사라지므로, 요약 시점에 완성된 InterviewResult 를 통째로 저장하고
(계약 ④ — 완성품 저장) REST 가 조회한다. 재조회 시 LLM 재호출이 없도록 산출물을
그대로 보관한다.

레이어 원칙: 여기서는 DB 접근만 한다(조립은 result_builder, 오케스트레이션·비교는
result_service). documents/repository.py 의 컬렉션·인덱스 보장 패턴을 따른다.

저장 문서 구조(내부 snake_case):
    result_id, user_id, company_id, conducted_at, created_at,  ← 조회/정렬 키
    result: <InterviewResult dump (by_alias=False)>            ← 완성 산출물
"""

import logging
from datetime import datetime, timezone

from pymongo import DESCENDING
from pymongo.database import Database

logger = logging.getLogger(__name__)

COLLECTION_NAME = 'interview_sessions'


def _get_collection(db: Database):
    """컬렉션을 보장하고 핸들을 반환한다(없으면 생성 + 인덱스 설정).

    result_id 는 1급 식별자라 unique, (user_id, created_at) 는 최신 세션·직전 세션
    조회를 빠르게 하려는 복합 인덱스다. create_index 는 멱등이라 매 호출 안전하다.
    """
    collection = db[COLLECTION_NAME]
    collection.create_index([('result_id', 1)], unique=True)
    collection.create_index([('user_id', 1), ('created_at', DESCENDING)])
    return collection


def save_session_result(db: Database, document: dict) -> str:
    """완성된 결과 문서를 저장하고 result_id 를 반환한다(created_at 은 여기서 찍는다).

    호출부(service)는 result_id·user_id·company_id·conducted_at·result 를 채워 넘긴다.
    created_at(저장 시각)은 정렬·직전 세션 비교의 기준이라 저장 계층에서 확정한다.
    """
    collection = _get_collection(db)
    record = {**document, 'created_at': datetime.now(timezone.utc)}
    collection.insert_one(record)
    logger.info('면접 결과 저장: result_id=%s', document.get('result_id'))
    return document['result_id']


def find_by_id(db: Database, result_id: str) -> dict | None:
    """result_id 로 결과 문서 1건을 조회한다(없으면 None)."""
    return _get_collection(db).find_one({'result_id': result_id})


def find_latest_by_company(db: Database, user_id: str, company_id: str) -> dict | None:
    """그 유저의 해당 회사 최신 세션 1건을 조회한다(없으면 None)."""
    return _get_collection(db).find_one(
        {'user_id': user_id, 'company_id': company_id},
        sort=[('created_at', DESCENDING)],
    )


def find_latest_by_user(db: Database, user_id: str) -> dict | None:
    """그 유저의 가장 최근 세션 1건을 조회한다(직전 세션 비교용, 없으면 None).

    저장 직전에 호출하면 '이번 세션을 뺀 직전 세션'이 된다(현재는 아직 미저장).
    """
    return _get_collection(db).find_one(
        {'user_id': user_id},
        sort=[('created_at', DESCENDING)],
    )


def count_by_user(db: Database, user_id: str) -> int:
    """그 유저의 누적 세션 수(누적 연습 횟수 계산용)."""
    return _get_collection(db).count_documents({'user_id': user_id})
