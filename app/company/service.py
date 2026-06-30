"""기업 분석 비즈니스 로직 — DB 데이터를 프론트 스키마(camelCase 8섹션) dict 로 조립.

라우터는 여기로 위임하고, 여기서 repository(MariaDB ORM + MongoDB)를 조합한다.
무거운 수치(재무지표·임직원)는 DART 실데이터로 채운다.
식별자 = 회사 ObjectId(24자) — 프론트가 ObjectId 를 넘긴다.
"""

import json
import re
from typing import Any

from pymongo.database import Database
from sqlalchemy.orm import Session

from app.company import repository


class CompanyNotFound(Exception):
    """회사 ObjectId 가 companies 에 없을 때."""


# ── JSON·문자열 헬퍼 ──────────────────────────────────────────────────
def _loads(raw: Any, default):
    try:
        return json.loads(raw) if raw else default
    except (json.JSONDecodeError, TypeError):
        return default


def _summary(raw: Any) -> str:
    """company_analyses 섹션({summary, evidence})에서 summary만."""
    d = _loads(raw, {})
    return d.get("summary", "") if isinstance(d, dict) else ""


def _texts(raw: Any) -> list[str]:
    """[{text, ...}] JSON 컬럼에서 text 리스트만."""
    return [x.get("text", "") for x in _loads(raw, []) if isinstance(x, dict) and x.get("text")]


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _logo(name: str) -> str:
    n = re.sub(r"^\(?주\)?|㈜", "", name or "").strip()
    return n[:2].upper() if n else "?"


# ── DART 변환 (raw 문서 → 화면용) ─────────────────────────────────────
_PROFIT = [("매출총이익률", "매출총이익률"), ("순이익률", "순이익률"), ("ROE", "ROE"),
           ("총자산영업이익률", "총자산영업이익률")]
_STABLE = [("부채비율", "부채비율"), ("자기자본비율", "자기자본비율"), ("유동비율", "유동비율")]
_SCORE_LABELS = [("advancement_rating", "승진/성장"), ("compensation_rating", "급여/복지"),
                 ("worklife_balance_rating", "워라밸"), ("culture_rating", "사내문화"),
                 ("management_rating", "경영진")]


def _dart_financial(mongo: Database, oid) -> tuple[str, list[dict], list[dict]]:
    """dart_indicators(최신연도) → (연도, profitability[], stability[])."""
    d = repository.find_dart_indicators(mongo, oid)
    if not d:
        return "", [], []
    ind = d.get("indicators") or {}
    prof, stab = ind.get("수익성지표") or {}, ind.get("안정성지표") or {}
    pf = [{"label": lbl, "value": prof.get(k), "unit": "%"}
          for k, lbl in _PROFIT if isinstance(prof.get(k), (int, float))]
    sb = [{"label": lbl, "value": stab.get(k), "unit": "%"}
          for k, lbl in _STABLE if isinstance(stab.get(k), (int, float))]
    return str(d.get("bsns_year") or ""), pf, sb


def _dart_employees(mongo: Database, oid) -> dict | None:
    """dart_employee.divisions(성별 분리) → 합산 임직원 현황. 없으면 None."""
    d = repository.find_dart_employee(mongo, oid)
    if not d:
        return None
    male = female = tot_sal = ten_w = 0
    for v in (d.get("divisions") or {}).values():
        for g in ("male", "female"):
            gd = v.get(g) or {}
            hc = gd.get("head_count") or 0
            male += hc if g == "male" else 0
            female += hc if g == "female" else 0
            tot_sal += gd.get("total_salary") or 0
            ten_w += (gd.get("avg_tenure") or 0) * hc
    total = male + female
    return {
        "year": str(d.get("bsns_year") or ""), "source": "DART",
        "totalCount": total, "maleCount": male, "femaleCount": female,
        "avgSalary": round(tot_sal / total) if total and tot_sal else None,
        "avgTenureYears": round(ten_w / total, 1) if total and ten_w else None,
    }


