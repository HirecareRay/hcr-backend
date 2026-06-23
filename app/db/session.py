"""MariaDB 연결 (SQLAlchemy) — 정형 데이터(기업·채용·재무).

DB 연동 단계에서 활성화한다:
  1. requirements.txt 의 sqlalchemy·pymysql 주석을 푼다
  2. app/core/config.py 에 mariadb_url 필드를 추가한다
  3. .env 에 MARIADB_URL 을 채운다
  4. 아래 주석을 푼다

라우터는 Depends(get_db) 로 세션을 주입받고, DB 접근은 각 도메인의
repository.py 를 통해서만 한다.
"""

# from sqlalchemy import create_engine
# from sqlalchemy.orm import declarative_base, sessionmaker
#
# from app.core.config import settings
#
# engine = create_engine(settings.mariadb_url, pool_pre_ping=True)
# SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
# Base = declarative_base()  # 도메인 models.py 가 상속하는 베이스
#
#
# def get_db():
#     """요청 단위 DB 세션 의존성."""
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
