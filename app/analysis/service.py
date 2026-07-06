"""적합성 분석 서비스 — 5단계 Pipeline.

Stage 1 (병렬): CandidateProfile · JobProfile · CompanyProfile 생성 → MongoDB 저장
Stage 2 (병렬): Candidate vs Job 매칭 · Candidate vs Company 매칭
Stage 3:        카테고리별 집계 + 강점 · 보완점 · 개선 방안 생성

fit_analyses 문서는 join 없이 읽을 수 있도록 Feature 데이터를 embed한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

from bson import ObjectId
from pymongo.database import Database
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings
from app.company import repository as company_repo
from app.analysis.schemas import (
    CandidateProfile, Feature, JobProfile, CompanyProfile,
    LLMJobMatchingResult, LLMCompanyMatchingResult, LLMReportSummary,
    EvidenceRef, JobMatch, CompanyMatch, CategorySummary,
)
from app.analysis.prompts import (
    CANDIDATE_PROFILE_SYSTEM, CANDIDATE_PROFILE_HUMAN,
    JOB_PROFILE_SYSTEM, JOB_PROFILE_HUMAN,
    COMPANY_PROFILE_SYSTEM, COMPANY_PROFILE_HUMAN,
    REQUIREMENT_MATCHER_SYSTEM, REQUIREMENT_MATCHER_HUMAN,
    COMPANY_MATCHER_SYSTEM, COMPANY_MATCHER_HUMAN,
    REPORT_GENERATOR_SYSTEM, REPORT_GENERATOR_HUMAN,
)

logger = logging.getLogger(__name__)

# ponytail: 프로세스 내 동시 요청 dedup — 같은 캐시 키가 진행 중이면 LLM 재실행 대신 대기
_in_flight: dict[str, asyncio.Event] = {}

_FEATURE_PATH_RE = re.compile(r'^(skills|experiences|education|certifications|awards)\[(\d+)\]$')

_CATEGORY_MAP = {
    "required": "자격요건",
    "preferred": "우대사항",
    "responsibility": "주요업무",
    "tech_tool": "기술·도구",
    "career": "경력사항",
    "education": "학력사항",
    "industry_domain": "산업 및 사업 분야",
    "culture": "인재상 및 조직문화",
    "talent_values": "인재상 및 조직문화",
}


class NoDocumentsFound(Exception):
    pass


class JobPostingNotFound(Exception):
    pass


class FitAnalysisNotFound(Exception):
    pass


_MAX_FIT_ANALYSES_PER_JOB = 2


def _prune_old_fit_analyses(mongo: Database, user_id: str, job_posting_id: str, company_id: str) -> None:
    """같은 (user, job, company)는 최신 _MAX_FIT_ANALYSES_PER_JOB개만 남기고 나머지는 삭제."""
    stale_ids = [
        d["_id"]
        for d in mongo.fit_analyses.find(
            {"user_id": user_id, "job_posting_id": job_posting_id, "company_id": company_id},
            {"_id": 1},
        ).sort("analyzed_at", -1).skip(_MAX_FIT_ANALYSES_PER_JOB)
    ]
    if stale_ids:
        mongo.fit_analyses.delete_many({"_id": {"$in": stale_ids}})


def _llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=settings.openai_api_key)


# ── 데이터 준비 ───────────────────────────────────────────────────────

def _to_json(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, default=str)


def _build_company_data(db: Session, mongo: Database, company_id: str) -> dict:
    company = company_repo.find_company(mongo, company_id) or {}
    crawler = company_repo.find_crawler(db, company_id)
    analysis = company_repo.find_analysis(db, company_id)

    data: dict = {k: v for k, v in {
        "company_name": company.get("company_name"),
        "industry": company.get("industry"),
        "company_size": company.get("company_size"),
        "founded": company.get("founded"),
    }.items() if v is not None}

    if crawler:
        for field in ("business_description", "ceo_message"):
            val = getattr(crawler, field, None)
            if val:
                data[field] = val
        if crawler.main_products_services:
            try:
                data["main_products_services"] = json.loads(crawler.main_products_services)
            except Exception:
                data["main_products_services"] = crawler.main_products_services

    if analysis:
        for field in ("swot_strengths", "swot_weaknesses", "swot_opportunities",
                      "swot_threats", "growth_potential", "key_points"):
            val = getattr(analysis, field, None)
            if val:
                data[field] = val
        if analysis.jobplanet_review_summary:
            try:
                data["jobplanet_review_summary"] = json.loads(analysis.jobplanet_review_summary)
            except Exception:
                data["jobplanet_review_summary"] = analysis.jobplanet_review_summary

    return data


# ── Stage 1: Profile 생성 ─────────────────────────────────────────────

def _chain(system: str, human: str, schema):
    prompt = ChatPromptTemplate.from_messages([("system", system), ("human", human)])
    return prompt | _llm().with_structured_output(schema)


async def _gen_candidate_profile(user_doc_json: str) -> CandidateProfile:
    return await _chain(CANDIDATE_PROFILE_SYSTEM, CANDIDATE_PROFILE_HUMAN, CandidateProfile).ainvoke(
        {"user_doc_json": user_doc_json}
    )


async def _gen_job_profile(job_doc_json: str) -> JobProfile:
    return await _chain(JOB_PROFILE_SYSTEM, JOB_PROFILE_HUMAN, JobProfile).ainvoke(
        {"job_doc_json": job_doc_json}
    )


async def _gen_company_profile(company_data_json: str) -> CompanyProfile:
    return await _chain(COMPANY_PROFILE_SYSTEM, COMPANY_PROFILE_HUMAN, CompanyProfile).ainvoke(
        {"company_data_json": company_data_json}
    )


# ── Stage 2: 매칭 ─────────────────────────────────────────────────────

async def _match_job(candidate: CandidateProfile, job: JobProfile) -> LLMJobMatchingResult:
    return await _chain(
        REQUIREMENT_MATCHER_SYSTEM, REQUIREMENT_MATCHER_HUMAN, LLMJobMatchingResult
    ).ainvoke({
        "candidate_profile_json": candidate.model_dump_json(indent=2),
        "job_profile_json": job.model_dump_json(indent=2),
    })


async def _match_company(candidate: CandidateProfile, company: CompanyProfile) -> LLMCompanyMatchingResult:
    return await _chain(
        COMPANY_MATCHER_SYSTEM, COMPANY_MATCHER_HUMAN, LLMCompanyMatchingResult
    ).ainvoke({
        "candidate_profile_json": candidate.model_dump_json(indent=2),
        "company_profile_json": company.model_dump_json(indent=2),
    })


def _resolve_feature(path: str | None, profile: CandidateProfile) -> Feature | None:
    """candidate_feature_path → Feature 객체. 못 찾으면 None."""
    if not path:
        return None
    m = _FEATURE_PATH_RE.match(path)
    if not m:
        return None
    section, idx = m.group(1), int(m.group(2))
    features = getattr(profile, section, None)
    if not features or idx >= len(features):
        return None
    return features[idx]


def _build_evidence_ref(
    path: str | None,
    llm_excerpt: str | None,
    profile: CandidateProfile,
    cp_id: str,
) -> EvidenceRef:
    feat = _resolve_feature(path, profile)
    return EvidenceRef(
        doc_id=cp_id,
        field=path if feat else None,
        feature_name=feat.name if feat else None,
        excerpt=feat.evidence if feat else llm_excerpt,
        source=feat.source if feat else None,
    )


def _build_job_matches(
    llm_result: LLMJobMatchingResult,
    job_posting_id: str,
    candidate_profile: CandidateProfile,
    cp_id: str,
) -> list[JobMatch]:
    return [
        JobMatch(
            job_posting_id=job_posting_id,
            match_target_type=item.match_target_type,
            match_target_text=item.match_target_text,
            match_target_evidence=item.match_target_evidence,
            matched=item.matched,
            candidate_profile_id=cp_id,
            candidate_evidence=_build_evidence_ref(
                item.candidate_feature_path, item.candidate_evidence_excerpt, candidate_profile, cp_id
            ),
            reasoning=item.reasoning,
        )
        for item in llm_result.items
    ]


def _build_company_matches(
    llm_result: LLMCompanyMatchingResult,
    co_id: str,
    candidate_profile: CandidateProfile,
    cp_id: str,
) -> list[CompanyMatch]:
    return [
        CompanyMatch(
            company_profile_id=co_id,
            dimension=item.dimension,
            criterion_text=item.criterion_text,
            criterion_evidence=item.criterion_evidence,
            matched=item.matched,
            candidate_profile_id=cp_id,
            candidate_evidence=_build_evidence_ref(
                item.candidate_feature_path, item.candidate_evidence_excerpt, candidate_profile, cp_id
            ),
            reasoning=item.reasoning,
        )
        for item in llm_result.items
    ]


# ── Stage 3: 리포트 ───────────────────────────────────────────────────

def _build_category_summary(
    job_matches: list[JobMatch],
    company_matches: list[CompanyMatch],
) -> list[CategorySummary]:
    counts: dict[str, dict] = {cat: {"total": 0, "matched": 0} for cat in _CATEGORY_MAP.values()}
    for m in job_matches:
        cat = _CATEGORY_MAP.get(m.match_target_type)
        if cat:
            counts[cat]["total"] += 1
            if m.matched:
                counts[cat]["matched"] += 1
    for m in company_matches:
        cat = _CATEGORY_MAP.get(m.dimension)
        if cat:
            counts[cat]["total"] += 1
            if m.matched:
                counts[cat]["matched"] += 1
    return [
        CategorySummary(category=cat, total=v["total"], matched=v["matched"])
        for cat, v in counts.items() if v["total"] > 0
    ]


def _format_matches_text(job_matches: list[JobMatch], company_matches: list[CompanyMatch]) -> tuple[str, str]:
    def _fmt(matches):
        lines = []
        for m in matches:
            label = _CATEGORY_MAP.get(getattr(m, "match_target_type", None) or getattr(m, "dimension", ""), "기타")
            text = getattr(m, "match_target_text", None) or getattr(m, "criterion_text", "")
            lines.append(f"[{label}] {'✓' if m.matched else '✗'} {text}")
            if m.matched and m.candidate_evidence.excerpt:
                lines.append(f"  근거: {m.candidate_evidence.excerpt}")
            if m.reasoning:
                lines.append(f"  판단: {m.reasoning}")
        return "\n".join(lines)
    return _fmt(job_matches), _fmt(company_matches)


async def _gen_report(
    job_matches: list[JobMatch],
    company_matches: list[CompanyMatch],
) -> LLMReportSummary:
    job_text, company_text = _format_matches_text(job_matches, company_matches)
    return await _chain(
        REPORT_GENERATOR_SYSTEM, REPORT_GENERATOR_HUMAN, LLMReportSummary
    ).ainvoke({
        "job_matches_text": job_text,
        "company_matches_text": company_text,
    })


# ── 진입점 ────────────────────────────────────────────────────────────

async def analyze_fit(
    db: Session,
    mongo: Database,
    user_id: str,
    job_posting_id: str,
    company_id: str,
) -> dict:
    # 1. 이력서 포함 문서 조회
    user_doc = mongo.user_documents.find_one({"user_id": user_id, "resume": {"$exists": True}})
    if not user_doc:
        raise NoDocumentsFound(user_id)

    # docs_updated_at 없으면 오래된 문서 — 지금 시각 발급 후 캐시 미스로 재분석
    docs_updated_at = user_doc.get("docs_updated_at")
    if not docs_updated_at:
        docs_updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        mongo.user_documents.update_one(
            {"user_id": user_id},
            {"$set": {"docs_updated_at": docs_updated_at}},
        )

    # 2. 캐시 조회 — 동일 문서 버전이면 LLM 재실행 없이 반환
    existing = mongo.fit_analyses.find_one({
        "user_id": user_id,
        "job_posting_id": job_posting_id,
        "company_id": company_id,
        "docs_updated_at": docs_updated_at,
    })
    if existing:
        logger.info("캐시 HIT: user=%s job=%s ts=%s", user_id, job_posting_id, docs_updated_at)
        existing["analysis_id"] = str(existing.pop("_id"))
        return existing

    # 진단: 동일 user+job+company는 있는데 ts가 다른지 확인
    stale = mongo.fit_analyses.find_one(
        {"user_id": user_id, "job_posting_id": job_posting_id, "company_id": company_id},
        {"docs_updated_at": 1},
    )
    if stale:
        logger.warning(
            "캐시 MISS(문서 재저장으로 버전 변경): stored_ts=%s current_ts=%s — "
            "save_document_field가 중간에 호출됐거나 문서가 재업로드된 것입니다",
            stale.get("docs_updated_at"), docs_updated_at,
        )
    else:
        logger.info("캐시 MISS(신규): user=%s job=%s company=%s", user_id, job_posting_id, company_id)

    # 동일 캐시 키가 이미 분석 중이면 LLM 재실행 없이 완료 대기 후 DB에서 반환
    flight_key = f"{user_id}:{job_posting_id}:{company_id}:{docs_updated_at}"
    if flight_key in _in_flight:
        logger.info("분석 진행 중(대기): user=%s job=%s", user_id, job_posting_id)
        await _in_flight[flight_key].wait()
        stored = mongo.fit_analyses.find_one({
            "user_id": user_id, "job_posting_id": job_posting_id,
            "company_id": company_id, "docs_updated_at": docs_updated_at,
        })
        if stored:
            stored["analysis_id"] = str(stored.pop("_id"))
            return stored

    event = asyncio.Event()
    _in_flight[flight_key] = event
    logger.info("[파이프라인 실행] user=%s job=%s company=%s", user_id, job_posting_id, company_id)

    try:
        job_doc = mongo.job_postings.find_one({"_id": ObjectId(job_posting_id)})
    except Exception:
        job_doc = None
    if not job_doc:
        raise JobPostingNotFound(job_posting_id)

    company_data = _build_company_data(db, mongo, company_id)

    user_doc_json = _to_json({k: v for k, v in user_doc.items() if k != "_id"})
    job_doc_json = _to_json({k: v for k, v in job_doc.items() if k != "_id"})
    company_data_json = json.dumps(company_data, ensure_ascii=False)

    logger.info("적합성 분석 시작: user=%s job=%s", user_id, job_posting_id)

    # 2. Stage 1: Profile 병렬 생성
    candidate_profile, job_profile, company_profile = await asyncio.gather(
        _gen_candidate_profile(user_doc_json),
        _gen_job_profile(job_doc_json),
        _gen_company_profile(company_data_json),
    )

    # 3. Profile 저장
    cp_id = str(mongo.candidate_profiles.insert_one({
        **candidate_profile.model_dump(), "user_id": user_id,
        "user_doc_id": str(user_doc["_id"]),
    }).inserted_id)
    jp_id = str(mongo.job_profiles.insert_one({
        **job_profile.model_dump(), "job_posting_id": job_posting_id,
    }).inserted_id)
    co_id = str(mongo.company_profiles.insert_one({
        **company_profile.model_dump(), "company_id": company_id,
    }).inserted_id)

    # 4. Stage 2: 매칭 병렬 실행
    job_llm, company_llm = await asyncio.gather(
        _match_job(candidate_profile, job_profile),
        _match_company(candidate_profile, company_profile),
    )

    # CompanyProfile에 실제 데이터가 없는 dimension 항목 제거
    # — 데이터 없으면 LLM이 할루시네이션으로 항목을 만들어 탭 카운트가 왜곡됨
    _has_dim = {
        "industry_domain": bool(company_profile.industry_domain),
        "culture":         bool(company_profile.culture),
        "talent_values":   bool(company_profile.talent_values),
    }
    company_llm.items = [i for i in company_llm.items if _has_dim.get(i.dimension, True)]

    # 5. Feature 데이터 embed — fit_analyses는 join 없이 읽을 수 있다
    job_matches = _build_job_matches(job_llm, job_posting_id, candidate_profile, cp_id)
    company_matches = _build_company_matches(company_llm, co_id, candidate_profile, cp_id)

    # 6. 카테고리별 집계 (Python 도출)
    category_summary = _build_category_summary(job_matches, company_matches)

    # 7. Stage 3: 리포트 생성
    summary = await _gen_report(job_matches, company_matches)

    # 8. 최종 저장 — 동시 요청이 모두 분석을 완료해도 중복 저장 방지
    cache_key = {
        "user_id": user_id,
        "job_posting_id": job_posting_id,
        "company_id": company_id,
        "docs_updated_at": docs_updated_at,
    }
    final = {
        **cache_key,
        "analyzed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "job_title": job_doc.get("posting_title") or job_doc.get("title") or job_profile.job_title,
        "job_names": [j.get("job_name") for j in (job_doc.get("jobs") or []) if j.get("job_name")],
        "company_name": company_data.get("company_name") or company_profile.company_name,
        "candidate_profile_id": cp_id,
        "job_profile_id": jp_id,
        "company_profile_id": co_id,
        "overall_summary": summary.overall_summary,
        "job_matches": [m.model_dump() for m in job_matches],
        "company_matches": [m.model_dump() for m in company_matches],
        "category_summary": [c.model_dump() for c in category_summary],
        "strengths": summary.strengths,
        "improvements": summary.improvements,
        "recommendations": summary.recommendations,
    }
    try:
        result = mongo.fit_analyses.update_one(
            cache_key,
            {"$setOnInsert": final},
            upsert=True,
        )
        if result.upserted_id:
            analysis_id = str(result.upserted_id)
            _prune_old_fit_analyses(mongo, user_id, job_posting_id, company_id)
        else:
            stored = mongo.fit_analyses.find_one(cache_key)
            stored["analysis_id"] = str(stored.pop("_id"))
            logger.info("적합성 분석 완료(동시요청 dedup): analysis_id=%s", stored["analysis_id"])
            return stored

        final["analysis_id"] = analysis_id
        logger.info("적합성 분석 완료: analysis_id=%s", analysis_id)
        return final
    finally:
        event.set()
        _in_flight.pop(flight_key, None)


def list_fit_history(mongo: Database, user_id: str) -> list[dict]:
    """그 유저의 적합도 분석 기록을 최신순 카드 목록으로 요약한다(적합도 분석 탭).

    fit_analyses 저장 시점의 완성품에서 카드에 필요한 필드만 뽑는다 — LLM 재호출 0.
    같은 공고는 최신 1건만 목록에 노출한다(이전 것은 DB에는 남아있고 analysis_id로만 조회 가능).
    """
    docs = mongo.fit_analyses.find(
        {"user_id": user_id},
        {
            "company_id": 1, "company_name": 1,
            "job_posting_id": 1, "job_title": 1, "job_names": 1,
            "analyzed_at": 1, "category_summary": 1,
        },
    ).sort("analyzed_at", -1)
    result = []
    seen_jobs = set()
    for d in docs:
        key = (d.get("company_id"), d.get("job_posting_id"))
        if key in seen_jobs:
            continue  # 같은 공고의 이전 분석 — 최신 것만 목록에 노출
        seen_jobs.add(key)

        # 저장된 category_summary로 종합 매칭률(%)을 계산 — LLM 재호출도, 별도 조회도 없다.
        summary = d.get("category_summary") or []
        total = sum(s.get("total", 0) for s in summary)
        matched = sum(s.get("matched", 0) for s in summary)
        result.append({
            "analysis_id": str(d["_id"]),
            "company_id": d.get("company_id"),
            "company_name": d.get("company_name"),
            "job_posting_id": d.get("job_posting_id"),
            "job_title": d.get("job_title"),
            "job_names": d.get("job_names") or [],
            "analyzed_at": d.get("analyzed_at"),
            "overall_pct": round(matched / total * 100) if total > 0 else None,
        })
    return result


def get_fit_analysis_by_id(mongo: Database, user_id: str, analysis_id: str) -> dict:
    """analysis_id로 히스토리 단건을 그대로 조회한다 — 캐시 키 재계산도, LLM 재실행도 없다."""
    try:
        oid = ObjectId(analysis_id)
    except Exception:
        raise FitAnalysisNotFound(analysis_id)
    doc = mongo.fit_analyses.find_one({"_id": oid, "user_id": user_id})
    if not doc:
        raise FitAnalysisNotFound(analysis_id)
    doc["analysis_id"] = str(doc.pop("_id"))
    return doc
