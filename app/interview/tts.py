"""ElevenLabs TTS 호출 캡슐화 — 면접관 질문 텍스트를 음성(mp3)으로 합성한다.

라우터·service 는 TTS 세부(HTTP 클라이언트·엔드포인트·헤더)를 모른다. stt.py(OpenAI
STT)·llm.py(OpenAI Chat)와 대칭인 유일한 ElevenLabs 경계이므로, 테스트는 여기(_post)
를 mock 해 실 API 를 호출하지 않는다(사용자 크레딧 보호).

⚠️ 비용 주의: 크레딧 과금이라 빈 입력은 호출조차 하지 않고, 같은 (voice·model·text)
결과는 인메모리 캐시로 재사용해 동일 질문 재생·재시도의 재과금을 막는다. 외부 API 장애는
내부 스택을 노출하지 않고 RuntimeError 로 변환한다. 서버는 중계만 한다(자원≈0, STT 와 동일).
"""

import asyncio
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
# 볼륨 정규화(ffmpeg) 타임아웃(초) — 질문 한 문장은 순식간이지만 프로세스가 멈춰도 안 걸리게.
_NORMALIZE_TIMEOUT_SECONDS = 20.0

# 동일 (voice·model·settings·text) 합성 결과 캐시 — 같은 질문의 재생·재시도가 재과금되지
# 않게 한다. voice_settings(speed 등)도 키에 넣어, 설정을 바꾸면 옛 캐시(예: 빠른 음성)를
# 돌려주지 않고 새로 합성한다. 질문 텍스트는 짧아 메모리 부담이 작고, LRU 로 상한을 둔다.
_CACHE_MAX = 256
# (voice_id, model, voice_settings 서명, 정규화 서명, text) — 정규화 on/off·목표 음량이
# 바뀌면 옛 캐시를 재사용하지 않도록 정규화 상태도 키에 포함한다.
_CacheKey = tuple[str, str, tuple[tuple[str, object], ...], tuple[bool, float], str]
_cache: OrderedDict[_CacheKey, bytes] = OrderedDict()


async def synthesize(
    text: str, voice_id: str, voice_settings: dict[str, object] | None = None
) -> bytes:
    """질문 텍스트를 지정 목소리(voice_id)로 합성한 mp3 바이트를 반환한다.

    빈 텍스트면 API 호출 없이 빈 바이트를 돌려준다(불필요한 과금 방지). 텍스트는 설정
    상한(interview_tts_max_chars)으로 잘라 거대 입력 과금을 막는다. voice_settings(안정도·
    속도 등)를 주면 그대로 API 에 전달한다(발화 속도 조정 등). 같은 입력은 캐시에서
    돌려준다. 외부 API 장애는 RuntimeError 로 변환한다(내부 스택 비노출).
    """
    trimmed = text.strip()[: settings.interview_tts_max_chars]
    if not trimmed:
        return b''
    if not settings.elevenlabs_api_key:
        raise RuntimeError('ELEVENLABS_API_KEY 가 설정되지 않았습니다 (.env 확인)')

    settings_key = tuple(sorted((voice_settings or {}).items()))
    norm_key = (settings.interview_tts_normalize, settings.interview_tts_target_lufs)
    key: _CacheKey = (voice_id, settings.elevenlabs_model, settings_key, norm_key, trimmed)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)  # LRU 갱신
        return cached

    audio = await _post(trimmed, voice_id, voice_settings)
    audio = await _normalize(audio)
    _remember(key, audio)
    return audio


async def _post(
    text: str, voice_id: str, voice_settings: dict[str, object] | None = None
) -> bytes:
    """ElevenLabs TTS API 를 호출해 오디오 바이트를 받는다(장애는 RuntimeError 로 변환).

    voice_settings 가 있으면 payload 에 실어 목소리 특성(속도·안정도 등)을 조정한다.
    테스트는 이 함수를 mock 해 실 네트워크·크레딧을 쓰지 않는다.
    """
    url = f'{_API_BASE}/{voice_id}'
    payload: dict[str, object] = {'text': text, 'model_id': settings.elevenlabs_model}
    if voice_settings:
        payload['voice_settings'] = voice_settings
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
                json=payload,
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


async def _normalize(audio: bytes) -> bytes:
    """합성된 mp3 를 목표 음량으로 정규화한다(목소리별 음량 편차 제거).

    ElevenLabs 는 목소리마다 원본 음량이 달라 voice_settings 로는 못 맞춘다(gain 없음).
    ffmpeg loudnorm(EBU R128)으로 전 목소리를 같은 음량으로 맞춘다. ffmpeg 이 없거나
    실패·타임아웃이면 원본을 그대로 돌려준다 — 정규화는 부가기능이라 음성 자체를 막지
    않는다(데모 보호). 설정으로 끄면(interview_tts_normalize=false) 바로 원본을 반환한다.
    """
    if not audio or not settings.interview_tts_normalize:
        return audio

    loudnorm = f'loudnorm=I={settings.interview_tts_target_lufs}:TP=-1.5:LRA=11'
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', 'pipe:0', '-af', loudnorm, '-f', 'mp3', '-b:a', '128k', 'pipe:1',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as error:
        # ffmpeg 미설치 등 — 정규화만 건너뛰고 원본 반환(음성은 정상 재생).
        logger.warning('TTS 음량 정규화 건너뜀 — ffmpeg 실행 불가(%s)', type(error).__name__)
        return audio

    try:
        out, _err = await asyncio.wait_for(
            proc.communicate(input=audio), timeout=_NORMALIZE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning('TTS 음량 정규화 타임아웃 — 원본 반환')
        return audio

    if proc.returncode != 0 or not out:
        logger.warning('TTS 음량 정규화 실패 — 원본 반환 (rc=%s)', proc.returncode)
        return audio
    return out


def _remember(key: _CacheKey, audio: bytes) -> None:
    """합성 결과를 LRU 캐시에 넣고 상한을 넘으면 가장 오래된 것부터 버린다."""
    _cache[key] = audio
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
