from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from app.db.session import get_db

from app.company.models import News

router = APIRouter()


# --- 홈 뉴스 목록 (전 기업 최신순) ---

def find_latest_news(db: Session, limit: int) -> list[News]:
    """전 기업 최신 뉴스 — 발행일 내림차순. 기사(article_id) 중복 대비 여유분을 뜬다.

    회사 리포트 growth.news 와 동일한 `news` 테이블을 재사용한다.
    """
    return list(
        db.execute(
            select(News).order_by(News.date.desc()).limit(max(limit * 3, limit))
        ).scalars().all()
    )


# --- Repository 함수 (기존 코드 유지) ---

def search_jobs(
    db: Session,
    embedding: list[float],
    limit: int = 10,
):
    sql = text("""
        SELECT
            id,
            title,
            VEC_DISTANCE_COSINE(embedding, :embedding) AS score
        FROM jobs
        ORDER BY score
        LIMIT :limit
    """)

    return db.execute(
        sql,
        {
            "embedding": embedding,
            "limit": limit,
        },
    ).fetchall()

def search_vector(
    db: Session,
    embedding: list[float],
):
    sql = text("""
        SELECT
            company_id,
            title,
            VEC_DISTANCE_COSINE(embedding, :embedding) score
        FROM company
        ORDER BY score
        LIMIT 10
    """)

    return db.execute(
        sql,
        {
            "embedding": embedding,
        },
    ).fetchall()


# --- FastAPI 엔드포인트 구현 (아래 코드 참고하여 적용) ---

@router.post("/search/jobs")
def search_jobs_endpoint(
    embedding: list[float], 
    limit: int = 10, 
    db: Session = Depends(get_db)
):
    # Depends(get_db)를 통해 주입받은 db 세션을 함수에 그대로 전달합니다.
    results = search_jobs(db, embedding=embedding, limit=limit)
    
    # 튜플 형태의 결과를 딕셔너리 리스트로 변환하여 반환 (Pydantic 모델 적용 가능)
    return [{"id": r.id, "title": r.title, "score": r.score} for r in results]


@router.post("/search/companies")
def search_companies_endpoint(
    embedding: list[float], 
    db: Session = Depends(get_db)
):
    results = search_vector(db, embedding=embedding)
    return [{"company_id": r.company_id, "title": r.title, "score": r.score} for r in results]
