"""MongoDB 연결 (pymongo) — 문서 저장용. (RAG 벡터·원문은 MariaDB 담당)

MONGODB_URI 가 비어 있으면 client 는 None 으로 두고 앱은 그대로 기동된다
(DB 접근 시점에 명확한 에러를 던진다). MongoClient 는 지연 연결이라
객체 생성만으로는 네트워크에 접속하지 않는다 — 첫 작업 때 연결된다.
"""

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings

mongo_client: MongoClient | None = (
    MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,  # 서버 못 찾으면 5초 후 에러 (무한 대기 방지)
    )
    if settings.mongodb_uri
    else None
)


def get_mongo_db() -> Database:
    """문서 컬렉션이 담긴 MongoDB 데이터베이스 핸들을 반환한다."""
    if mongo_client is None:
        raise RuntimeError("MONGODB_URI가 설정되지 않았습니다 (.env 확인)")

    return mongo_client[settings.mongodb_db_name]
