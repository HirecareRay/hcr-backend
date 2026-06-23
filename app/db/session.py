"""MariaDB 연결 (SQLAlchemy) — 정형 데이터(기업·채용·재무).

라우터는 Depends(get_db) 로 세션을 주입받고, DB 접근은 각 도메인의
repository.py 를 통해서만 한다.

MARIADB_URL 이 비어 있으면 engine 은 None 으로 두고 앱은 그대로 기동된다
(DB 접근 시점에 명확한 에러를 던진다). 덕분에 DB 미연결 환경(로컬·CI)에서도
서버와 테스트가 동작한다.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

# 모든 도메인 models.py 가 상속하는 ORM 베이스
Base = declarative_base()

engine: Engine | None = (
    create_engine(
        settings.mariadb_url,
        pool_pre_ping=True,   # 끊긴 커넥션 자동 감지 후 폐기
        pool_recycle=3600,    # MariaDB wait_timeout 전에 커넥션 재활용
        future=True,
    )
    if settings.mariadb_url
    else None
)

SessionLocal = (
    sessionmaker(bind=engine, autoflush=False, autocommit=False)
    if engine is not None
    else None
)


def get_db() -> Iterator[Session]:
    """요청 단위 DB 세션 의존성. 라우터에서 Depends(get_db) 로 주입한다."""
    if SessionLocal is None:
        raise RuntimeError("MARIADB_URL이 설정되지 않았습니다 (.env 확인)")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
