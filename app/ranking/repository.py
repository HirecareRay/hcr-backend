"""랭킹 도메인 데이터 접근 — MariaDB(조회수 카운터) + MongoDB(회사 메타).

조회수 증가·집계는 MariaDB(company_view_daily), 순위에 붙일 회사명/업종은
MongoDB(companies)에서 가져온다. 조립·변환은 service 가 한다.
캐노니컬 회사 id = 24자 ObjectId(문자열).
"""

from datetime import date

from bson import ObjectId
from pymongo.database import Database
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ranking.models import CompanyViewDaily


# ─── MariaDB (조회수 카운터) ──────────────────────────────────────────
def increment_view(db: Session, company_id: str, today: date) -> None:
    """(company_id, today) 조회수를 1 올린다 — 행이 없으면 만든다.

    방언 중립(MariaDB·SQLite 공통): 먼저 원자적 UPDATE +1 을 시도하고, 해당 행이
    없어(rowcount 0) INSERT 하다 동시 삽입으로 PK 충돌(IntegrityError)이 나면
    이미 다른 요청이 만든 것이므로 UPDATE 로 한 번 더 올린다. commit 은 호출자 책임.
    """
    if _bump(db, company_id, today) > 0:
        return
    try:
        db.add(CompanyViewDaily(company_id=company_id, view_date=today, view_count=1))
        db.flush()
    except IntegrityError:
        db.rollback()
        _bump(db, company_id, today)


def _bump(db: Session, company_id: str, today: date) -> int:
    """기존 행 view_count 를 +1 (원자적). 갱신된 행 수를 반환(0=행 없음)."""
    result = db.execute(
        update(CompanyViewDaily)
        .where(
            CompanyViewDaily.company_id == company_id,
            CompanyViewDaily.view_date == today,
        )
        .values(view_count=CompanyViewDaily.view_count + 1)
    )
    return result.rowcount or 0


def top_company_views(db: Session, since: date, limit: int) -> list[tuple[str, int]]:
    """since(포함) 이후 조회수 합산 상위 회사 — [(company_id, total), ...].

    동률일 때 순위가 요청마다 흔들리지 않도록 company_id 오름차순을 2차 정렬키로
    박아 결정적으로 만든다.
    """
    total = func.sum(CompanyViewDaily.view_count)
    rows = db.execute(
        select(CompanyViewDaily.company_id, total)
        .where(CompanyViewDaily.view_date >= since)
        .group_by(CompanyViewDaily.company_id)
        .order_by(total.desc(), CompanyViewDaily.company_id.asc())
        .limit(limit)
    ).all()
    return [(cid, int(tot or 0)) for cid, tot in rows]


# ─── MongoDB (회사 메타) ──────────────────────────────────────────────
_META_FIELDS = {"company_name": 1, "industry": 1}


def find_company_meta(mongo: Database, ids: list[str]) -> dict[str, dict]:
    """순위 회사들의 표시용 메타 → {idStr: {company_name, industry}}.

    id 형식이 틀린 값은 건너뛴다(조용히 제외 — 순위에서 빠질 뿐 전체 실패는 아님).
    """
    oids = []
    for i in ids:
        try:
            oids.append(ObjectId(i))
        except Exception:
            continue
    if not oids:
        return {}
    cur = mongo["companies"].find({"_id": {"$in": oids}}, _META_FIELDS)
    return {str(d["_id"]): d for d in cur}


def find_seed_companies(mongo: Database, limit: int) -> list[dict]:
    """조회수 데이터가 아직 없을 때(콜드 스타트) 노출할 회사 시드.

    companies 자연순서 상위 N — '실제 인기'가 아니라 빈 피드 방지용 폴백이다.
    """
    return list(mongo["companies"].find({}, _META_FIELDS).limit(limit))
