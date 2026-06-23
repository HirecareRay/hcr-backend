"""기업 도메인의 DB 모델 (SQLAlchemy ORM).

DB 연동 단계에서 app/db/session.py 의 Base 를 상속해 정의한다.
현재는 비어 있다.

예시:
    from sqlalchemy import Column, String
    from app.db.session import Base

    class Company(Base):
        __tablename__ = "companies"
        id = Column(String, primary_key=True)
        name = Column(String, nullable=False)
"""
