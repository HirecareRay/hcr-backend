"""기업 분석 도메인의 요청·응답 스키마 (Pydantic).

응답 스키마는 CamelModel 을 상속해 snake_case → camelCase 변환을 자동화한다.
필드는 프론트 features/company/types/ 의 응답 타입을 스펙으로 삼아 채운다.
"""

from app.shared.schema import CamelModel


class CompanyReportOut(CamelModel):
    """기업 분석 리포트 응답.

    프론트 BFF(app/api/companies/[companyId]/report)의 더미 계약과
    동일한 형태로 맞춘다. 실데이터 연결 시 필드를 확장한다.
    """

    company_id: str
    company_name: str
    # TODO: 프론트 리포트 타입(재무·문화·성장·예상질문)에 맞춰 필드 추가
