"""DB 연결 상태 점검 — /health/db 엔드포인트에서 사용한다.

각 DB에 가벼운 핑을 보내 연결 여부만 bool 로 돌려준다. 실패 사유는
서버 로그로만 남기고 응답에는 노출하지 않는다(접속정보 유출 방지).
"""

import logging

from sqlalchemy import text

from app.db.mongo import mongo_client
from app.db.session import engine

logger = logging.getLogger(__name__)


def check_mariadb() -> bool:
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


def check_mongodb() -> bool:
    """MongoDB admin 'ping' 명령으로 연결을 확인한다."""
    if mongo_client is None:
        return False

    try:
        mongo_client.admin.command("ping")
        return True
    except Exception as exc:  # noqa: BLE001 — 연결 실패 사유는 로그로만
        logger.warning("MongoDB 헬스체크 실패: %s", exc)
        return False
