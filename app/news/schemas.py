"""뉴스 응답 스키마 (Pydantic).

CamelModel 을 상속해 내부 snake_case → 프론트 camelCase 로 자동 직렬화한다.
"""

from app.shared.schema import CamelModel


class NewsItemOut(CamelModel):
    """기업 이슈 브리핑 아이템 1건."""

    id: str
    company_tag: str
    headline: str
    url: str
    published_at: str             # 'YYYY-MM-DD'


class NewsListOut(CamelModel):
    """GET /home/news 응답."""

    items: list[NewsItemOut]
