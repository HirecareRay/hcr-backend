"""앱 설정 — 환경변수를 한 곳에서 읽어 관리한다.

.env 파일이나 OS 환경변수에서 값을 읽어온다. 실제 값(.env)은 깃에 올리지
않고, .env.example만 공유한다. 새 환경변수가 생기면 여기에 필드를 추가한다.
"""

from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersonaVoice(BaseModel):
    """한 면접관 페르소나의 ElevenLabs 목소리·발화 설정.

    voice_id 와 voice_settings 를 코드가 아니라 설정에서 읽어, 코드 수정 없이
    .env(INTERVIEW_TTS_VOICES JSON)로 목소리·속도를 튜닝할 수 있게 한다.
    speed 는 0.7~1.2(1.0=기본, 낮을수록 느림), 나머지 값은 0~1 범위.
    """

    voice_id: str
    stability: float = Field(0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(0.75, ge=0.0, le=1.0)
    style: float = Field(0.0, ge=0.0, le=1.0)
    use_speaker_boost: bool = True
    speed: float = Field(0.88, ge=0.7, le=1.2)

    def as_voice_settings(self) -> dict[str, float | bool]:
        """ElevenLabs API payload 의 voice_settings 로 직렬화(snake_case 그대로 전송)."""
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "use_speaker_boost": self.use_speaker_boost,
            "speed": self.speed,
        }


# 면접관 3인의 기본 목소리 — 코드 기본값이며, .env INTERVIEW_TTS_VOICES(JSON)로 통째
# override 할 수 있다(코드 수정 없이 튜닝). 남성은 tech_pressure 1명, 나머지는 여성.
# 다국어 모델(eleven_flash_v2_5)이라 한국어도 이 id 로 발화된다. speed=0.88 로 기본
# 보다 느리게 발화한다(voice_id 는 비밀값이 아니라 코드에 두어도 안전 — 시크릿은 API 키뿐).
DEFAULT_TTS_VOICES: dict[str, PersonaVoice] = {
    # 인사담당자 — 따뜻·친근한 여성(Sarah).
    "culture_fit": PersonaVoice(voice_id="EXAVITQu4vr4xnSDxMaL"),
    # 기술담당자 — 낮고 단단한 한국어 남성(Midnight Cave). 패널 유일 남성, 압박감 톤.
    "tech_pressure": PersonaVoice(voice_id="aQzFKIjVemqRAhfd9est"),
    # 실무담당자 — 차분·중립적인 한국어 여성(Hanna).
    "practical": PersonaVoice(voice_id="zgDzx5jLLCqEp6Fl7Kl7"),
}


