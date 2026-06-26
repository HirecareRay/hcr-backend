"""기업 분석 도메인의 응답 스키마 (Pydantic).

프론트 계약(features/company/types/companyReportSchema.ts 의 Zod)을 1:1 미러링한다.
모든 응답 모델은 CamelModel 을 상속 → 코드에선 snake_case, JSON 출력은 camelCase.
라우터에서 response_model_by_alias=True 로 내보낸다.

값이 없는 필드는 None/빈 배열/0 으로 채워 프론트 Zod parse 를 통과시킨다
(Zod 가 엄격해서 한 필드라도 빠지면 실패하기 때문).
"""

from app.shared.schema import CamelModel

# ─── 기업 식별 정보 ───────────────────────────────────────────────
class Company(CamelModel):
    id: str
    name: str
    corp_code: str            # corpCode (DART 고유번호) — 없으면 ""
    stock_code: str | None    # stockCode (상장사만, 비상장 None)
    industry: str


# ─── 기업 개요 ────────────────────────────────────────────────────
class CompanyProfile(CamelModel):
    industry: str
    company_size: str
    company_type: str
    founded: str
    ceo: str
    employee_count: str
    revenue: str
    capital: str
    entry_salary: str
    address: str
    main_business: str
    credit_rating: str | None
    insurance: list[str]


class CompanyHistoryEvent(CamelModel):
    year: str
    month: str
    events: list[str]


class OverviewSection(CamelModel):
    business_description: str
    main_products_services: list[str]
    talent_values: str | None
    ceo_message: str | None
    website_url: str | None
    profile: CompanyProfile
    history: list[CompanyHistoryEvent]


# ─── 재무 분석 (DART) ─────────────────────────────────────────────
class FinancialIndicator(CamelModel):
    label: str
    value: float | None
    unit: str


class FinancialSection(CamelModel):
    year: str
    source: str
    profitability: list[FinancialIndicator]
    stability: list[FinancialIndicator]
    summary: str


# ─── 임직원 현황 (DART) ───────────────────────────────────────────
class EmployeeSection(CamelModel):
    year: str
    source: str
    total_count: int
    male_count: int
    female_count: int
    avg_salary: float | None
    avg_tenure_years: float | None


# ─── 평판 / 리뷰 (잡플래닛) ───────────────────────────────────────
class ReviewRating(CamelModel):
    label: str
    score: float


class ReviewScore(CamelModel):
    advancement: float
    compensation: float
    worklife_balance: float
    culture: float
    management: float


class ReviewItem(CamelModel):
    id: int
    rating: float
    title: str
    pros: str
    cons: str
    occupation: str
    employ_status: str
    date: str
    helpful_count: int
    scores: ReviewScore


class ReviewSection(CamelModel):
    source: str
    overall_rating: float
    review_count: int
    ratings: list[ReviewRating]
    pros: list[str]
    cons: list[str]
    summary: str
    reviews: list[ReviewItem]


# ─── 성장성 / 뉴스 ────────────────────────────────────────────────
class NewsItem(CamelModel):
    id: str
    article_id: str
    company: str
    title: str
    media: str
    url: str
    date: str
    article_idx: int
    article_type: str
    paragraph_start: int
    news_count: int
    content: str


class GrowthSection(CamelModel):
    summary: str
    news: list[NewsItem]


# ─── 채용 정보 ────────────────────────────────────────────────────
class JobDetail(CamelModel):
    name: str
    headcount: str
    locations: list[str]
    responsibilities: list[str]
    requirements: list[str]
    preferred: list[str]


class JobQualification(CamelModel):
    education: str
    major: str
    documents: list[str]


class JobWorkConditions(CamelModel):
    employment_type: str
    work_type: str
    salary: str
    benefits: list[str]
    deadline: str | None
    deadline_type: str


class JobPosting(CamelModel):
    id: str
    company_name: str
    title: str
    url: str
    job: JobDetail
    qualification: JobQualification
    process: list[str]
    work_conditions: JobWorkConditions


class HiringSection(CamelModel):
    summary: str
    openings: list[JobPosting]


# ─── AI 인사이트 (LLM) ────────────────────────────────────────────
class SwotAnalysis(CamelModel):
    strengths: list[str]
    weaknesses: list[str]
    opportunities: list[str]
    threats: list[str]


class Competitor(CamelModel):
    name: str
    description: str


class InsightSection(CamelModel):
    headline: str
    key_points: list[str]
    vision: str
    industry: str
    competitors: list[Competitor]
    swot: SwotAnalysis


# ─── 보고서 전체 ──────────────────────────────────────────────────
class CompanyReportOut(CamelModel):
    company: Company
    overview: OverviewSection
    financial: FinancialSection
    employees: EmployeeSection
    review: ReviewSection
    growth: GrowthSection
    hiring: HiringSection
    insight: InsightSection
    generated_at: str