# ── 검색 ──────────────────────────────────────────────────────────────
def search_companies(mongo: Database, q: str, limit: int = 20) -> list[dict]:
    """회사명/업종 부분일치 → FE CompanySearchResult 리스트. q 비면 []."""
    q = (q or "").strip()
    if not q:
        return []
    rx = {"$regex": re.escape(q), "$options": "i"}
    return [{
        "id": str(c["_id"]), "key": str(c["_id"]),
        "name": _s(c.get("company_name")), "industry": _s(c.get("industry")),
        "companySize": _s(c.get("company_size")), "companyType": _s(c.get("company_type")),
        "founded": _s(c.get("founded")), "employeeCount": _s(c.get("employee_count")),
        "category": "미디어",          # ponytail: FE 더미 enum placeholder, 일반화 나중
        "logoText": _logo(c.get("company_name")),
    } for c in repository.search_companies(mongo, rx, limit)]


def search_company_jobs(mongo: Database, q: str, limit: int = 20) -> list[dict]:
    """검색 결과 회사들의 채용공고 — /search 페이지 '연관 채용공고'용.

    q 매칭 회사들 → 그 회사들의 job_postings → 카드용 평탄 리스트.
    """
    q = (q or "").strip()
    if not q:
        return []
    rx = {"$regex": re.escape(q), "$options": "i"}
    companies = repository.search_companies(mongo, rx, limit)
    ids = [str(c["_id"]) for c in companies]
    jobs = repository.find_jobs_by_company_ids(mongo, ids, limit)
    return [_related_job(j) for j in jobs]


def _related_job(j: dict) -> dict:
    """job_postings 문서 → 연관공고 카드(RelatedJobPosting) 형태."""
    wc = j.get("work_conditions") or {}
    dtype = _s(wc.get("deadline_type"))
    deadline_raw = wc.get("deadline")
    if dtype == "rolling" or not deadline_raw:
        deadline = "상시채용"
    else:
        deadline = f"{str(deadline_raw).replace('-', '.')} 마감"
    return {
        "id": str(j["_id"]),
        "companyName": _s(j.get("company_name")),
        "title": _s(j.get("posting_title")),
        "url": _s(j.get("source_url")),
        "employmentType": _s(wc.get("employment_type")),
        "deadline": deadline,
    }


def get_company(mongo: Database, company_id: str) -> dict:
    """회사 기본정보 1건(_id 문자열화). 없으면 CompanyNotFound."""
    doc = repository.find_company(mongo, company_id)
    if doc is None:
        raise CompanyNotFound(company_id)
    doc["_id"] = str(doc["_id"])
    return doc


