"""기업 분석 비즈니스 로직 — DB 데이터를 FE 계약(CompanyReportOut) 모양으로 조립.

v1: companies(식별·개요) + company_analyses(LLM 요약·SWOT) 두 테이블만 사용.
배열 섹션(DART 숫자·리뷰목록·뉴스목록·채용)은 소스 추가조인이 필요해 지금은
빈값 stub 으로 채워 FE Zod 통과만 보장한다.
# ponytail: 배열 섹션은 stub. dart_*/jobplanet_review/news 조인은 섹션별로 추가.
"""

import json

from fastapi import HTTPException
from pymongo.database import Database
from sqlalchemy.orm import Session

from app.company import repository
from app.company.schemas import (
    Company,
    CompanyProfile,
    CompanyReportOut,
    EmployeeSection,
    FinancialSection,
    GrowthSection,
    HiringSection,
    InsightSection,
    NewsItem,
    OverviewSection,
    ReviewSection,
    SwotAnalysis,
)


def _s(value: object) -> str:
    """None 을 빈 문자열로 — str 필수 필드용."""
    return "" if value is None else str(value)


def _i(value: object) -> int:
    """None/비정수를 0 으로 — int 필수 필드용."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _summary(raw: object) -> str:
    """'{"summary": "..."}' JSON 문자열에서 summary 만 뽑는다(없으면 "")."""
    if not raw:
        return ""
    try:
        return json.loads(raw).get("summary", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _texts(raw: object) -> list[str]:
    """'[{"text": "..."}, ...]' JSON 에서 text 들만 리스트로(없으면 [])."""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [t.get("text", "") for t in items if isinstance(t, dict)]


def _build_news(db: Session, sources_raw: object) -> list[NewsItem]:
    """보고서 sources 의 news 키 → news 테이블 hydrate → growth.news[] 구성.

    sources = [{source_key, source_type, ...}, ...]. source_type=="news" 만 골라
    news.id 로 원문을 끌어온다. sources 순서를 유지하고, 매칭 안 되는 키는 건너뛴다.
    """
    if not sources_raw:
        return []
    try:
        sources = json.loads(sources_raw)
    except json.JSONDecodeError:
        return []

    keys = [
        s["source_key"]
        for s in sources
        if isinstance(s, dict) and s.get("source_type") == "news" and s.get("source_key")
    ]
    if not keys:
        return []

    by_id = {n.id: n for n in repository.find_news_by_ids(db, keys)}
    items: list[NewsItem] = []
    for key in keys:  # sources 순서 유지
        n = by_id.get(key)
        if n is None:
            continue
        content = n.page_content or ""
        if content.startswith("passage: "):  # 임베딩 입력용 접두사 제거
            content = content[len("passage: ") :]
        items.append(
            NewsItem(
                id=n.id,
                article_id=_s(n.article_id),
                company=_s(n.company),
                title=_s(n.title),
                media=_s(n.media),
                url=_s(n.url),
                date=_s(n.date),
                article_idx=_i(n.article_idx),
                article_type=_s(n.article_type),
                paragraph_start=_i(n.paragraph_start),
                news_count=_i(n.news_count),
                content=content,
            )
        )
    return items


def _build_overview(c: dict) -> OverviewSection:
    """Mongo companies 문서로 개요 섹션 구성(없는 필드는 빈값)."""
    profile = CompanyProfile(
        industry=_s(c.get("industry")),
        company_size=_s(c.get("company_size")),
        company_type=_s(c.get("company_type")),
        founded=_s(c.get("founded")),
        ceo=_s(c.get("ceo")),
        employee_count=_s(c.get("employee_count")),
        revenue=_s(c.get("revenue")),
        capital="",          # companies 에 없음
        entry_salary="",     # companies 에 없음
        address=_s(c.get("address")),
        main_business=_s(c.get("main_business")),
        credit_rating=None,
        insurance=[],
    )
    return OverviewSection(
        business_description=_s(c.get("main_business")),
        main_products_services=[],
        talent_values=None,
        ceo_message=None,
        website_url=c.get("website_url"),
        profile=profile,
        history=[],
    )


def get_company_report(
    db: Session, mongo_db: Database, company_id: str
) -> CompanyReportOut:
    """기업 분석 리포트 조립. 회사가 없으면 404."""
    c = repository.find_company(mongo_db, company_id)
    if c is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다")

    # company_analyses 는 24자 ObjectId 로 키가 잡혀 있다(companies._id).
    object_id = str(c["_id"])
    a = repository.find_company_analysis(db, object_id)  # CompanyAnalysis | None

    return CompanyReportOut(
        company=Company(
            id=company_id,
            name=_s(c.get("company_name")),
            corp_code="",        # DART corp_code 별도 소스 — 추후
            stock_code=None,
            industry=_s(c.get("industry")),
        ),
        overview=_build_overview(c),
        financial=FinancialSection(
            year="",
            source="DART",
            profitability=[],
            stability=[],
            summary=_summary(getattr(a, "financial_analysis", None)),
        ),
        employees=EmployeeSection(
            year="",
            source="DART",
            total_count=0,
            male_count=0,
            female_count=0,
            avg_salary=None,
            avg_tenure_years=None,
        ),
        review=ReviewSection(
            source="잡플래닛",
            overall_rating=0,
            review_count=0,
            ratings=[],
            pros=[],
            cons=[],
            summary=_summary(getattr(a, "jobplanet_review_summary", None)),
            reviews=[],
        ),
        growth=GrowthSection(
            summary=_summary(getattr(a, "growth_potential", None)),
            news=_build_news(db, getattr(a, "sources", None)),
        ),
        hiring=HiringSection(summary="", openings=[]),
        insight=InsightSection(
            headline="",
            key_points=_texts(getattr(a, "key_points", None)),
            vision="",
            industry=_s(c.get("industry")),
            competitors=[],
            swot=SwotAnalysis(
                strengths=_texts(getattr(a, "swot_strengths", None)),
                weaknesses=_texts(getattr(a, "swot_weaknesses", None)),
                opportunities=_texts(getattr(a, "swot_opportunities", None)),
                threats=_texts(getattr(a, "swot_threats", None)),
            ),
        ),
        generated_at=_s(getattr(a, "generated_at", None)),
    )
