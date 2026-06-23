"""모의 면접 라우터 — 음성(STT)/텍스트 답변 → 실시간 평가(SSE 스트리밍).

company/ 와 동일한 모듈 구조(router·service·repository·schemas·models)로 채운다.
SSE 스트리밍은 fastapi.responses.StreamingResponse 또는 sse-starlette 를 쓴다.
실연결 단계에서 구현한다.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/interviews", tags=["interview"])

# TODO: 면접 세션 시작·답변 제출·SSE 평가 스트리밍 엔드포인트 추가
