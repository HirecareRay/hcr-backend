"""기업 데이터 접근 — MariaDB·MongoDB 쿼리만 담당.

서비스는 여기를 통해서만 DB에 접근한다.
캐노니컬 id = 24자 ObjectId. 개요/식별은 Mongo `companies`(_id=ObjectId),
분석은 MariaDB `company_analyses`(company_id=ObjectId 문자열) — 둘 다 같은 24자로 키 맞춤.
(MariaDB `companies` 테이블은 옛 20자 id라 안 씀.)
"""

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database
from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


def find_company(mongo_db: Database, company_id: str) -> dict | None:
    """Mongo companies 에서 회사 1건 조회(없으면 None).

    FE/브라우저는 20자 legacy company_id 를 보낸다(예: 038bed5f36960ab6dcf2).
    그래서 먼저 company_id 필드(20자)로 찾고, 24자 ObjectId 로 와도 _id 로 받아준다.
    """
    doc = mongo_db["companies"].find_one({"company_id": company_id})
    if doc is None and len(company_id) == 24:
        try:
            doc = mongo_db["companies"].find_one({"_id": ObjectId(company_id)})
        except InvalidId:
            doc = None
    return doc


def find_company_analysis(db: Session, company_id: str) -> RowMapping | None:
    """company_analyses(LLM 분석 보고서)에서 회사 1건 조회(없으면 None)."""
    return (
        db.execute(
            text("SELECT * FROM company_analyses WHERE company_id = :cid"),
            {"cid": company_id},
        )
        .mappings()
        .first()
    )


def find_news_by_ids(db: Session, news_ids: list[str]) -> list[RowMapping]:
    """news 테이블에서 id(=source_key) 목록에 해당하는 기사들을 조회.

    보고서 evidence 의 source_key 로 뉴스 원문(title·url·media 등)을 하이드레이션한다.
    IN 절은 expanding bindparam 으로 안전하게 바인딩한다.
    """
    if not news_ids:
        return []
    stmt = text(
        "SELECT id, article_id, company, title, media, url, date, "
        "article_idx, article_type, paragraph_start, news_count, page_content "
        "FROM news WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    return db.execute(stmt, {"ids": news_ids}).mappings().all()
