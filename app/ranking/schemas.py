"""홈 도메인 응답 스키마 — 프론트 features/home/types/home.ts 의 계약을 미러한다.

내부는 snake_case, 직렬화 시 CamelModel 이 camelCase 로 바꾼다. 라우터에서
response_model_by_alias=True 로 내보낸다. 프론트 zod(homeSchema.ts)의 제약과 일치:
logo_text 는 비지 않고(min 1), logo_color 는 '#rrggbb' 6자리 hex. logo_url(camel
직렬화 시 logoUrl)은 nullable - 있으면 프론트가 <img>로 렌더하고, 없거나 로드 실패면
logoText/logoColor 이니셜 원으로 폴백한다.
"""

from app.shared.schema import CamelModel


class TrendingCompanyOut(CamelModel):
    """인기기업 순위 카드 1건 — 프론트 TrendingCompany 와 동일."""

    rank: int
    company_id: str
    name: str
    parent_name: str
    logo_text: str
    logo_color: str
    logo_url: str | None = None
