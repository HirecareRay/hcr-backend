"""DB 연결 상태 점검 — /health/db 엔드포인트에서 사용한다.

각 DB에 가벼운 핑을 보내 연결 여부만 bool 로 돌려준다. 실패 사유는
서버 로그로만 남기고 응답에는 노출하지 않는다(접속정보 유출 방지).

엔진·클라이언트는 lifespan 이 app.state 에 올려둔 것을 라우터가 넘겨준다.
"""

import logging

from pymongo import MongoClient
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)


def check_mariadb(engine: Engine | None) -> bool:
    """MariaDB에 SELECT 1 을 보내 연결을 확인한다."""
    if engine is None:
        return False

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — 연결 실패 사유는 로그로만
        logger.warning("MariaDB 헬스체크 실패: %s", exc)
        return False

def check_mongodb(mongo_client: MongoClient | None) -> tuple[bool, list[str]]:
    """MongoDB 연결을 확인하고 모든 데이터베이스의 콜렉션 목록을 반환한다."""
    if mongo_client is None:
        return False, []
    try:
        # 1. 핑 테스트로 연결 확인
        mongo_client.admin.command("ping")

        # 2. 모든 데이터베이스의 콜렉션 목록 조회
        all_collections = []
        for db_name in mongo_client.list_database_names():
            db = mongo_client[db_name]
            for coll_name in db.list_collection_names():
                all_collections.append(f"{db_name}.{coll_name}")
                
        return True, all_collections
    except Exception as exc:  # noqa: BLE001 — 연결 실패 사유는 로그로만
        logger.warning("MongoDB 헬스체크 실패: %s", exc)
        return False, []