"""인증 비즈니스 로직 — 회원가입·로그인.

라우터는 여기로 위임한다. 여기서 repository(DB) + security(해시·JWT) 를 조합해
프론트 계약(AuthResponse) 형태로 돌려준다. 실패는 HTTPException 으로 알린다.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.auth import repository
from app.auth.models import User
from app.auth.schemas import AuthResponse, AuthUserOut, LoginIn, SignupIn
from app.auth.security import create_access_token, hash_password, verify_password


def _to_response(user: User) -> AuthResponse:
    """User ORM → 토큰 발급 + 프론트 계약 응답으로 변환."""
    token = create_access_token(str(user.id))
    return AuthResponse(
        token=token,
        user=AuthUserOut(id=str(user.id), name=user.name, email=user.email),
    )


def signup(db: Session, data: SignupIn) -> AuthResponse:
    """회원가입 — 이메일 중복을 막고 해시 저장 후 토큰을 발급한다."""
    if repository.get_user_by_email(db, data.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 가입된 이메일입니다",
        )

    user = repository.create_user(
        db,
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    return _to_response(user)


def login(db: Session, data: LoginIn) -> AuthResponse:
    """로그인 — 이메일·비밀번호를 검증하고 토큰을 발급한다.

    이메일이 없거나 비번이 틀려도 동일한 401 로 응답한다(계정 존재 여부 노출 방지).
    """
    user = repository.get_user_by_email(db, data.email)
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다",
        )

    return _to_response(user)
