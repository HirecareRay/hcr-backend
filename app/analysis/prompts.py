"""적합도 분석 LLM 프롬프트 정의.

TODO: 아래 빈 문자열에 실제 시스템 프롬프트를 작성하세요.

입력 변수 (HUMAN_TEMPLATE 치환 변수):
  {job_text}      – 대상 채용 공고 텍스트 (직무명·주요업무·자격요건·우대사항)
  {docs_text}     – 지원자 서류 텍스트 (이력서·경력기술서·자기소개서)
  {rag_context}   – RAG 참고 컨텍스트 (아래 참고)

RAG 컨텍스트 구성 계획 (service.py _build_rag_context() 에서 조립):
  1. 동일 회사의 이전 채용 공고 (company_id 기준, job_postings 테이블)
  2. 유사 직무 채용 공고 (직무명·태그 유사도 기준, 임베딩 검색 예정)
  → 빈 문자열이면 프롬프트에서 해당 섹션을 생략하도록 작성 권장

출력 스키마: schemas.py FitResult 참조
"""

# 시스템 프롬프트
# ↓ 여기에 작성하세요
SYSTEM_PROMPT: str = ""

# 사용자 메시지 템플릿
# {rag_context} 가 비어있을 경우를 대비해 조건부 섹션으로 작성 권장
HUMAN_TEMPLATE: str = (
    "[채용 공고]\n{job_text}"
    "\n\n[참고 공고]\n{rag_context}"
    "\n\n[지원자 서류]\n{docs_text}"
)
