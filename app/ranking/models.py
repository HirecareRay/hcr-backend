"""랭킹 도메인의 DB 모델 (SQLAlchemy ORM).

company 도메인과 달리 이 테이블은 우리가 새로 만든다 — main.py 의 create_all 이
없으면 생성한다(기존 회사 테이블들은 읽기 전용이라 no-op 이지만 이건 실제로 만들어짐).

company_view_daily: '기업 리포트 조회'를 회사·날짜별로 집계하는 일일 카운터.
인기기업 순위(trending)는 최근 N 일 합산을 내림차순 정렬해 뽑는다. 일일 granularity 를
유지하므로 '오늘만'·'최근 24시간' 등으로 윈도우만 바꿔 끼우기 쉽다.
캐노니컬 회사 id = 24자 ObjectId(Mongo companies._id) — 다른 회사 테이블과 동일.
"""

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CompanyViewDaily(Base):
    """회사·날짜별 리포트 조회수. (company_id, view_date) 복합 PK 로 일자별 누적."""

    __tablename__ = "company_view_daily"

    company_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    view_date: Mapped[date] = mapped_column(Date, primary_key=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
