"""앱 설정 — 환경변수를 한 곳에서 읽어 관리한다.

.env 파일이나 OS 환경변수에서 값을 읽어온다. 실제 값(.env)은 깃에 올리지
않고, .env.example만 공유한다. 새 환경변수가 생기면 여기에 필드를 추가한다.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 앱 기본 정보
    appName: str = "HCR Backend"
    debug: bool = False

    # CORS 허용 출처 (프론트 개발 서버)
    frontendOrigin: str = "http://localhost:3000"

    # DB 연동
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
        validation_alias=AliasChoices("MONGODB_DATABASE", "MONGODBDATABASE"),
    )

    # LLM 연동 단계에서 채울 예정
    # openaiApiKey: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
