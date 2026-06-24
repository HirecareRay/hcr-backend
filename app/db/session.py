"""MariaDB 연결 (SQLAlchemy) — 정형 데이터(기업·채용·재무) + RAG 벡터·원문.

엔진·세션 팩토리는 앱 수명(main.py 의 lifespan)에서 만들어 app.state 에
보관하고, 종료 시 자동 정리된다. 라우터는 Depends(get_db) 로 세션을 주입받고,
DB 접근은 각 도메인의 repository.py 를 통해서만 한다.

MARIADB_URL 이 비어 있으면 엔진을 만들지 않는다(None). 앱은 그대로 기동되고
DB 접근 시점에 명확한 에러를 던진다 — 덕분에 DB 미연결 환경(로컬·CI)에서도
서버와 테스트가 동작한다.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

# 모든 도메인 models.py 가 상속하는 ORM 베이스
Base = declarative_base()


def build_engine() -> Engine | None:
    """MARIADB_URL 이 있으면 커넥션 풀이 달린 엔진을 만든다(없으면 None)."""
    if not settings.mariadb_url:
        return None

    return create_engine(
        settings.mariadb_url,
        pool_pre_ping=True,   # 끊긴 커넥션 자동 감지 후 폐기
        pool_recycle=3600,    # MariaDB wait_timeout 전에 커넥션 재활용
        future=True,
    )


def build_session_factory(engine: Engine | None) -> sessionmaker | None:
    """엔진에 묶인 세션 팩토리를 만든다(엔진이 없으면 None)."""
    if engine is None:
        return None

    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db(request: Request) -> Iterator[Session]:
    """요청 단위 DB 세션 의존성. 라우터에서 Depends(get_db) 로 주입한다.

    세션 팩토리는 lifespan 이 app.state 에 올려둔 것을 쓴다.
    """
    session_factory: sessionmaker | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise RuntimeError("MARIADB_URL이 설정되지 않았습니다 (.env 확인)")

    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()  # 요청 처리 중 에러나면 하다 만 트랜잭션 되돌림
        raise
    finally:
        db.close()  # 성공·실패 무관하게 커넥션을 풀에 반납 (다음 요청이 재사용)
