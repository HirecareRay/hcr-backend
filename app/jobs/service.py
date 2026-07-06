"""채용공고 비즈니스 로직 — Mongo 공고를 직군 분류 후 카드 형태로 조립.

라우터는 여기로 위임한다. 표기 변환(camelCase)은 응답 스키마(CamelModel)가 한다.
홈 카드(/home/jobs-by-role)와 후속 목록(/jobs)이 같은 잡 아이템 스키마를 공유한다.
"""

import re
from datetime import date

from pymongo.database import Database

from app.company.matching import normalize, search_terms
from app.jobs import repository
from app.jobs.job_roles import ROLE_LABELS, classify_job_role, extract_tech_tags

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _classify_text(doc: dict) -> tuple[str, str]:
    """공고 문서 → (강한 신호=제목·직무명, 약한 신호=자격/우대)."""
    strong = [str(doc.get("posting_title") or "")]
    weak: list[str] = []
    for jb in doc.get("jobs") or []:
        strong.append(str(jb.get("job_name") or ""))
        weak.extend(str(x) for x in (jb.get("responsibilities") or []))
        weak.extend(str(x) for x in (jb.get("preferred_common") or []))
        for tr in (jb.get("tracks") or {}).values():
            if isinstance(tr, dict):
                weak.extend(str(x) for x in (tr.get("requirements") or []))
                weak.extend(str(x) for x in (tr.get("preferred") or []))
    weak.extend(str(x) for x in ((doc.get("common") or {}).get("preferred") or []))
    return " ".join(strong), " ".join(weak)


def _deadline_fields(wc: dict) -> tuple[str | None, str]:
    """work_conditions → (deadline, deadlineType). 날짜면 fixed_date, 아니면 상시(rolling)."""
    dl = str(wc.get("deadline") or "").strip()
    if _DATE_RE.match(dl):
        return dl, "fixed_date"
    return None, "rolling"


def _short_location(locations: list) -> str:
    """공고 근무지 → 시/도 수준의 짧은 표기. 전체 주소면 첫 토큰(시/도)만.

    'catch' 데이터의 locations 는 전체 주소('서울 서초구 …')로 오기도 해서
    카드용으로 첫 어절(시/도)만 남긴다. 없으면 ''.
    """
    if not locations:
        return ""
    first = str(locations[0]).strip()
    return first.split()[0] if first else ""


def _to_job_item(doc: dict) -> dict:
    """job_postings 문서 → 잡 카드 아이템(내부 snake_case)."""
    strong, weak = _classify_text(doc)
    role = classify_job_role(strong, weak)
    wc = doc.get("work_conditions") or {}
    deadline, deadline_type = _deadline_fields(wc)
    jobs = doc.get("jobs") or []
    locations = (jobs[0].get("locations") if jobs else None) or []
    return {
        "id": str(doc.get("_id") or ""),
        "company_id": str(doc.get("company_id") or ""),
        "company_name": str(doc.get("company_name") or ""),
        "title": str(doc.get("posting_title") or ""),
        "job_role": role,
        "job_role_label": ROLE_LABELS.get(role, ROLE_LABELS["etc"]),
        "location": _short_location(locations),
        "employment_type": str(wc.get("employment_type") or ""),
        "deadline": deadline,
        "deadline_type": deadline_type,
        "url": str(doc.get("source_url") or ""),
        "tags": extract_tech_tags(f"{strong} {weak}"),
    }


def _sort_key(item: dict) -> tuple[bool, str]:
    """마감임박 우선(deadline 오름차순), 상시(deadline=null)는 맨 뒤."""
    deadline = item["deadline"]
    return (deadline is None, deadline or "")


def search_jobs(mongo: Database, q: str, limit: int = 20) -> list[dict]:
    """공고명·회사명·직군명에 검색어가 포함된 진행중 채용공고 — /jobs/search(검색 페이지 탭)용.

    직군 탭 고정 분류 없이 전체 진행중 공고를 대상으로 자유 키워드 매칭 후 마감임박순 정렬.
    회사명 쪽은 법인표기/공백 무시 + 별칭까지 확장(search_terms), 제목/직군명은 정규화만
    적용해 띄어쓰기 차이를 흡수한다.
    """
    terms = search_terms(q)
    if not terms:
        return []
    docs = repository.find_open_job_postings(mongo, date.today().isoformat())
    items = [_to_job_item(d) for d in docs]
    matched = [
        it
        for it in items
        if any(
            t in normalize(it["title"]) or t in normalize(it["company_name"]) or t in normalize(it["job_role_label"])
            for t in terms
        )
    ]
    return sorted(matched, key=_sort_key)[:limit]


def build_jobs_by_role(
    mongo: Database,
    roles: list[str],
    per_role: int,
    today: date | None = None,
) -> dict:
    """진행중 공고를 직군 분류 → 요청 직군별 마감임박순 상위 per_role 개로 조립.

    특정 직군에 공고가 0건이면 jobs=[] 로 그룹은 유지한다(프론트 빈 상태 처리).
    """
    today = today or date.today()
    docs = repository.find_open_job_postings(mongo, today.isoformat())
    items = [_to_job_item(d) for d in docs]

    by_role: dict[str, list[dict]] = {}
    for it in items:
        by_role.setdefault(it["job_role"], []).append(it)

    groups = [
        {
            "role": role,
            "label": ROLE_LABELS.get(role, role),
            "jobs": sorted(by_role.get(role, []), key=_sort_key)[:per_role],
        }
        for role in roles
    ]
    return {"groups": groups}
