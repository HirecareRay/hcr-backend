"""기업 데이터 접근 — MariaDB(정형 + RAG 벡터·원문)·MongoDB(문서) 쿼리만 담당.

라우터·서비스는 여기를 통해서만 DB에 접근한다.
- raw SQL 문자열 조합 금지, 파라미터 바인딩 사용
- DB 연동 단계에서 구현한다 (현재는 service 가 더미를 반환)

예시 시그니처:
    async def find_company_by_id(db, company_id: str) -> Company | None: ...
"""
