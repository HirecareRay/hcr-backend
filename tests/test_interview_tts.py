"""면접관 음성 합성(TTS) 테스트 — ElevenLabs 중계 클라이언트 + REST 엔드포인트.

클라이언트(app/interview/tts.py)는 실 네트워크·크레딧을 쓰지 않도록 _post 를 mock 해
빈입력·캐시·키누락 경로를 본다. 엔드포인트(POST /interviews/tts)는 인증(로그인 전용)·
비활성 폴백(404)·정상 합성·담당 목소리 매핑·외부 장애(502)를 TestClient 로 검증한다.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import DEFAULT_TTS_VOICES, resolve_persona_voice, settings
from app.interview import tts
from app.main import app

client = TestClient(app)


# ── 클라이언트(tts.synthesize) 단위 ───────────────────────────────
# async 함수는 asyncio.run 으로 실행해 pytest-asyncio 의존성을 피한다(레포 관례).


def test_synthesize_empty_text_skips_call(monkeypatch):
    """빈 텍스트면 API 호출 없이 빈 바이트(불필요한 과금 방지) — 키 없어도 예외 없음."""
    monkeypatch.setattr(settings, 'elevenlabs_api_key', '')
    called = False

    async def _fail(*_args, **_kwargs):
        nonlocal called
        called = True
        return b'x'

    monkeypatch.setattr(tts, '_post', _fail)
    assert asyncio.run(tts.synthesize('   ', 'v1')) == b''
    assert called is False


def test_synthesize_without_key_raises(monkeypatch):
    """키가 없으면 명확한 RuntimeError(내부 스택 비노출용 친화 메시지)."""
    monkeypatch.setattr(settings, 'elevenlabs_api_key', '')
    with pytest.raises(RuntimeError):
        asyncio.run(tts.synthesize('안녕하세요', 'v1'))


def test_synthesize_caches_same_input(monkeypatch):
    """같은 (voice·model·settings·text)는 캐시에서 돌려줘 재과금하지 않는다."""
    tts._cache.clear()
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'k')
    monkeypatch.setattr(settings, 'interview_tts_normalize', False)  # ffmpeg 없이 결정적
    calls = 0

    async def _post(text, voice_id, voice_settings=None):
        nonlocal calls
        calls += 1
        return b'audio-bytes'

    monkeypatch.setattr(tts, '_post', _post)
    first = asyncio.run(tts.synthesize('같은 질문', 'v1'))
    second = asyncio.run(tts.synthesize('같은 질문', 'v1'))
    assert first == second == b'audio-bytes'
    assert calls == 1  # 두 번째는 캐시 히트


def test_synthesize_settings_change_bypasses_cache(monkeypatch):
    """voice_settings(speed 등)가 다르면 옛 캐시가 아니라 새로 합성한다."""
    tts._cache.clear()
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'k')
    monkeypatch.setattr(settings, 'interview_tts_normalize', False)  # ffmpeg 없이 결정적
    calls = 0

    async def _post(text, voice_id, voice_settings=None):
        nonlocal calls
        calls += 1
        return b'audio-bytes'

    monkeypatch.setattr(tts, '_post', _post)
    asyncio.run(tts.synthesize('질문', 'v1', {'speed': 1.0}))
    asyncio.run(tts.synthesize('질문', 'v1', {'speed': 0.88}))
    assert calls == 2  # 속도가 달라 캐시 미스 → 재합성


def test_normalize_disabled_returns_original(monkeypatch):
    """정규화를 끄면(interview_tts_normalize=false) ffmpeg 없이 원본을 그대로 반환한다."""
    monkeypatch.setattr(settings, 'interview_tts_normalize', False)
    assert asyncio.run(tts._normalize(b'raw-audio')) == b'raw-audio'


def test_normalize_empty_returns_empty(monkeypatch):
    """빈 오디오는 정규화하지 않고 그대로 빈 바이트를 돌려준다(ffmpeg 호출 없음)."""
    monkeypatch.setattr(settings, 'interview_tts_normalize', True)
    assert asyncio.run(tts._normalize(b'')) == b''


def test_post_sends_voice_settings_in_payload(monkeypatch):
    """_post 는 voice_settings 를 payload 에 실어 ElevenLabs 로 전달한다(속도 적용 경로)."""
    monkeypatch.setattr(settings, 'elevenlabs_api_key', 'k')
    captured = {}

    class _Resp:
        content = b'audio'

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, params=None, headers=None, json=None):
            captured['url'] = url
            captured['json'] = json
            return _Resp()

    monkeypatch.setattr(tts.httpx, 'AsyncClient', _Client)
    out = asyncio.run(tts._post('질문', 'v9', {'speed': 0.88, 'stability': 0.5}))
    assert out == b'audio'
    assert captured['url'].endswith('/v9')
    assert captured['json']['voice_settings'] == {'speed': 0.88, 'stability': 0.5}
    assert captured['json']['model_id'] == settings.elevenlabs_model


# ── 엔드포인트(POST /interviews/tts) 통합 ─────────────────────────


def _auth(monkeypatch, user_id='u1'):
    monkeypatch.setattr(
        'app.interview.router.decode_access_token', lambda token: user_id
    )


def test_tts_requires_auth():
    """인증 헤더가 없으면 401 (과금 경로는 로그인 전용)."""
    res = client.post('/interviews/tts', json={'text': '안녕', 'personaId': 'tech_pressure'})
    assert res.status_code == 401


def test_tts_disabled_returns_404(monkeypatch):
    """비활성(interview_tts_enabled=false)이면 404 → 프론트 SpeechSynthesis 폴백."""
    _auth(monkeypatch)
    monkeypatch.setattr(settings, 'interview_tts_enabled', False)
    res = client.post(
        '/interviews/tts',
        json={'text': '안녕', 'personaId': 'tech_pressure'},
        headers={'Authorization': 'Bearer x'},
    )
    assert res.status_code == 404


def test_tts_returns_audio_and_maps_persona_voice(monkeypatch):
    """활성 시 담당 면접관 voice id·발화 설정으로 합성해 audio/mpeg 를 반환한다."""
    _auth(monkeypatch)
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)
    captured = {}

    async def _synth(text, voice_id, voice_settings=None):
        captured['text'] = text
        captured['voice_id'] = voice_id
        captured['voice_settings'] = voice_settings
        return b'mp3-bytes'

    monkeypatch.setattr('app.interview.router.tts.synthesize', _synth)
    res = client.post(
        '/interviews/tts',
        json={'text': '자기소개 부탁드립니다', 'personaId': 'tech_pressure'},
        headers={'Authorization': 'Bearer x'},
    )
    assert res.status_code == 200
    assert res.headers['content-type'] == 'audio/mpeg'
    assert res.content == b'mp3-bytes'
    tech = resolve_persona_voice('tech_pressure')
    assert captured['voice_id'] == tech.voice_id
    # 발화 속도(speed 등)가 API 로 전달되는지 — 기본은 1.0 보다 느리다.
    assert captured['voice_settings']['speed'] == tech.speed
    assert captured['voice_settings']['speed'] < 1.0


def test_tts_unknown_persona_falls_back(monkeypatch):
    """알 수 없는 persona_id 는 진행자(culture_fit) 목소리로 폴백한다(면접 안 끊김)."""
    _auth(monkeypatch)
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)
    captured = {}

    async def _synth(text, voice_id, voice_settings=None):
        captured['voice_id'] = voice_id
        return b'x'

    monkeypatch.setattr('app.interview.router.tts.synthesize', _synth)
    res = client.post(
        '/interviews/tts',
        json={'text': '질문', 'personaId': 'nonexistent'},
        headers={'Authorization': 'Bearer x'},
    )
    assert res.status_code == 200
    assert captured['voice_id'] == DEFAULT_TTS_VOICES['culture_fit'].voice_id


def test_tts_upstream_failure_returns_502(monkeypatch):
    """외부 합성 실패(RuntimeError)는 502 로 변환한다(내부 스택 비노출)."""
    _auth(monkeypatch)
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)

    async def _synth(text, voice_id, voice_settings=None):
        raise RuntimeError('boom')

    monkeypatch.setattr('app.interview.router.tts.synthesize', _synth)
    res = client.post(
        '/interviews/tts',
        json={'text': '질문', 'personaId': 'tech_pressure'},
        headers={'Authorization': 'Bearer x'},
    )
    assert res.status_code == 502


def test_tts_rejects_empty_text(monkeypatch):
    """빈 text 는 스키마 검증에서 422 로 막는다(min_length=1)."""
    _auth(monkeypatch)
    monkeypatch.setattr(settings, 'interview_tts_enabled', True)
    res = client.post(
        '/interviews/tts',
        json={'text': '', 'personaId': 'tech_pressure'},
        headers={'Authorization': 'Bearer x'},
    )
    assert res.status_code == 422
