"""뉴스 라우터 — HTTP 입출력·검증만. 조립은 service 로 위임한다.

홈 이슈 브리핑용 /home/news 를 제공한다(비로그인 공개 조회). URL prefix 는
프론트 계약(/home/*)을 따르고, 코드는 뉴스 도메인(app/news)에 둔다.
응답은 CamelModel 로 camelCase 직렬화한다(response_model_by_alias=True).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.news import service
from app.news.schemas import NewsListOut

router = APIRouter(prefix="/home", tags=["news"])


@router.get(
    "/news",
    response_model=NewsListOut,
    response_model_by_alias=True,
)
def home_news(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> NewsListOut:
    """전 기업 최신 뉴스(기업 이슈 브리핑) 목록을 발행일 최신순으로 반환."""
    data = service.build_news_list(db, limit)
    return NewsListOut.model_validate(data)
