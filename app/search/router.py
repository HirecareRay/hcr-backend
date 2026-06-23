"""기업 검색 라우터 — 기업명 검색·자동완성.

company/ 와 동일한 모듈 구조로 채운다. 프론트 features/search 의
응답 타입을 스펙으로 삼는다. 실연결 단계에서 구현한다.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/search", tags=["search"])

# TODO: 기업 검색·자동완성 엔드포인트 추가
