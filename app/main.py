"""FastAPI 진입점.

지금은 서버가 살아있는지 확인하는 /health 만 있다. 이후 단계에서
DB 연결, 도메인별 라우터(기업 분석·면접 등), LLM 연동을 여기에 붙인다.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.company.router import router as company_router
from app.core.config import settings
from app.interview.router import router as interview_router
from app.search.router import router as search_router

app = FastAPI(title=settings.app_name)

# 프론트(Next.js)에서 호출할 수 있도록 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 도메인별 라우터 등록
app.include_router(company_router)
app.include_router(interview_router)
app.include_router(search_router)


@app.get("/health")
def health_check():
    """서버 상태 확인용. 배포 후 살아있는지 점검할 때 쓴다."""
    return {"status": "ok", "app": settings.app_name}


@app.get("/")
def root():
    return {"message": "HCR Backend 동작 중. 문서는 /docs 참고"}
