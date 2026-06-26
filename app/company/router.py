"""기업 분석 라우터 — HTTP 입출력·검증만. 로직은 service 로 위임한다.

response_model_by_alias=True 로 응답을 camelCase 로 내보내 프론트 계약에 맞춘다.
"""

from fastapi import APIRouter, Depends
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.company.schemas import CompanyReportOut
from app.company.service import get_company_report
from app.db.mongo import get_mongo_db
from app.db.session import get_db

router = APIRouter(prefix="/companies", tags=["company"])


@router.get(
    "/{company_id}/report",
    response_model=CompanyReportOut,
    response_model_by_alias=True,
)
def read_company_report(
    company_id: str,
    db: Session = Depends(get_db),
    mongo_db: Database = Depends(get_mongo_db),
) -> CompanyReportOut:
    """기업 분석 리포트 조회."""
    return get_company_report(db, mongo_db, company_id)
