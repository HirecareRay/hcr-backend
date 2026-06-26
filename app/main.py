"""FastAPI 진입점.

서버 상태 확인용 /health 와 DB 점검용 /health/db, 도메인별 라우터를 등록한다.
DB 연결은 lifespan 에서 만들어 app.state 에 보관하고 종료 시 자동 정리한다.
회사 분석 리포트/검색은 report.py 의 조립 함수로 직접 엔드포인트를 등록한다
(develop 의 company_router 스텁 대신 report.py 가 실데이터를 담당하므로 제외).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

# Base.metadata 에 테이블을 등록하려면 모델 모듈이 import 돼 있어야 한다.
from app.auth import models as _auth_models  # noqa: F401
from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.mongo import closeMongo, getMongoDatabase
from app.db.health import check_mariadb, check_mongodb
from app.db.mongo import build_mongo_client
from app.db.session import Base, build_engine, build_session_factory
from app.documents.router import router as documents_router
from app.interview.router import router as interview_router
from app.report import CompanyNotFound, build_company_report, search_companies
from app.search.router import router as search_router

logger = logging.getLogger(__name__)


def _ensure_tables(engine: Engine) -> None:
    """등록된 ORM 테이블을 보장한다(있으면 무시).

    초기 단계라 Alembic 대신 부팅 시 생성한다. DB 가 닿지 않는 환경
    (로컬 터널 꺼짐·CI)에서도 서버는 떠야 하므로, 실패해도 경고만 남기고
    부팅을 막지 않는다.
    """
    try:
        Base.metadata.create_all(bind=engine)
    except SQLAlchemyError as exc:
        logger.warning("테이블 생성 생략 — DB 연결 실패: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 수명 주기. 시작 시 DB 연결을 만들어 app.state 에 보관, 종료 시 자동 정리."""
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

        stack.callback(closeMongo)  # report.py 가 쓰는 core mongo 클라이언트도 종료 시 정리

        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 프론트(Next.js)에서 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 도메인별 라우터 등록 (회사 리포트/검색은 아래 report.py 엔드포인트가 담당)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(interview_router)
app.include_router(search_router)


@app.get("/health")
def health_check():
    """서버 상태 확인용. 배포 후 살아있는지 점검할 때 쓴다."""
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db")
def db_health_check(request: Request):
    """DB 연결 상태 확인 — MariaDB·MongoDB에 핑을 보낸다."""
    mariadb_ok = check_mariadb(getattr(request.app.state, "db_engine", None))
    mongodb_ok = check_mongodb(getattr(request.app.state, "mongo_client", None))
    return {
        "status": "ok" if mariadb_ok and mongodb_ok else "degraded",
        "mariadb": mariadb_ok,
        "mongodb": mongodb_ok,
    }


@app.get("/companies/search")
def searchCompaniesRoute(q: str = ""):
    """회사명/업종 검색 — q 부분일치, FE CompanySearchResult 리스트.

    주의: /companies/{company_id} 보다 먼저 정의해야 'search'가 id로 안 잡힌다.
    """
    return search_companies(q)


@app.get("/companies/{company_id}")
def getCompany(company_id: str):
    """회사 기본정보 — Mongo companies 컬렉션에서 _id(ObjectId)로 조회."""
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 company_id 형식")
    doc = getMongoDatabase()["companies"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="회사 없음")
    doc["_id"] = str(doc["_id"])
    return doc


@app.get("/companies/{company_id}/report")
def getCompanyReport(company_id: str):
    """회사 분석 보고서 — DB 테이블들을 합쳐 프론트 스키마(8섹션)로 반환."""
    try:
        return build_company_report(company_id)
    except CompanyNotFound:
        raise HTTPException(status_code=404, detail="회사 없음")


@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}
