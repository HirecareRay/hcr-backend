"""FastAPI 진입점.

지금은 서버가 살아있는지 확인하는 /health 만 있다. 이후 단계에서
DB 연결, 도메인별 라우터(기업 분석·면접 등), LLM 연동을 여기에 붙인다.
"""

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.mongo import closeMongo, connectMongo, getMongoDatabase, pingMongo
from app.report import CompanyNotFound, build_company_report

app = FastAPI(title=settings.appName)

# 프론트(Next.js)에서 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontendOrigin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    """Initialize optional external connections."""
    connectMongo()


@app.on_event("shutdown")
def shutdown() -> None:
    """Close external connections."""
    closeMongo()


@app.get("/health")
def healthCheck():
    """서버 상태 확인용. 배포 후 살아있는지 점검할 때 쓴다."""
    return {"status": "ok", "app": settings.appName}


@app.get("/health/mongo")
def mongoHealthCheck():
    """MongoDB 연결 상태 확인용."""
    try:
        pingMongo()
    except Exception as e:
        return {"status": "error", "mongo": "down", "detail": str(e)}
    return {"status": "ok", "mongo": "up", "database": settings.mongodbDatabase}


@app.get("/companies/{company_id}")
def getCompany(company_id: str):
    """회사 기본정보 — Mongo companies 컬렉션에서 _id(ObjectId)로 조회.

    company_id 는 24자 ObjectId 문자열(예: 6a3ca079d7da326c07819639).
    """
    try:
        oid = ObjectId(company_id)          # 24자 문자열 → ObjectId. 형식 틀리면 예외.
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 company_id 형식")
    doc = getMongoDatabase()["companies"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=404, detail="회사 없음")
    doc["_id"] = str(doc["_id"])            # ObjectId는 JSON으로 못 보내니 문자열로
    return doc


@app.get("/companies/{company_id}/report")
def getCompanyReport(company_id: str):
    """회사 분석 보고서 — DB 테이블들을 합쳐 프론트 스키마(8섹션)로 반환.

    company_id 는 24자 ObjectId. 없는 회사면 404.
    """
    try:
        return build_company_report(company_id)
    except CompanyNotFound:
        raise HTTPException(status_code=404, detail="회사 없음")


@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}
