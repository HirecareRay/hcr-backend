"""랭킹 라우터 — HTTP 입출력·검증만. 조립은 service 로 위임한다.

DB 는 develop 패턴대로 Depends(get_db)·Depends(get_mongo_db)로 주입받는다.
인기기업 순위는 프론트 BFF(/api/home/feed)가 trending 섹션을 채우려 호출한다.
"""

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.mongo import get_mongo_db
from app.db.session import get_db
from app.ranking import service
from app.ranking.schemas import TrendingCompanyOut

router = APIRouter(prefix="/rankings", tags=["ranking"])


@router.get(
    "/trending",
    response_model=list[TrendingCompanyOut],
    response_model_by_alias=True,
)
def get_trending(
    limit: int | None = Query(default=None, ge=1, le=20),
    db: Session = Depends(get_db),
    mongo: Database = Depends(get_mongo_db),
):
    """인기기업 순위 — 최근 N 일 리포트 조회수 상위 회사 카드 리스트.

    limit 미지정 시 설정 기본값(ranking_trending_default_limit)을 쓴다. 집계 윈도우는
    설정(ranking_trending_window_days)으로 고정 — 순수 '오늘만'은 자정 비움/순위 흔들림이
    있어 기본 7일 롤링이다.
    """
    n = limit or settings.ranking_trending_default_limit
    return service.get_trending(db, mongo, n, settings.ranking_trending_window_days)
