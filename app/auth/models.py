"""인증 도메인의 DB 모델 (SQLAlchemy ORM).

회원 한 명을 나타내는 users 테이블. 비밀번호는 평문이 아니라 해시만 저장한다.
app/db/session.py 의 Base 를 상속한다.

소셜 로그인 유저는 비밀번호가 없어 password_hash 가 NULL 이고, 대신 provider
(kakao·google·naver)와 provider_id(해당 provider 안의 유저 식별자)로 식별한다.
(provider, provider_id) 조합은 유일해야 한다.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"
    # 같은 provider 안에서 provider_id 는 유일 — 소셜 유저 중복 생성을 DB 차원에서 막는다.
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_users_provider_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # 소셜 유저는 비밀번호가 없으므로 nullable. 이메일 가입 유저만 값을 가진다.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 가입 경로. 이메일 가입 유저는 NULL, 소셜 유저는 'kakao'·'google'·'naver'.
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # provider 안에서의 유저 고유 id(문자열로 통일 — 카카오는 숫자, 네이버는 문자열).
    provider_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
