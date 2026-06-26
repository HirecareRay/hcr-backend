"""기업 도메인의 DB 모델 (SQLAlchemy ORM).

app/db/session.py 의 Base 를 상속한다. 이 API 는 읽기 전용이라, 리포트 조립에
실제로 쓰는 컬럼만 매핑한다(예: news.embedding vector(384) 는 매핑하지 않음).
기존 테이블을 읽기만 하므로 create_all 은 no-op(이미 존재).
"""

from datetime import date

from sqlalchemy import BigInteger, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CompanyAnalysis(Base):
    """LLM 분석 보고서 (RAG v2). evidence 는 key-only, 원문은 sources/news 로 따로."""

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
    sources: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[date | None] = mapped_column(Date)


class News(Base):
    """뉴스 원문. company_analyses.sources 의 source_key(=News.id)로 하이드레이션한다."""

    __tablename__ = "news"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
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
