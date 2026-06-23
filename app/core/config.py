"""앱 설정 — 환경변수를 한 곳에서 읽어 관리한다.

.env 파일이나 OS 환경변수에서 값을 읽어온다. 실제 값(.env)은 깃에 올리지
않고, .env.example만 공유한다. 새 환경변수가 생기면 여기에 필드를 추가한다.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 앱 기본 정보
    app_name: str = "HCR Backend"
    debug: bool = False

    # CORS 허용 출처 (프론트 개발 서버)
    frontend_origin: str = "http://localhost:3000"

    # DB 연동 단계에서 채울 예정
    # mariadb_url: str = ""
    # mongodb_uri: str = ""

    # LLM 연동 단계에서 채울 예정
    # openai_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
