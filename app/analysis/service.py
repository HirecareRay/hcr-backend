"""적합도 분석 서비스.

흐름: 유저 문서 + 회사 정보 + RAG 컨텍스트 → LLM → FitResult
"""
from __future__ import annotations

import json
import logging

from pymongo.database import Database
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings
from app.company import repository as company_repo
from app.analysis.schemas import FitResult
from app.analysis.prompts import SYSTEM_PROMPT, HUMAN_TEMPLATE

logger = logging.getLogger(__name__)


class NoDocumentsFound(Exception):
    pass


# ── 유저 문서 텍스트 직렬화 ────────────────────────────────────────────

def _doc_text(user_doc: dict) -> str:
    parts: list[str] = []

    resume = user_doc.get("resume") or {}
    if resume:
        parts.append("[이력서]")
        for s in (resume.get("school") or []):
            parts.append(f"학력: {s.get('name','')} {s.get('major','')} ({s.get('graduate','')})")
        for c in (resume.get("career") or []):
            parts.append(f"경력: {c.get('name','')} {c.get('position','')} "
                         f"({c.get('start_date','')}-{c.get('end_date','')})")
        skills = ", ".join(
            f"{t.get('name','')}({t.get('proficiency','')})"
            for t in (resume.get("tools_skills") or []) if t.get("name")
        )
        if skills:
            parts.append(f"기술: {skills}")
        certs = ", ".join(c.get("name", "") for c in (resume.get("certifications") or []) if c.get("name"))
        if certs:
            parts.append(f"자격증: {certs}")

    we = user_doc.get("work_experience") or {}
    for w in (we.get("work_experience") or []):
        parts.append(f"\n[경력기술서] {w.get('company_name','')} | {w.get('position','')} "
                     f"({w.get('start_date','')}-{w.get('end_date','')})")
        for r in (w.get("responsibilities") or []):
            parts.append(f"  · {r}")
        for p in (w.get("projects") or []):
            parts.append(f"  [프로젝트] {p.get('name','')} – {p.get('description','')}")

    cl = user_doc.get("cover_letter") or {}
    for item in (cl.get("items") or []):
        if item.get("content"):
            parts.append(f"\n[자기소개서 – {item.get('title','')}]\n{item.get('content','')}")

    return "\n".join(parts)


# ── 채용 공고 텍스트 직렬화 ───────────────────────────────────────────

def _job_text(job: dict, company_name: str, industry: str) -> str:
    lines = [
        f"회사: {company_name} ({industry})",
        f"직무: {job.get('job_title', '')}",
        "",
        "주요 업무:",
        *[f"- {r}" for r in job.get("responsibilities", [])],
        "",
        "자격 요건:",
        *[f"- {r}" for r in job.get("requirements", [])],
    ]
    if job.get("preferred_qualifications"):
        lines += ["", "우대 사항:", *[f"- {r}" for r in job["preferred_qualifications"]]]
    return "\n".join(lines)


# ── RAG 컨텍스트 조립 ────────────────────────────────────────────────
# TODO: 아래 함수에 실제 RAG 로직을 구현하세요.
#   1. 동일 회사 이전 채용 공고: company_repo.find_jobs(db, company_id) 로 조회 후 텍스트화
#   2. 유사 직무 공고: 임베딩 유사도 검색 (app/ai/embedding.py 참고)
#   현재는 빈 문자열 반환 → HUMAN_TEMPLATE의 {rag_context} 슬롯에 삽입됨

def _build_rag_context(db: Session, mongo: Database, company_id: str, job_title: str) -> str:
    # ponytail: RAG 미구현 — 빈 문자열 반환. 구현 시 이 함수만 채우면 됩니다.
    return ""


# ── 기업 문화 분석 요약 (MariaDB, 없으면 빈 문자열) ───────────────────

def _culture_summary(db: Session, company_id: str) -> str:
    try:
        analysis = company_repo.find_analysis(db, company_id)
        if not analysis:
            return ""
        raw = getattr(analysis, "jobplanet_review_summary", None)
        if not raw:
            return ""
        d = json.loads(raw) if isinstance(raw, str) else raw
        return d.get("summary", "") if isinstance(d, dict) else ""
    except Exception:
        return ""


# ── 진입점 ────────────────────────────────────────────────────────────

def analyze_fit(db: Session, mongo: Database, user_id: str, job: dict) -> dict:
    # 1. 유저 문서
    user_doc = mongo.user_documents.find_one({"user_id": user_id})
    if not user_doc:
        raise NoDocumentsFound(user_id)

    docs_text = _doc_text(user_doc)
    if not docs_text.strip():
        raise NoDocumentsFound(user_id)

    # 2. 회사 기본정보
    company = company_repo.find_company(mongo, job["company_id"]) or {}
    company_name = str(company.get("company_name") or "")
    industry = str(company.get("industry") or "")

    # 3. 텍스트 조립
    job_text = _job_text(job, company_name, industry)

    # 기업 문화 요약은 job_text 끝에 붙임 (빈 문자열이면 생략)
    culture = _culture_summary(db, job["company_id"])
    if culture:
        job_text += f"\n\n기업 문화 분석 요약: {culture}"

    # 4. RAG 컨텍스트 (TODO: _build_rag_context 구현 후 자동 반영)
    rag_context = _build_rag_context(db, mongo, job["company_id"], job.get("job_title", ""))

    # 5. LLM 호출
    logger.info("적합도 분석 LLM 호출: user_id=%s company=%s", user_id, company_name)
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=settings.openai_api_key)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", HUMAN_TEMPLATE),
    ])
    result: FitResult = (prompt | llm.with_structured_output(FitResult)).invoke({
        "job_text": job_text,
        "docs_text": docs_text,
        "rag_context": rag_context,
    })
    return result.model_dump()
