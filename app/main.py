"""FastAPI 진입점.

서버 상태 확인용 /health 와 DB 점검용 /health/db, 도메인별 라우터를 등록한다.
DB 연결은 lifespan 에서 만들어 app.state 에 보관하고 종료 시 자동 정리한다.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

# Base.metadata 에 테이블을 등록하려면 모델 모듈이 import 돼 있어야 한다.
# (auth_router import 로도 끌려오지만, 명시적으로 의존성을 드러낸다)
from app.auth import models as _auth_models  # noqa: F401
from app.auth.router import router as auth_router
from app.company.router import router as company_router
from app.core.config import settings
from app.db.health import check_mariadb, check_mongodb
from app.db.mongo import build_mongo_client
from app.db.session import Base, build_engine, build_session_factory
from app.analysis.router import router as analysis_router
from app.documents.router import router as documents_router
from app.interview.router import router as interview_router
from app.jobs.router import router as jobs_router
from app.news.router import router as news_router
from app.ranking import models as _ranking_models  # noqa: F401  (create_all 용 테이블 등록)
from app.ranking.router import router as ranking_router
from app.search.router import router as search_router

logger = logging.getLogger(__name__)


def _ensure_tables(engine: Engine) -> None:
    """등록된 ORM 테이블을 보장한다(있으면 무시).

    초기 단계라 Alembic 대신 부팅 시 생성한다. DB 가 닿지 않는 환경
    (로컬 터널 꺼짐·CI)에서도 서버는 떠야 하므로, 실패해도 경고만 남기고
    부팅을 막지 않는다.
    TODO: 스키마가 안정되면 Alembic 마이그레이션으로 교체
    """
    try:
        Base.metadata.create_all(bind=engine)
    except SQLAlchemyError as exc:
        logger.warning("테이블 생성 생략 — DB 연결 실패: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 수명 주기.

    시작 시 DB 연결을 만들어 app.state 에 보관하고, 종료 시 ExitStack 이
    등록된 정리를 자동 실행한다(커넥션 풀 닫기). with/ExitStack 으로 묶어
    close 를 손으로 부르지 않아도 누수 없이 정리된다.
    """
    with ExitStack() as stack:
        engine = build_engine()
        if engine is not None:
            stack.callback(engine.dispose)  # 종료 시 MariaDB 풀 정리
            _ensure_tables(engine)
        app.state.db_engine = engine
        app.state.session_factory = build_session_factory(engine)

        mongo_client = build_mongo_client()
        if mongo_client is not None:
            stack.enter_context(mongo_client)  # with 처럼 종료 시 close 자동
        app.state.mongo_client = mongo_client

        yield
    # ExitStack 블록을 벗어나며 mongo close · engine.dispose 가 자동 실행됨


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 프론트(Next.js)에서 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 도메인별 라우터 등록
app.include_router(auth_router)
app.include_router(analysis_router)
app.include_router(company_router)
app.include_router(documents_router)
app.include_router(interview_router)
app.include_router(jobs_router)
app.include_router(news_router)
app.include_router(ranking_router)
app.include_router(search_router)


@app.get("/health")
def health_check():
    """서버 상태 확인용. 배포 후 살아있는지 점검할 때 쓴다."""
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db")
def db_health_check(request: Request):
    """DB 연결 상태 확인 — MariaDB·MongoDB에 핑을 보낸다.

    둘 다 붙으면 status "ok", 하나라도 끊기면 "degraded" 로 알려준다.
    (드라이버·연결 정보가 맞는지 팀에서 빠르게 확인하는 용도)
    """
    mariadb_ok = check_mariadb(getattr(request.app.state, "db_engine", None))
    # check_mongodb 는 (연결여부, 콜렉션목록) 튜플을 준다 — 헬스 응답엔 bool 만 쓴다.
    mongodb_ok, _collections = check_mongodb(getattr(request.app.state, "mongo_client", None))
    return {
        "status": "ok" if mariadb_ok and mongodb_ok else "degraded",
        "mariadb": mariadb_ok,
        "mongodb": mongodb_ok,
    }

@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}