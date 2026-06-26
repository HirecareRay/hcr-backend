"""회사 보고서 조립 — DB 여러 테이블 → 프론트 스키마(camelCase 8섹션).

식별자 = 회사 ObjectId(24자). 슬러그 해석 안 함(프론트가 ObjectId를 넘김).
깨끗한 섹션은 실데이터, 무거운 수치(재무지표·임직원 상세·채용 상세)는
스키마-유효한 빈값으로 채운다. (ponytail: dart/리치공고 파싱은 나중에)
소스: Mongo(companies) + MariaDB(company_analyses·jobplanet_review·news·job_postings
·company_crawler·similar_companies).
"""
from __future__ import annotations

import json
import re
from typing import Any

from bson import ObjectId
from sqlalchemy import text

from app.core.mariadb import getEngine
from app.core.mongo import getMongoDatabase


# ── JSON 컬럼 파싱 헬퍼 ────────────────────────────────────────────────
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


# DART 지표 → 화면용 (값은 % 단위 비율)
_PROFIT = [("매출총이익률", "매출총이익률"), ("순이익률", "순이익률"), ("ROE", "ROE"),
           ("총자산영업이익률", "총자산영업이익률")]
_STABLE = [("부채비율", "부채비율"), ("자기자본비율", "자기자본비율"), ("유동비율", "유동비율")]


def _dart_financial(mongo, oid):
    """dart_indicators(최신연도) → (연도, profitability[], stability[])."""
    d = mongo["dart_indicators"].find_one({"company_id": oid}, sort=[("bsns_year", -1)])
    if not d:
        return "", [], []
    ind = d.get("indicators") or {}
    prof, stab = ind.get("수익성지표") or {}, ind.get("안정성지표") or {}
    pf = [{"label": lbl, "value": prof.get(k), "unit": "%"}
          for k, lbl in _PROFIT if isinstance(prof.get(k), (int, float))]
    sb = [{"label": lbl, "value": stab.get(k), "unit": "%"}
          for k, lbl in _STABLE if isinstance(stab.get(k), (int, float))]
    return str(d.get("bsns_year") or ""), pf, sb


def _dart_employees(mongo, oid):
    """dart_employee.divisions(성별 분리) → 합산 임직원 현황. 없으면 None."""
    d = mongo["dart_employee"].find_one({"company_id": oid}, sort=[("bsns_year", -1)])
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


class CompanyNotFound(Exception):
    pass


def _logo(name: str) -> str:
    n = re.sub(r"^\(?주\)?|㈜", "", name or "").strip()
    return (n[:2].upper() if n else "?")


def search_companies(q: str, limit: int = 20) -> list[dict]:
    """회사명/업종 부분일치 검색 → FE CompanySearchResult 형태 리스트. q 비면 [] ."""
    q = (q or "").strip()
    if not q:
        return []
    rx = {"$regex": re.escape(q), "$options": "i"}
    cur = getMongoDatabase()["companies"].find(
        {"$or": [{"company_name": rx}, {"industry": rx}]},
        {"company_name": 1, "industry": 1, "company_size": 1, "company_type": 1,
         "founded": 1, "employee_count": 1},
    ).limit(limit)
    return [{
        "id": str(c["_id"]), "key": str(c["_id"]),
        "name": _s(c.get("company_name")), "industry": _s(c.get("industry")),
        "companySize": _s(c.get("company_size")), "companyType": _s(c.get("company_type")),
        "founded": _s(c.get("founded")), "employeeCount": _s(c.get("employee_count")),
        "category": "미디어",          # ponytail: FE 더미 enum placeholder, 일반화는 나중
        "logoText": _logo(c.get("company_name")),
    } for c in cur]


