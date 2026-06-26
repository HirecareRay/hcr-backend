"""기업 데이터 접근 — MariaDB(ORM)·MongoDB(pymongo) 쿼리만 담당.

서비스는 여기를 통해서만 DB에 접근한다. MariaDB 는 auth 와 동일하게
SQLAlchemy ORM(select(Model))로 조회하고, MongoDB 는 SQLAlchemy 대상이
아니라 pymongo 로 조회한다.

캐노니컬 id = 24자 ObjectId. 개요/식별은 Mongo `companies`(_id), 분석은
MariaDB `company_analyses.company_id`(동일 24자).
"""

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.company.models import CompanyAnalysis, News


def find_company(mongo_db: Database, company_id: str) -> dict | None:
    """Mongo companies 에서 회사 1건 조회(없으면 None).

    캐노니컬 id 는 24자 ObjectId(=_id). 혹시 남아 있을 수 있는 20자 company_id
    필드로도 폴백 조회한다.
    """
    if len(company_id) == 24:
        try:
            doc = mongo_db["companies"].find_one({"_id": ObjectId(company_id)})
            if doc:
                return doc
        except InvalidId:
            pass
    return mongo_db["companies"].find_one({"company_id": company_id})


def find_company_analysis(db: Session, company_id: str) -> CompanyAnalysis | None:
    """company_analyses(LLM 분석 보고서)에서 회사 1건 조회(없으면 None)."""
    return db.execute(
        select(CompanyAnalysis).where(CompanyAnalysis.company_id == company_id)
    ).scalar_one_or_none()


def find_news_by_ids(db: Session, news_ids: list[str]) -> list[News]:
    """news 테이블에서 id(=source_key) 목록에 해당하는 기사들을 조회.

    보고서 evidence 의 source_key 로 뉴스 원문(title·url·media 등)을 하이드레이션한다.
    """
    if not news_ids:
        return []
    return list(
        db.execute(select(News).where(News.id.in_(news_ids))).scalars().all()
    )
