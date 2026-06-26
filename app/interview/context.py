"""면접 컨텍스트 공급자 — 회사 정보를 LLM 면접관에 주입하는 유일한 경계.

지금은 mock 문자열을 반환한다. 기업 분석 RAG/MariaDB 가 준비되면
``get_company_context`` 한 함수만 교체하면 된다 — 프롬프트·스키마·상태머신은
전부 그대로다(연결 지점 단일화).

⚠️ DB 미연동 단계라 회사 무관 mock 을 쓴다. 실데이터 교체 지점은 TODO 로 표시.
"""

# LLM 면접관에 주입할 회사 컨텍스트(mock). 실제로는 기업 분석 리포트·RAG 원문이
# 들어온다. 데모에서도 "회사 분석이 면접관 컨텍스트로 주입된다"는 흐름을 보여준다.
MOCK_COMPANY_CONTEXT = """\
회사명: (주)하이어케어레이(HireCareRay)
업종: AI 기반 취업 준비 SaaS — 기업 분석 + 모의 면접 원스톱 서비스
인재상: 스스로 문제를 정의하고, 데이터로 의사결정하며, 협업으로 끝까지 완성하는 사람
주요 기술: Python/FastAPI, Next.js, LangChain·RAG, 실시간 멀티모달(STT·MediaPipe)
지원 직무: 백엔드 엔지니어 (AI 파이프라인)
핵심 역량: 비동기 처리, LLM 연동, 실시간 스트리밍 설계, 테스트 주도 개발
"""

# LLM 질문 생성이 실패해도 면접이 끊기지 않도록 쓰는 안전 기본 질문.
FALLBACK_MAIN_QUESTIONS: tuple[str, ...] = (
    '간단히 자기소개 부탁드립니다.',
    '이 직무에 지원하신 동기는 무엇인가요?',
    '가장 자신 있는 기술과 그것을 활용한 경험을 말씀해 주세요.',
    '마지막으로 하고 싶은 말씀이 있나요?',
)


async def get_company_context(company_id: str | None = None) -> str:
    """면접관에 주입할 회사 컨텍스트를 반환한다.

    현재는 회사 무관 mock 을 돌려준다. async 시그니처는 추후 DB·RAG 조회로
    교체할 때 호출부를 바꾸지 않도록 미리 맞춰 둔 것이다.

    TODO(RAG): company_id 로 MariaDB(기업 분석 리포트)·RAG 원문을 조회해
    실제 컨텍스트 문자열로 교체한다. company_id 는 추후 WS 초기화 시 주입한다.
    """
    return MOCK_COMPANY_CONTEXT
