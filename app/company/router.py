"""기업 분석 라우터 — HTTP 입출력·검증만. 조립은 service 로 위임한다.

DB 는 develop 패턴대로 Depends(get_db)·Depends(get_mongo_db)로 주입받는다.
"""

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.company import service
from app.db.mongo import get_mongo_db
from app.db.session import get_db

router = APIRouter(prefix="/companies", tags=["company"])


@router.get("/search")
def search_companies(q: str = "", mongo: Database = Depends(get_mongo_db)):
    """회사명/업종 검색 — q 부분일치, FE CompanySearchResult 리스트.

    주의: /{company_id} 보다 먼저 정의해야 'search'가 id로 안 잡힌다.
    """
    return service.search_companies(mongo, q)


@router.get("/{company_id}")
def get_company(company_id: str, mongo: Database = Depends(get_mongo_db)):
    """회사 기본정보 — Mongo companies._id(ObjectId)로 조회."""
    try:
        return service.get_company(mongo, company_id)
    except service.CompanyNotFound:
        raise HTTPException(status_code=404, detail="회사 없음")


@router.get("/{company_id}/report")
def get_company_report(
    company_id: str,
    db: Session = Depends(get_db),
    mongo: Database = Depends(get_mongo_db),
):
    """회사 분석 보고서 — DB 테이블들을 합쳐 프론트 스키마(8섹션)로 반환."""
    try:
        return service.build_company_report(db, mongo, company_id)
    except service.CompanyNotFound:
        raise HTTPException(status_code=404, detail="회사 없음")
