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

    # 면접 WS 입장 티켓 TTL(초) — 브라우저 WS 는 헤더를 못 붙이므로 JWT 대신 단기·
    # 1회용 티켓을 쿼리로 받는다. 짧을수록 URL 노출 위험이 줄지만 너무 짧으면 발급↔
    # 연결 사이 지연에 걸린다. 10~300 으로 제한(기본 60).
    interview_ws_ticket_ttl_seconds: int = Field(60, ge=10, le=300)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