def build_company_report(company_id: str) -> dict:
    """company_id(24자 ObjectId) → 보고서 dict. 없으면 CompanyNotFound."""
    mongo = getMongoDatabase()
    try:                                       # ObjectId(24자)로만 식별 — 20자 company_id 드롭됨
        company = mongo["companies"].find_one({"_id": ObjectId(company_id)})
    except Exception:
        company = None
    if company is None:
        raise CompanyNotFound(company_id)

    eng = getEngine()
    with eng.connect() as cn:
        analysis = cn.execute(text(
            "SELECT industry_status, recent_trends, financial_analysis, jobplanet_review_summary, "
            "growth_potential, swot_strengths, swot_weaknesses, swot_opportunities, swot_threats, "
            "key_points, generated_at FROM company_analyses WHERE company_id=:i"), {"i": company_id}).mappings().first() or {}

        crawler = cn.execute(text(
            "SELECT business_description, main_products_services, ceo_message, website_url "
            "FROM company_crawler WHERE company_id=:i"), {"i": company_id}).mappings().first() or {}

        jp_agg = cn.execute(text(
            "SELECT COUNT(*) n, AVG(overall) avg FROM jobplanet_review WHERE company_id=:i"),
            {"i": company_id}).mappings().first() or {}
        jp_rows = cn.execute(text(
            "SELECT id, overall, title, pros, cons, occupation_name, employ_status_name, "
            "review_date, helpful_count, score FROM jobplanet_review WHERE company_id=:i "
            "ORDER BY helpful_count DESC LIMIT 10"), {"i": company_id}).mappings().all()

        news_rows = cn.execute(text(
            "SELECT id, article_id, company, title, media, url, date, article_idx, article_type, "
            "paragraph_start, news_count, page_content FROM news WHERE company_id=:i "
            "ORDER BY date DESC LIMIT 10"), {"i": company_id}).mappings().all()

        job_rows = cn.execute(text(
            "SELECT id, company_name, posting_title, source_url FROM job_postings "
            "WHERE company_id=:i LIMIT 20"), {"i": company_id}).mappings().all()

        sim_rows = cn.execute(text(
            "SELECT similar_company_id FROM similar_companies WHERE company_id=:i "
            "ORDER BY similarity_score DESC LIMIT 6"), {"i": company_id}).mappings().all()

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
    overview = {
        "businessDescription": _s(crawler.get("business_description")),
        "mainProductsServices": _loads(crawler.get("main_products_services"), []) if crawler.get("main_products_services") else [],
        "talentValues": None,          # talent_values 적재 제외
        "ceoMessage": crawler.get("ceo_message") or None,
        "websiteUrl": company.get("website_url") or crawler.get("website_url") or None,
        "profile": profile,
        "history": [],                 # ponytail: company_pages 연혁 파싱 나중
    }

    # ── financial (DART 지표 + 요약) ──
    fin_year, profitability, stability = _dart_financial(mongo, company["_id"])
    financial = {
        "year": fin_year or "2025",
        "source": "DART",
        "profitability": profitability,
        "stability": stability,
        "summary": _summary(analysis.get("financial_analysis")),
    }

    # ── employees (DART 직원현황, 없으면 프로필 인원수) ──
    emp = company.get("employee_count")
    employees = _dart_employees(mongo, company["_id"]) or {
        "year": "2025",
        "source": "DART",
        "totalCount": int(emp) if isinstance(emp, int) else 0,
        "maleCount": 0, "femaleCount": 0,
        "avgSalary": None, "avgTenureYears": None,
    }

    # ── review (잡플래닛 실데이터) ──
    _SCORE_LABELS = [("advancement_rating", "승진/성장"), ("compensation_rating", "급여/복지"),
                     ("worklife_balance_rating", "워라밸"), ("culture_rating", "사내문화"),
                     ("management_rating", "경영진")]
    review_items, dim_sums = [], {k: [] for k, _ in _SCORE_LABELS}
    for r in jp_rows:
        sc = _loads(r.get("score"), {})
        for k, _ in _SCORE_LABELS:
            if isinstance(sc.get(k), (int, float)):
                dim_sums[k].append(sc[k])
        review_items.append({
            "id": int(r["id"]),
            "rating": int(r.get("overall") or 0),
            "title": _s(r.get("title")), "pros": _s(r.get("pros")), "cons": _s(r.get("cons")),
            "occupation": _s(r.get("occupation_name")), "employStatus": _s(r.get("employ_status_name")),
            "date": _s(r.get("review_date")), "helpfulCount": int(r.get("helpful_count") or 0),
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
        "overallRating": round(float(jp_agg.get("avg") or 0), 1),
        "reviewCount": int(jp_agg.get("n") or 0),
        "ratings": ratings,
        "pros": [], "cons": [],        # ponytail: pros/cons 키워드 집계 나중
        "summary": _summary(analysis.get("jobplanet_review_summary")),
        "reviews": review_items,
    }

    # ── growth (요약 + 뉴스) ──
    growth = {
        "summary": _summary(analysis.get("growth_potential")),
        "news": [{
            "id": _s(n["id"]), "articleId": _s(n.get("article_id")), "company": _s(n.get("company")),
            "title": _s(n.get("title")), "media": _s(n.get("media")), "url": _s(n.get("url")),
            "date": _s(n.get("date")), "articleIdx": int(n.get("article_idx") or 0),
            "articleType": _s(n.get("article_type")), "paragraphStart": int(n.get("paragraph_start") or 0),
            "newsCount": int(n.get("news_count") or 0), "content": _s(n.get("page_content")),
        } for n in news_rows],
    }

    # ── hiring (공고 카드 — 상세는 빈값) ──
    def _empty_job():
        return {"name": "", "headcount": "", "locations": [], "responsibilities": [],
                "requirements": [], "preferred": []}
    openings = [{
        "id": _s(j["id"]), "companyName": _s(j.get("company_name")),
        "title": _s(j.get("posting_title")), "url": _s(j.get("source_url")),
        "job": _empty_job(),
        "qualification": {"education": "", "major": "", "documents": []},
        "process": [],
        "workConditions": {"employmentType": "", "workType": "", "salary": "",
                           "benefits": [], "deadline": None, "deadlineType": ""},
    } for j in job_rows]   # ponytail: 상세(common/jobs/process)는 job_postings 리치 재적재 시 채움
    hiring = {"summary": "", "openings": openings}

    # ── insight (company_analyses swot/key_points + 유사기업) ──
    comp_names = {}
    sim_ids = [r["similar_company_id"] for r in sim_rows]
    if sim_ids:
        for c in mongo["companies"].find({"_id": {"$in": [ObjectId(s) for s in sim_ids]}},
                                          {"company_name": 1, "industry": 1}):
            comp_names[str(c["_id"])] = (c.get("company_name", ""), c.get("industry", ""))
    competitors = [{"name": comp_names.get(s, ("", ""))[0], "description": comp_names.get(s, ("", ""))[1]}
                   for s in sim_ids if comp_names.get(s)]
    key_points = _texts(analysis.get("key_points"))
    insight = {
        "headline": (key_points[0] if key_points else _s(company.get("company_name"))),
        "keyPoints": key_points,
        "vision": "",                  # ponytail: 비전 전용 필드 없음
        "industry": industry,
        "competitors": competitors,
        "swot": {
            "strengths": _texts(analysis.get("swot_strengths")),
            "weaknesses": _texts(analysis.get("swot_weaknesses")),
            "opportunities": _texts(analysis.get("swot_opportunities")),
            "threats": _texts(analysis.get("swot_threats")),
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
        "generatedAt": _s(analysis.get("generated_at")) or "",
    }
