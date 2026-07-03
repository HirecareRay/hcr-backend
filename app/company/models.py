"""기업 도메인의 DB 모델 (SQLAlchemy ORM).

app/db/session.py 의 Base 를 상속한다. 읽기 전용이라 리포트 조립에 실제로 쓰는
컬럼만 매핑한다(예: news.embedding vector(384) 는 매핑하지 않음). 기존 테이블을
읽기만 하므로 create_all 은 no-op(이미 존재).
MongoDB(companies·dart_*)는 SQLAlchemy 대상이 아니라 pymongo 로 접근한다.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CompanyAnalysis(Base):
    """LLM 분석 보고서 (RAG v2). 요약·SWOT·key_points(JSON longtext)."""

    __tablename__ = "company_analyses"

    analysis_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[str] = mapped_column(String(24), index=True)
    financial_analysis: Mapped[str | None] = mapped_column(Text)
    jobplanet_review_summary: Mapped[str | None] = mapped_column(Text)
    growth_potential: Mapped[str | None] = mapped_column(Text)
    swot_strengths: Mapped[str | None] = mapped_column(Text)
    swot_weaknesses: Mapped[str | None] = mapped_column(Text)
    swot_opportunities: Mapped[str | None] = mapped_column(Text)
    swot_threats: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[date | None] = mapped_column(Date)


class CompanyCrawler(Base):
    """기업 개요 크롤링 — 사업소개·제품·CEO 인사말."""

    __tablename__ = "company_crawler"

    company_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    business_description: Mapped[str | None] = mapped_column(Text)
    main_products_services: Mapped[str | None] = mapped_column(Text)  # JSON 문자열
    ceo_message: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(500))


class JobplanetReview(Base):
    """잡플래닛 리뷰 1건."""

    __tablename__ = "jobplanet_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[str | None] = mapped_column(String(24), index=True)
    overall: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[str | None] = mapped_column(Text)  # JSON 문자열(세부 평점)
    occupation_name: Mapped[str | None] = mapped_column(String(150))
    employ_status_name: Mapped[int | None] = mapped_column(Integer)
    helpful_count: Mapped[int | None] = mapped_column(Integer)
    review_date: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(Text)
    pros: Mapped[str | None] = mapped_column(Text)
    cons: Mapped[str | None] = mapped_column(Text)


class News(Base):
    """뉴스 원문(성장성 섹션). company_id 로 회사별 조회."""

    __tablename__ = "news"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str | None] = mapped_column(String(24), index=True)
    article_id: Mapped[str | None] = mapped_column(String(64))
    company: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(500))
    media: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    date: Mapped[date | None] = mapped_column(Date)
    article_idx: Mapped[int | None] = mapped_column(Integer)
    article_type: Mapped[str | None] = mapped_column(String(100))
    paragraph_start: Mapped[int | None] = mapped_column(Integer)
    news_count: Mapped[int | None] = mapped_column(Integer)
    page_content: Mapped[str | None] = mapped_column(Text)


class JobPosting(Base):
    """채용공고 — 제목 + 원문 URL."""

    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[str | None] = mapped_column(String(24), index=True)
    company_name: Mapped[str | None] = mapped_column(String(255))
    posting_title: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(700))


class SimilarCompany(Base):
    """동종·유사규모 추천(점수순). 복합 PK."""

    __tablename__ = "similar_companies"

    company_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    similar_company_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    similarity_score: Mapped[int | None] = mapped_column(Integer)
