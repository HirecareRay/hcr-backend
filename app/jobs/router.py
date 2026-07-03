"""채용공고 라우터 — HTTP 입출력·검증만. 조립은 service 로 위임한다.

홈 카드용 /home/jobs-by-role 를 제공한다(비로그인 공개 조회). URL prefix 는
프론트 계약(/home/*)을 따르고, 코드는 채용공고 도메인(app/jobs)에 둔다.
응답은 CamelModel 로 camelCase 직렬화한다(response_model_by_alias=True).
"""

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from app.db.mongo import get_mongo_db
from app.jobs import service
from app.jobs.job_roles import DEFAULT_ROLES, ROLE_LABELS
from app.jobs.schemas import JobOut, JobsByRoleOut

router = APIRouter(prefix="/home", tags=["jobs"])

# URL 계약은 /jobs/*, 코드는 같은 채용공고 도메인이라 이 파일에 같이 둔다.
jobs_search_router = APIRouter(prefix="/jobs", tags=["jobs"])


def _parse_roles(raw: str) -> list[str]:
    """콤마구분 roles → 알려진 직군만(순서·중복 정리). 유효값 없으면 기본값."""
    result: list[str] = []
    for part in (raw or "").split(","):
        role = part.strip().lower()
        if role in ROLE_LABELS and role not in result:
            result.append(role)
    return result or list(DEFAULT_ROLES)


@router.get(
    "/jobs-by-role",
    response_model=JobsByRoleOut,
    response_model_by_alias=True,
)
def jobs_by_role(
    roles: str = "backend,frontend,ai",
    per_role: int = Query(5, alias="perRole", ge=1, le=50),
    mongo: Database = Depends(get_mongo_db),
) -> JobsByRoleOut:
    """직군별(백엔드·프론트엔드·AI) 진행중 채용공고를 마감임박순으로 반환."""
    data = service.build_jobs_by_role(mongo, _parse_roles(roles), per_role)
    return JobsByRoleOut.model_validate(data)


@jobs_search_router.get(
    "/search",
    response_model=list[JobOut],
    response_model_by_alias=True,
)
def search_jobs(
    q: str = "",
    limit: int = Query(20, ge=1, le=50),
    mongo: Database = Depends(get_mongo_db),
) -> list[dict]:
    """공고명·회사명·직군명(예: AI, 백엔드)으로 진행중 채용공고 검색 — 검색 페이지 '채용공고' 탭용."""
    return service.search_jobs(mongo, q, limit)
