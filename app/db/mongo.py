"""MongoDB 연결 (pymongo) — 문서 저장용. (RAG 벡터·원문은 MariaDB 담당)

클라이언트는 앱 수명(main.py 의 lifespan)에서 만들어 app.state 에 보관하고,
종료 시 with 로 자동 close 된다. MONGODB_URI 가 비면 만들지 않는다(None).
앱은 그대로 기동되고 DB 접근 시점에 명확한 에러를 던진다.
"""

from fastapi import Request
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings


def build_mongo_client() -> MongoClient | None:
    """MONGODB_URI 가 있으면 MongoClient 를 만든다(없으면 None).

    MongoClient 는 지연 연결이라 객체 생성만으로는 접속하지 않는다 — 첫 작업 때
    연결된다. with 컨텍스트 매니저를 지원하므로 lifespan 에서 자동 close 된다.
    """
    if not settings.mongodb_uri:
        return None

    return MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,  # 서버 못 찾으면 5초 후 에러 (무한 대기 방지)
    )


def get_mongo_db(request: Request) -> Database:
    """문서 컬렉션이 담긴 MongoDB 데이터베이스 핸들을 반환한다.

    클라이언트는 lifespan 이 app.state 에 올려둔 것을 쓴다.
    """
    mongo_client: MongoClient | None = getattr(
        request.app.state, "mongo_client", None
    )
    if mongo_client is None:
        raise RuntimeError("MONGODB_URI가 설정되지 않았습니다 (.env 확인)")

    return mongo_client[settings.mongodb_db_name]
