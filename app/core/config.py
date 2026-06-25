"""앱 설정 — 환경변수를 한 곳에서 읽어 관리한다.

.env 파일이나 OS 환경변수에서 값을 읽어온다. 실제 값(.env)은 깃에 올리지
않고, .env.example만 공유한다. 새 환경변수가 생기면 여기에 필드를 추가한다.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 앱 기본 정보
    app_name: str = "HCR Backend"
    debug: bool = False

    # CORS 허용 출처 (프론트 개발 서버)
    frontend_origin: str = "http://localhost:3000"

    # DB 연동 (값이 비어 있으면 해당 DB는 비활성 — 앱은 그대로 기동된다)
    mariadb_url: str = ""        # 예: mysql+pymysql://user:pw@host:3306/hcr
    mongodb_uri: str = ""        # 예: mongodb://host:27017
    mongodb_db_name: str = "hcr"  # 사용할 MongoDB 데이터베이스명

    # 인증(JWT) — 시크릿은 .env 에서만 채운다(코드·example 에 박지 않음)
    jwt_secret: str = ""               # 토큰 서명 키 (반드시 .env 에 설정)
    jwt_algorithm: str = "HS256"       # 서명 알고리즘
    jwt_expire_minutes: int = 60 * 24  # 액세스 토큰 만료(분) — 기본 1일

    # LLM·STT 연동 — 시크릿은 .env 에서만 채운다(코드·example 에 박지 않음)
    openai_api_key: str = ""

    # 면접 — 한 세션에서 LLM 이 생성할 메인 질문 수(꼬리질문은 별도). 비용·길이 상한.
    # 0·음수면 첫 질문 송신이 깨지고, 과도하면 토큰 비용이 급증하므로 1~10 으로 제한한다.
    interview_main_question_count: int = Field(4, ge=1, le=10)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
