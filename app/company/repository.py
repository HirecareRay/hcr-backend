"""기업 데이터 접근 — MariaDB(SQLAlchemy ORM) + MongoDB(pymongo) 쿼리만 담당.

서비스는 여기를 통해서만 DB에 접근한다(조립·변환은 service). MariaDB 는 auth 와
동일하게 ORM select(Model) 로, MongoDB 는 pymongo 로 조회한다.
캐노니컬 회사 id = 24자 ObjectId(Mongo companies._id).
"""

from bson import ObjectId
from pymongo.database import Database
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.company.models import (
    CompanyAnalysis,
    CompanyCrawler,
    JobplanetReview,
    JobPosting,
    News,
    SimilarCompany,
)

# ─── MongoDB (companies·dart_*) ──────────────────────────────────────
_SEARCH_FIELDS = {
    "company_name": 1, "industry": 1, "company_size": 1,
    "company_type": 1, "founded": 1, "employee_count": 1,
}


def find_company(mongo: Database, company_id: str) -> dict | None:
    """companies 에서 _id(24자 ObjectId)로 1건. id 형식 틀리면 None."""
    try:
        return mongo["companies"].find_one({"_id": ObjectId(company_id)})
    except Exception:
        return None


def search_companies(mongo: Database, regex: dict, limit: int) -> list[dict]:
    """company_name·industry 부분일치(정규식) 검색."""
    return list(
        mongo["companies"]
        .find({"$or": [{"company_name": regex}, {"industry": regex}]}, _SEARCH_FIELDS)
        .limit(limit)
    )


def find_companies_by_ids(mongo: Database, ids: list[str]) -> dict[str, dict]:
    """유사기업 이름/업종 조회 → {idStr: doc}."""
    if not ids:
        return {}
    cur = mongo["companies"].find(
        {"_id": {"$in": [ObjectId(i) for i in ids]}},
        {"company_name": 1, "industry": 1},
    )
    return {str(d["_id"]): d for d in cur}


def find_dart_indicators(mongo: Database, oid) -> dict | None:
    """dart_indicators 최신 사업연도 1건."""
    return mongo["dart_indicators"].find_one({"company_id": oid}, sort=[("bsns_year", -1)])


def find_dart_employee(mongo: Database, oid) -> dict | None:
    """dart_employee 최신 사업연도 1건."""
    return mongo["dart_employee"].find_one({"company_id": oid}, sort=[("bsns_year", -1)])


# ─── MariaDB (ORM) ───────────────────────────────────────────────────
def find_analysis(db: Session, company_id: str) -> CompanyAnalysis | None:
    return db.execute(
        select(CompanyAnalysis).where(CompanyAnalysis.company_id == company_id)
    ).scalar_one_or_none()


def find_crawler(db: Session, company_id: str) -> CompanyCrawler | None:
    return db.execute(
        select(CompanyCrawler).where(CompanyCrawler.company_id == company_id)
    ).scalar_one_or_none()


def jobplanet_aggregate(db: Session, company_id: str) -> tuple[int, float]:
    """리뷰 개수·평균 평점. (count, avg)."""
    row = db.execute(
        select(func.count(JobplanetReview.id), func.avg(JobplanetReview.overall))
        .where(JobplanetReview.company_id == company_id)
    ).one()
    return int(row[0] or 0), float(row[1] or 0)


def find_reviews(db: Session, company_id: str, limit: int = 10) -> list[JobplanetReview]:
    return list(
        db.execute(
            select(JobplanetReview)
            .where(JobplanetReview.company_id == company_id)
            .order_by(JobplanetReview.helpful_count.desc())
            .limit(limit)
        ).scalars().all()
    )


def find_news(db: Session, company_id: str, limit: int = 10) -> list[News]:
    return list(
        db.execute(
            select(News)
            .where(News.company_id == company_id)
            .order_by(News.date.desc())
            .limit(limit)
        ).scalars().all()
    )


def find_jobs(db: Session, company_id: str, limit: int = 20) -> list[JobPosting]:
    return list(
        db.execute(
            select(JobPosting).where(JobPosting.company_id == company_id).limit(limit)
        ).scalars().all()
    )


def find_similar_ids(db: Session, company_id: str, limit: int = 6) -> list[str]:
    return list(
        db.execute(
            select(SimilarCompany.similar_company_id)
            .where(SimilarCompany.company_id == company_id)
            .order_by(SimilarCompany.similarity_score.desc())
            .limit(limit)
        ).scalars().all()
    )
