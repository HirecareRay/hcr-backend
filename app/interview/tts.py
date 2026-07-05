"""ElevenLabs TTS 호출 캡슐화 — 면접관 질문 텍스트를 음성(mp3)으로 합성한다.

라우터·service 는 TTS 세부(HTTP 클라이언트·엔드포인트·헤더)를 모른다. stt.py(OpenAI
STT)·llm.py(OpenAI Chat)와 대칭인 유일한 ElevenLabs 경계이므로, 테스트는 여기(_post)
를 mock 해 실 API 를 호출하지 않는다(사용자 크레딧 보호).

⚠️ 비용 주의: 크레딧 과금이라 빈 입력은 호출조차 하지 않고, 같은 (voice·model·text)
결과는 인메모리 캐시로 재사용해 동일 질문 재생·재시도의 재과금을 막는다. 외부 API 장애는
내부 스택을 노출하지 않고 RuntimeError 로 변환한다. 서버는 중계만 한다(자원≈0, STT 와 동일).
"""

import logging
from collections import OrderedDict

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_API_BASE = 'https://api.elevenlabs.io/v1/text-to-speech'
# mp3 44.1kHz/128kbps — 브라우저 재생 호환성이 넓고 질문 한 문장 용량이 작다.
_OUTPUT_FORMAT = 'mp3_44100_128'
# 외부 호출 타임아웃(초) — 질문 한 문장 합성은 짧지만, 네트워크 지연에 걸려 무한 대기하지 않게.
_TIMEOUT_SECONDS = 30.0

# 동일 (voice·model·text) 합성 결과 캐시 — 같은 질문의 재생·재시도가 재과금되지 않게 한다.
# 질문 텍스트는 짧아 메모리 부담이 작다. LRU 로 상한을 둬 장수 세션에서 무한 증식을 막는다.
_CACHE_MAX = 256
_cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()


async def synthesize(text: str, voice_id: str) -> bytes:
    """질문 텍스트를 지정 목소리(voice_id)로 합성한 mp3 바이트를 반환한다.

    빈 텍스트면 API 호출 없이 빈 바이트를 돌려준다(불필요한 과금 방지). 텍스트는 설정
    상한(interview_tts_max_chars)으로 잘라 거대 입력 과금을 막는다. 같은 입력은 캐시에서
    돌려준다. 외부 API 장애는 RuntimeError 로 변환한다(내부 스택 비노출).
    """
    trimmed = text.strip()[: settings.interview_tts_max_chars]
    if not trimmed:
        return b''
    if not settings.elevenlabs_api_key:
        raise RuntimeError('ELEVENLABS_API_KEY 가 설정되지 않았습니다 (.env 확인)')

    key = (voice_id, settings.elevenlabs_model, trimmed)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)  # LRU 갱신
        return cached

    audio = await _post(trimmed, voice_id)
    _remember(key, audio)
    return audio


async def _post(text: str, voice_id: str) -> bytes:
    """ElevenLabs TTS API 를 호출해 오디오 바이트를 받는다(장애는 RuntimeError 로 변환).

    테스트는 이 함수를 mock 해 실 네트워크·크레딧을 쓰지 않는다.
    """
    url = f'{_API_BASE}/{voice_id}'
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                params={'output_format': _OUTPUT_FORMAT},
                headers={
                    'xi-api-key': settings.elevenlabs_api_key,
                    'accept': 'audio/mpeg',
                    'content-type': 'application/json',
                },
                json={'text': text, 'model_id': settings.elevenlabs_model},
            )
            response.raise_for_status()
            return response.content
    except httpx.HTTPStatusError as error:
        # 상태 코드만 로깅하고 응답 본문·키는 남기지 않는다(민감정보·크레딧 정보 비노출).
        logger.error('TTS 합성 실패 — status=%s', error.response.status_code)
        raise RuntimeError('면접관 음성 합성에 실패했습니다') from error
    except httpx.HTTPError as error:
        logger.error('TTS 합성 실패 — %s', type(error).__name__)
        raise RuntimeError('면접관 음성 합성에 실패했습니다') from error


def _remember(key: tuple[str, str, str], audio: bytes) -> None:
    """합성 결과를 LRU 캐시에 넣고 상한을 넘으면 가장 오래된 것부터 버린다."""
    _cache[key] = audio
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
