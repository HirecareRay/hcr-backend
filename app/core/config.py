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

    # DB 연동 (값이 비어 있으면 해당 DB는 비활성 — 앱은 그대로 기동된다)
    mariadb_url: str = ""        # 예: mysql+pymysql://user:pw@host:3306/hcr
    mongodb_uri: str = ""        # 예: mongodb://host:27017
    mongodb_db_name: str = "hcr"  # 사용할 MongoDB 데이터베이스명

    # LLM 연동 단계에서 채울 예정
    # openai_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
