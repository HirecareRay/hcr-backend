"""OpenAI STT 호출 캡슐화 — 면접 답변 오디오를 텍스트로 전사한다.

라우터·service 는 STT 세부(클라이언트 생성·모델·파일 포장)를 모른다. 이 모듈이
유일한 OpenAI 경계이므로, 테스트는 여기를 mock 해 실 API 를 호출하지 않는다.

⚠️ 비용 주의: OPENAI_API_KEY 는 강사님 대여분이다. 빈 입력은 호출조차 하지 않는다.
모델은 whisper-1 로 고정한다 — 이 키 프로젝트는 gpt-4o-mini-transcribe·realtime
전사 모델 접근 권한이 없고(403 model_not_found), 접근 가능한 전사 모델이
whisper-1 뿐이라 확인됨. whisper-1 은 $0.006/분(완성 파일 ≤25MB, 스트리밍 불가).
실시간 부분결과(Realtime API)는 키 권한이 풀리면 Phase 2.5 에서 교체한다.
"""

import io
import logging

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# 이 키가 접근 가능한 유일한 전사 모델(상단 주석 참고). 변경은 키 권한 확인 후.
_STT_MODEL = 'whisper-1'
# 누적 webm/opus 컨테이너. 파일명 확장자로 OpenAI 에 포맷을 알린다.
_AUDIO_FILENAME = 'answer.webm'

# AsyncOpenAI 클라이언트는 지연 생성한다(import 시 키를 요구하지 않도록 — 테스트
# 는 _get_client 를 mock 하므로 실 키 없이도 import·실행된다).
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트를 지연 생성·재사용한다(키 없으면 명확히 실패)."""
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError('OPENAI_API_KEY 가 설정되지 않았습니다 (.env 확인)')
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def transcribe_audio(audio: bytes) -> str:
    """누적 오디오(webm/opus)를 전사해 텍스트를 반환한다.

    빈 입력이면 API 호출 없이 빈 문자열을 돌려준다(불필요한 과금 방지).
    외부 API 장애는 내부 스택을 노출하지 않고 RuntimeError 로 변환한다.
    """
    if not audio:
        return ''
    try:
        file = io.BytesIO(audio)
        file.name = _AUDIO_FILENAME  # OpenAI SDK 는 파일명으로 포맷을 추론한다
        result = await _get_client().audio.transcriptions.create(
            model=_STT_MODEL,
            file=file,
        )
        return result.text.strip()
    except Exception as error:  # noqa: BLE001 - 외부 API 장애를 친화 메시지로 변환
        logger.error('STT 전사 실패: %s', error)
        raise RuntimeError('음성 전사에 실패했습니다') from error
