"""앱 설정 — 환경변수를 한 곳에서 읽어 관리한다.

.env 파일이나 OS 환경변수에서 값을 읽어온다. 실제 값(.env)은 깃에 올리지
않고, .env.example만 공유한다. 새 환경변수가 생기면 여기에 필드를 추가한다.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
