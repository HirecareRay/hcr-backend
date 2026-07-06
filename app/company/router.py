"""기업 분석 라우터 — HTTP 입출력·검증만. 조립은 service 로 위임한다.

DB 는 develop 패턴대로 Depends(get_db)·Depends(get_mongo_db)로 주입받는다.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.company import service
from app.db.mongo import get_mongo_db
from app.db.session import get_db
from app.ranking import service as ranking_service

router = APIRouter(prefix="/companies", tags=["company"])


@router.get("/search")
def search_companies(
    q: str = "",
    limit: int = Query(20, ge=1, le=50, description="최대 결과 수 — 자동완성은 5, 미지정 시 20"),
    db: Session = Depends(get_db),
    mongo: Database = Depends(get_mongo_db),
):
    """회사명/업종 검색 — q 부분일치 + company_aliases 정확일치, FE CompanySearchResult 리스트.

    limit 은 DB 조회(.limit)까지 그대로 전달된다. FE 자동완성이 limit=5 로 호출한다.
    주의: /{company_id} 보다 먼저 정의해야 'search'가 id로 안 잡힌다.
    """
    return service.search_companies(mongo, db, q, limit)


@router.get("/jobs")
def search_company_jobs(q: str = "", mongo: Database = Depends(get_mongo_db)):
    """검색 결과 회사들의 연관 채용공고 — q 매칭 회사들의 공고 리스트.

    주의: /{company_id} 보다 먼저 정의해야 'jobs'가 id로 안 잡힌다.
    """
    return service.search_company_jobs(mongo, q)


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
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    mongo: Database = Depends(get_mongo_db),
):
    """회사 분석 보고서 — DB 테이블들을 합쳐 프론트 스키마(8섹션)로 반환.

    조회 성공 시 인기기업 순위 집계용으로 조회수를 +1 한다 — 응답을 막지 않도록
    BackgroundTasks 로 응답 후에 처리한다. record_view 는 새 세션을 직접 열고
    실패도 삼키므로, 집계가 어긋나도 리포트 응답엔 영향이 없다.
    """
    try:
        report = service.build_company_report(db, mongo, company_id)
    except service.CompanyNotFound:
        raise HTTPException(status_code=404, detail="회사 없음")
    background_tasks.add_task(
        ranking_service.record_view, request.app.state.session_factory, company_id
    )
    return report
