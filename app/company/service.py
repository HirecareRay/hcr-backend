"""기업 분석 비즈니스 로직 — DB 조회·LLM·RAG를 조합해 리포트를 만든다.

라우터는 여기로 위임하고, 여기서 repository(DB) + LLM/RAG 를 조합한다.
지금은 더미를 반환하며, 실연결 지점은 TODO 로 표시한다.
"""

from app.company.schemas import CompanyReportOut


async def get_company_report(company_id: str) -> CompanyReportOut:
    """기업 분석 리포트 생성.

    실연결 단계: repository 로 DART·크롤러·뉴스 조회 → LLM/RAG 분석 → 조합.
    현재는 프론트 BFF 더미와 동일 형태의 자리표시 값을 반환한다.
    """
    # TODO: repository(DB) + LLM/RAG 연결로 교체
    return CompanyReportOut(company_id=company_id, company_name="(미구현)")
