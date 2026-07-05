"""인증 데이터 접근 — users 테이블 쿼리만 담당.

service 는 여기를 통해서만 DB 에 접근한다. ORM 쿼리라 파라미터 바인딩이
자동으로 적용된다(raw SQL 문자열 조합 없음).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User


def get_user_by_email(db: Session, email: str) -> User | None:
    """이메일로 사용자 1명을 찾는다(없으면 None)."""
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """기본키로 사용자 1명을 찾는다(없으면 None)."""
    return db.get(User, user_id)


def create_user(db: Session, *, name: str, email: str, password_hash: str) -> User:
    """새 사용자를 저장하고 커밋한 뒤 반환한다."""
    user = User(name=name, email=email, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_provider(db: Session, provider: str, provider_id: str) -> User | None:
    """소셜 식별자(provider, provider_id)로 사용자 1명을 찾는다(없으면 None)."""
    return db.execute(
        select(User).where(
            User.provider == provider,
            User.provider_id == provider_id,
        )
    ).scalar_one_or_none()


def create_social_user(
    db: Session, *, name: str, email: str, provider: str, provider_id: str
) -> User:
    """소셜 사용자를 저장하고 커밋한 뒤 반환한다(비밀번호 없음 → password_hash=NULL)."""
    user = User(
        name=name,
        email=email,
        provider=provider,
        provider_id=provider_id,
        password_hash=None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
