"""FastAPI 진입점.

서버 상태 확인용 /health 와 DB 점검용 /health/db, 도메인별 라우터를 등록한다.
DB 연결은 lifespan 에서 만들어 app.state 에 보관하고 종료 시 자동 정리한다.
"""

from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.company.router import router as company_router
from app.core.config import settings
from app.db.health import check_mariadb, check_mongodb
from app.db.mongo import build_mongo_client
from app.db.session import build_engine, build_session_factory
from app.interview.router import router as interview_router
from app.search.router import router as search_router


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
app.include_router(company_router)
app.include_router(interview_router)
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
    mongodb_ok = check_mongodb(getattr(request.app.state, "mongo_client", None))
    return {
        "status": "ok" if mariadb_ok and mongodb_ok else "degraded",
        "mariadb": mariadb_ok,
        "mongodb": mongodb_ok,
    }


@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}