# ── 리포트 조립 ────────────────────────────────────────────────────────
def build_company_report(db: Session, mongo: Database, company_id: str) -> dict:
    """company_id(24자 ObjectId) → 보고서 dict(8섹션). 없으면 CompanyNotFound."""
    company = repository.find_company(mongo, company_id)
    if company is None:
        raise CompanyNotFound(company_id)
    oid = company["_id"]

    analysis = repository.find_analysis(db, company_id)
    crawler = repository.find_crawler(db, company_id)
    jp_count, jp_avg = repository.jobplanet_aggregate(db, company_id)
    jp_rows = repository.find_reviews(db, company_id)
    news_rows = repository.find_news(db, company_id)
    job_rows = repository.find_jobs(mongo, company_id)
    sim_ids = repository.find_similar_ids(db, company_id)

    def _a(name: str):  # analysis 속성(없으면 None)
        return getattr(analysis, name, None)

    industry = _s(company.get("industry"))

    # ── overview ──
    profile = {
        "industry": industry,
        "companySize": _s(company.get("company_size")),
        "companyType": _s(company.get("company_type")),
        "founded": _s(company.get("founded")),
        "ceo": _s(company.get("ceo")),
        "employeeCount": _s(company.get("employee_count")),
        "revenue": _s(company.get("revenue")),
        "capital": "",                 # ponytail: DART 자본금 미적재
        "entrySalary": "",             # ponytail: 초봉 미적재
        "address": _s(company.get("address")),
        "mainBusiness": _s(company.get("main_business")),
        "creditRating": None,
        "insurance": [],
    }
    cr_mps = getattr(crawler, "main_products_services", None)
    overview = {
        "businessDescription": _s(getattr(crawler, "business_description", None)),
        "mainProductsServices": _loads(cr_mps, []) if cr_mps else [],
        "talentValues": None,
        "ceoMessage": getattr(crawler, "ceo_message", None) or None,
        "websiteUrl": company.get("website_url") or getattr(crawler, "website_url", None) or None,
        "profile": profile,
        "history": [],                 # ponytail: company_pages 연혁 파싱 나중
    }

    # ── financial (DART 지표 + 요약) ──
    fin_year, profitability, stability = _dart_financial(mongo, oid)
    financial = {
        "year": fin_year or "2025",
        "source": "DART",
        "profitability": profitability,
        "stability": stability,
        "summary": _summary(_a("financial_analysis")),
    }

    # ── employees (DART 직원현황, 없으면 프로필 인원수) ──
    emp = company.get("employee_count")
    employees = _dart_employees(mongo, oid) or {
        "year": "2025", "source": "DART",
        "totalCount": int(emp) if isinstance(emp, int) else 0,
        "maleCount": 0, "femaleCount": 0, "avgSalary": None, "avgTenureYears": None,
    }

    # ── review (잡플래닛 실데이터) ──
    review_items, dim_sums = [], {k: [] for k, _ in _SCORE_LABELS}
    for r in jp_rows:
        sc = _loads(r.score, {})
        for k, _ in _SCORE_LABELS:
            if isinstance(sc.get(k), (int, float)):
                dim_sums[k].append(sc[k])
        review_items.append({
            "id": int(r.id),
            "rating": int(r.overall or 0),
            "title": _s(r.title), "pros": _s(r.pros), "cons": _s(r.cons),
            "occupation": _s(r.occupation_name), "employStatus": _s(r.employ_status_name),
            "date": _s(r.review_date), "helpfulCount": int(r.helpful_count or 0),
            "scores": {
                "advancement": int(sc.get("advancement_rating") or 0),
                "compensation": int(sc.get("compensation_rating") or 0),
                "worklifeBalance": int(sc.get("worklife_balance_rating") or 0),
                "culture": int(sc.get("culture_rating") or 0),
                "management": int(sc.get("management_rating") or 0),
            },
        })
    ratings = [{"label": lbl, "score": round(sum(dim_sums[k]) / len(dim_sums[k]), 1) if dim_sums[k] else 0}
               for k, lbl in _SCORE_LABELS]
    review = {
        "source": "잡플래닛",
        "overallRating": round(jp_avg, 1),
        "reviewCount": jp_count,
        "ratings": ratings,
        "pros": [], "cons": [],        # ponytail: pros/cons 키워드 집계 나중
        "summary": _summary(_a("jobplanet_review_summary")),
        "reviews": review_items,
    }

    # ── growth (요약 + 뉴스) ──
    growth = {
        "summary": _summary(_a("growth_potential")),
        "news": [{
            "id": _s(n.id), "articleId": _s(n.article_id), "company": _s(n.company),
            "title": _s(n.title), "media": _s(n.media), "url": _s(n.url),
            "date": _s(n.date), "articleIdx": int(n.article_idx or 0),
            "articleType": _s(n.article_type), "paragraphStart": int(n.paragraph_start or 0),
            "newsCount": int(n.news_count or 0), "content": _s(n.page_content),
        } for n in news_rows],
    }

    # ── hiring (공고 카드 — Mongo job_postings 리치) ──
    def _opening(j: dict) -> dict:
        jobs = j.get("jobs") or []
        jb = jobs[0] if jobs else {}        # 카드는 대표 직무 1건 (ponytail)
        tracks = jb.get("tracks") or {}
        track = tracks.get("experienced") or tracks.get("newcomer") or {}
        common = j.get("common") or {}
        wc = j.get("work_conditions") or {}
        return {
            "id": _s(j.get("_id")), "companyName": _s(j.get("company_name")),
            "title": _s(j.get("posting_title")), "url": _s(j.get("source_url")),
            "job": {
                "name": _s(jb.get("job_name")), "headcount": _s(jb.get("headcount")),
                "locations": jb.get("locations") or [],
                "responsibilities": jb.get("responsibilities") or [],
                "requirements": track.get("requirements") or [],
                "preferred": jb.get("preferred_common") or track.get("preferred") or [],
            },
            "qualification": {
                "education": _s(common.get("education") or jb.get("education")),
                "major": _s(common.get("major") or jb.get("major")),
                "documents": common.get("documents") or [],
            },
            "process": j.get("process") or [],
            "workConditions": {
                "employmentType": _s(wc.get("employment_type")),
                "workType": _s(wc.get("work_type")), "salary": _s(wc.get("salary")),
                "benefits": wc.get("benefits") or [],
                "deadline": wc.get("deadline") or None,
                "deadlineType": _s(wc.get("deadline_type")),
            },
        }
    openings = [_opening(j) for j in job_rows]
    hiring = {"summary": "", "openings": openings}

    # ── insight (swot/key_points + 유사기업) ──
    comp_names = repository.find_companies_by_ids(mongo, sim_ids)
    competitors = [{"name": _s(comp_names[s].get("company_name")), "description": _s(comp_names[s].get("industry"))}
                   for s in sim_ids if s in comp_names]
    key_points = _texts(_a("key_points"))
    insight = {
        "headline": (key_points[0] if key_points else _s(company.get("company_name"))),
        "keyPoints": key_points,
        "vision": "",                  # ponytail: 비전 전용 필드 없음
        "industry": industry,
        "competitors": competitors,
        "swot": {
            "strengths": _texts(_a("swot_strengths")),
            "weaknesses": _texts(_a("swot_weaknesses")),
            "opportunities": _texts(_a("swot_opportunities")),
            "threats": _texts(_a("swot_threats")),
        },
    }

    return {
        "company": {
            "id": company_id,
            "name": _s(company.get("company_name")),
            "corpCode": "",            # ponytail: company_corp_codes 미연동
            "stockCode": None,
            "industry": industry,
        },
        "overview": overview,
        "financial": financial,
        "employees": employees,
        "review": review,
        "growth": growth,
        "hiring": hiring,
        "insight": insight,
        "generatedAt": _s(_a("generated_at")) or "",
    }