class Settings(BaseSettings):
    # 앱 기본 정보
    app_name: str = "HCR Backend"
    debug: bool = False
    env: str = "dev"
    # CORS 허용 출처 (프론트 개발 서버)
    frontend_origin: str = "http://localhost:3000"

    # DB 연동 (값이 비어 있으면 해당 DB는 비활성 — 앱은 그대로 기동된다)
    # develop 인프라(db/session.py·db/mongo.py·auth)가 쓰는 snake_case 필드
    mariadb_url: str = ""        # 예: mysql+pymysql://user:pw@host:3306/hcr
    mongodb_uri: str = ""        # 예: mongodb://host:27017
    mongodb_db_name: str = "hcr"  # 사용할 MongoDB 데이터베이스명

    # report.py / core(core/mariadb.py·core/mongo.py)가 쓰는 camelCase 필드.
    # 같은 환경변수(MARIADB_URL·MONGODB_URI)를 읽어 develop 필드와 값이 일치한다.
    mariadbUrl: str = Field(
        default="",
        validation_alias=AliasChoices("MARIADB_URL", "MARIADBURL"),
    )
    mongodbUri: str = Field(
        default="",
        validation_alias=AliasChoices("MONGODB_URI", "MONGODBURI", "MONGO_URI"),
    )
    mongodbDatabase: str = Field(
        default="hcr",
        validation_alias=AliasChoices("MONGODB_DATABASE", "MONGODBDATABASE", "MONGODB_DB_NAME"),
    )

    # 인증(JWT) — 시크릿은 .env 에서만 채운다(코드·example 에 박지 않음)
    jwt_secret: str = ""               # 토큰 서명 키 (반드시 .env 에 설정)
    jwt_algorithm: str = "HS256"       # 서명 알고리즘
    jwt_expire_minutes: int = 60 * 24  # 액세스 토큰 만료(분) — 기본 1일

    # LLM·STT 연동 — 시크릿은 .env 에서만 채운다(코드·example 에 박지 않음)
    openai_api_key: str = ""

    # TTS(면접관 음성) — ElevenLabs 백엔드 중계. 키는 .env 에서만 채운다(코드·example
    # 에 박지 않음). interview_tts_enabled 가 false(기본)면 엔드포인트를 비활성화해
    # 프론트 브라우저 SpeechSynthesis(무료)로 폴백한다 — 켜야 과금 경로가 열린다.
    # 모델은 저지연·저가 flash 로 고정(바꾸면 지연·비용 변동). 한 요청 텍스트 상한으로
    # 거대 입력 과금을 막는다.
    elevenlabs_api_key: str = ""
    interview_tts_enabled: bool = False
    elevenlabs_model: str = "eleven_flash_v2_5"
    interview_tts_max_chars: int = Field(600, ge=1, le=5000)
    # 페르소나별 목소리·발화 설정 — 코드 기본값(DEFAULT_TTS_VOICES)을 쓰되, .env 의
    # INTERVIEW_TTS_VOICES 에 JSON 을 넣으면 통째로 override 된다(코드 수정 없이 튜닝).
    # 예: INTERVIEW_TTS_VOICES='{"tech_pressure":{"voice_id":"...","speed":0.9}}'
    interview_tts_voices: dict[str, PersonaVoice] = Field(
        default_factory=lambda: dict(DEFAULT_TTS_VOICES)
    )
    # 볼륨 정규화 — ElevenLabs 는 목소리마다 원본 음량이 달라(voice_settings 에 gain 없음)
    # 담당별 크기가 제각각이다. 합성 후 ffmpeg loudnorm 으로 목표 음량에 맞춰 균일화한다.
    # ⚠️ 배포 서버에 ffmpeg 이 있어야 동작하며, 없거나 실패하면 원본을 그대로 반환한다
    # (정규화는 부가기능 — 음성 자체를 막지 않는다). target_lufs 는 EBU R128 목표 음량
    # (낮을수록 조용, 방송 기준 -16 근처 권장, -30~-9 로 제한).
    interview_tts_normalize: bool = True
    interview_tts_target_lufs: float = Field(-16.0, ge=-30.0, le=-9.0)

    # 면접 — 한 세션에서 LLM 이 생성할 메인 질문 수(꼬리질문은 별도). 비용·길이 상한.
    # 0·음수면 첫 질문 송신이 깨지고, 과도하면 토큰 비용이 급증하므로 1~10 으로 제한한다.
    interview_main_question_count: int = Field(4, ge=1, le=10)

    # 면접 자막 — True 면 오디오 청크마다 더미 부분 자막을 흘려 실시간 자막 UX 를
    # OpenAI 호출·키 없이 시연한다(Phase 1 워킹 스켈레톤). False(기본)면 answer_end
    # 에 누적 오디오를 gpt-4o-mini-transcribe 로 한 번에 전사하는 실 STT 경로를 쓴다.
    interview_dummy_transcript: bool = False

    # 면접 실시간 부분 자막 — True 면 답변 중 누적 오디오를 일정 간격으로 재전사해
    # 부분 자막(transcript_delta isFinal=False)을 흘려 '말하면서 자막이 차오르는'
    # UX 를 만든다. False(기본)면 answer_end 에 한 번만 전사한다(배치).
    # ⚠️ 비용: 켜면 누적 버퍼를 반복 전사하므로 OpenAI 호출이 늘어 과금이 커진다
    # (강사님 키 주의). 더미 모드(interview_dummy_transcript)가 켜져 있으면 그쪽이
    # 우선이라 이 설정은 무시된다.
    interview_partial_transcript: bool = False
    # 부분 자막 재전사 간격(오디오 청크 N개마다 1회). 작을수록 자막이 자주 갱신되지만
    # 비용↑. 프론트 MediaRecorder timeslice 에 맞춰 튜닝한다. 1~50 으로 제한.
    interview_partial_transcript_every: int = Field(8, ge=1, le=50)

    # 면접 결과 비언어·음성 모달의 최소 표본 — 이만큼 실제 신호가 쌓여야 점수를 낸다.
    # 카메라·마이크가 잠깐만 켜져 1~2 프레임만 잡힌 경우를 '데이터 부족(빈 모달)'으로
    # 처리해, 근거 없는 자신만만한 점수(예: 1프레임으로 시선 만점)를 막는다. 0 이면
    # 사실상 비활성(1개만 있어도 점수 — 하한 1). 표정은 ~1s 주기라 5≈5초, 음성 3≈3초.
    interview_min_expression_frames: int = Field(5, ge=0, le=100)
    interview_min_voice_frames: int = Field(3, ge=0, le=100)

    # 면접 WS 입장 티켓 TTL(초) — 브라우저 WS 는 헤더를 못 붙이므로 JWT 대신 단기·
    # 1회용 티켓을 쿼리로 받는다. 짧을수록 URL 노출 위험이 줄지만 너무 짧으면 발급↔
    # 연결 사이 지연에 걸린다. 10~300 으로 제한(기본 60).
    interview_ws_ticket_ttl_seconds: int = Field(60, ge=10, le=300)

    # 면접 WS 보안 — CSWSH(Cross-Site WebSocket Hijacking) 방어용 허용 Origin 목록.
    # 브라우저는 WS 핸드셰이크에 Origin 헤더를 위조 불가로 자동으로 싣지만, WS 엔
    # CORS 가 적용되지 않으므로 서버가 직접 출처를 본다(파싱·판정은 ws_origin.py).
    # 콤마 구분 다중 도메인 예: "https://hcr.example.com,https://www.hcr.example.com"
    # ⚠️ 운영에선 반드시 .env 로 도메인을 채운다 — 비우면 개발 모드(로컬 프론트만 허용)다.
    interview_allowed_origins: str = ""

    # ── 인기기업 순위(랭킹/trending) ──────────────────────────────────────
    # 리포트 조회수를 회사·날짜별로 누적해 최근 N 일 합산으로 순위를 낸다.
    # 윈도우(일): '오늘만'(=1)이면 자정에 비고 새벽엔 표본이 적어 순위가 출렁이므로
    # 기본 7일 롤링으로 안정화한다(1~30). 노출 개수는 기본 5개(1~20).
    ranking_trending_window_days: int = Field(7, ge=1, le=30)
    ranking_trending_default_limit: int = Field(5, ge=1, le=20)
    # 로고 베이스 — website_url 도메인으로 Google favicon URL(f"{base}?domain={d}&sz={n}")을 만든다.
    # Clearbit(구 기본값)는 2024 HubSpot 인수 후 무료 로고 API 가 폐지돼 DNS 자체가 죽어 교체.
    # Google s2/favicons 는 favicon 기반이라 브랜드 로고 DB 와 달리 중소기업 커버리지가 높고,
    # 없으면 404 라 프론트 onError 폴백(이니셜 원)이 깔끔하다.
    # 빈 값이면 자동 로고 산출을 끈다(큐레이션 logo_url 만 사용 — 외부 의존 차단 스위치).
    ranking_logo_cdn_base: str = "https://www.google.com/s2/favicons"
    # favicon 요청 크기(px) — 프론트 로고 원에 맞춰 업스케일한다(16~256).
    ranking_logo_size: int = Field(128, ge=16, le=256)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()


def resolve_persona_voice(persona_id: str) -> PersonaVoice:
    """persona_id 에 맞는 목소리 설정을 돌려준다(모르면 진행자 culture_fit 로 폴백).

    빈 문자열·미지의 id 는 진행자(culture_fit)로 폴백한다(TTS 계약). env(JSON)로 일부
    페르소나만 넣으면 나머지는 코드 기본값(DEFAULT_TTS_VOICES)으로 폴백한다 — pydantic
    은 dict 를 병합하지 않고 통째 교체하므로, 개별 페르소나 단위로 기본값을 보충한다.
    """
    voices = settings.interview_tts_voices
    return (
        voices.get(persona_id)
        or DEFAULT_TTS_VOICES.get(persona_id)
        or voices.get("culture_fit")
        or DEFAULT_TTS_VOICES["culture_fit"]
    )
