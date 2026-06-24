"""FastAPI 진입점.

지금은 서버가 살아있는지 확인하는 /health 만 있다. 이후 단계에서
DB 연결, 도메인별 라우터(기업 분석·면접 등), LLM 연동을 여기에 붙인다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.company.router import router as company_router
from app.core.config import settings
from app.db.health import check_mariadb, check_mongodb
from app.db.mongo import mongo_client
from app.db.session import engine
from app.interview.router import router as interview_router
from app.search.router import router as search_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 수명 주기. 종료 시 DB 커넥션 풀을 정리한다."""
    yield
    if mongo_client is not None:
        mongo_client.close()  # MongoDB 커넥션 풀 정리
    if engine is not None:
        engine.dispose()  # MariaDB 커넥션 풀 정리


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
def db_health_check():
    """DB 연결 상태 확인 — MariaDB·MongoDB에 핑을 보낸다.

    둘 다 붙으면 status "ok", 하나라도 끊기면 "degraded" 로 알려준다.
    (드라이버·연결 정보가 맞는지 팀에서 빠르게 확인하는 용도)
    """
    mariadb_ok = check_mariadb()
    mongodb_ok = check_mongodb()
    return {
        "status": "ok" if mariadb_ok and mongodb_ok else "degraded",
        "mariadb": mariadb_ok,
        "mongodb": mongodb_ok,
    }


@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}
