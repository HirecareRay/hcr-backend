"""채용공고 데이터 접근 — MongoDB `job_postings` 조회만.

조립·분류는 service 가 한다. 회사 리포트 hiring.openings 와 동일한 컬렉션을
재사용한다(catch/incruit v2 정규화 공고).
"""

from pymongo.database import Database

# 분류·카드 조립에 필요한 필드만 가져온다(payload·메모리 절약).
_JOB_PROJECTION = {
    "company_id": 1,
    "company_name": 1,
    "posting_title": 1,
    "source_url": 1,
    "work_conditions.employment_type": 1,
    "work_conditions.deadline": 1,
    "work_conditions.deadline_type": 1,
    "jobs.job_name": 1,
    "jobs.locations": 1,
    "jobs.responsibilities": 1,
    "jobs.preferred_common": 1,
    "jobs.tracks": 1,
    "common.preferred": 1,
}


def find_open_job_postings(mongo: Database, today_str: str) -> list[dict]:
    """진행중(마감 안 된) 채용공고 — deadline 이 오늘 이후이거나 상시/빈값.

    deadline 은 'YYYY-MM-DD' 문자열이라 문자열 비교로 오늘 이후를 판정한다.
    날짜 형식이 아닌 값(빈값·상시 등)은 상시채용으로 보고 항상 포함한다
    (비-숫자 문자열은 코드포인트상 날짜보다 커서 $gte 에도 걸린다).
    """
    query = {
        "$or": [
            {"work_conditions.deadline": {"$gte": today_str}},
            {"work_conditions.deadline": {"$in": ["", None]}},
        ]
    }
    return list(mongo["job_postings"].find(query, _JOB_PROJECTION))
