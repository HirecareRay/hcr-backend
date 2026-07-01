"""채용공고 응답 스키마 (Pydantic).

CamelModel 을 상속해 내부 snake_case → 프론트 camelCase 로 자동 직렬화한다.
홈 카드(/home/jobs-by-role)와 후속 목록(/jobs)이 JobOut 을 공유한다.
"""

from app.shared.schema import CamelModel


class JobOut(CamelModel):
    """채용공고 카드 1건."""

    id: str
    company_id: str
    company_name: str
    title: str
    job_role: str
    job_role_label: str
    location: str
    employment_type: str
    deadline: str | None          # 'YYYY-MM-DD' 또는 null(상시채용)
    deadline_type: str            # 'fixed_date' | 'rolling'
    url: str
    tags: list[str]


class JobRoleGroupOut(CamelModel):
    """직군 1개 그룹(공고 0건이어도 jobs=[] 로 유지)."""

    role: str
    label: str
    jobs: list[JobOut]


class JobsByRoleOut(CamelModel):
    """GET /home/jobs-by-role 응답."""

    groups: list[JobRoleGroupOut]