# ── 면접 컨텍스트 ──────────────────────────────────────────────────────
def build_interview_context(db: Session, mongo: Database, company_id: str) -> str:
    """면접관 LLM 에 주입할 간결한 회사 컨텍스트 문자열을 만든다.

    build_company_report 의 8섹션 중 질문 생성에 쓸모 있는 요약 필드(업종·사업개요·
    인재상·재무/성장 요약·뉴스·채용공고·SWOT 강점)만 추려 텍스트로 직렬화한다 —
    전체 리포트는 너무 길어 프롬프트 토큰을 낭비한다. 회사가 없으면 CompanyNotFound.
    """
    report = build_company_report(db, mongo, company_id)
    company = report["company"]
    overview = report["overview"]
    insight = report["insight"]

    lines: list[str] = [f"회사명: {company['name']}"]
    _append_line(lines, "업종", company.get("industry"))
    _append_line(lines, "사업 개요", overview.get("businessDescription"))
    _append_joined(lines, "핵심 포인트", insight.get("keyPoints"))
    _append_line(lines, "재무 요약", report["financial"].get("summary"))
    _append_line(lines, "성장성 요약", report["growth"].get("summary"))
    news_titles = [n.get("title") for n in report["growth"].get("news") or []]
    _append_joined(lines, "최근 뉴스", news_titles)
    opening_titles = [j.get("title") for j in report["hiring"].get("openings") or []]
    _append_joined(lines, "채용 중 포지션", opening_titles)
    _append_joined(lines, "강점(SWOT)", insight.get("swot", {}).get("strengths"))

    return "\n".join(lines)


def _append_line(lines: list[str], label: str, value: Any) -> None:
    """값이 비어 있지 않을 때만 'label: value' 한 줄을 추가한다."""
    text = _s(value).strip()
    if text:
        lines.append(f"{label}: {text}")


def _append_joined(lines: list[str], label: str, values: Any, limit: int = 5) -> None:
    """리스트에서 빈 값을 거른 뒤 상위 limit 개를 ' / ' 로 이어 한 줄로 추가한다."""
    items = [_s(v).strip() for v in (values or []) if _s(v).strip()]
    if items:
        lines.append(f"{label}: " + " / ".join(items[:limit]))
