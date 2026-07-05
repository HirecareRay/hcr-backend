"""인증 비즈니스 로직 — 회원가입·로그인.

라우터는 여기로 위임한다. 여기서 repository(DB) + security(해시·JWT) 를 조합해
프론트 계약(AuthResponse) 형태로 돌려준다. 실패는 HTTPException 으로 알린다.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.auth import oauth, repository
from app.auth.models import User
from app.auth.oauth import OAuthError, OAuthProfile, OAuthProfileIncomplete
from app.auth.schemas import AuthResponse, AuthUserOut, LoginIn, SignupIn, SocialLoginIn
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.config import resolve_oauth_provider


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
    # 소셜 전용 유저는 password_hash 가 NULL 이라 비번 로그인이 불가하다.
    if user is None or user.password_hash is None or not verify_password(
        data.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다",
        )

    return _to_response(user)


def login_social(db: Session, provider: str, data: SocialLoginIn) -> AuthResponse:
    """소셜 로그인 — provider 프로필을 조회해 find-or-create 후 토큰을 발급한다.

    provider 통신 실패는 502, 이메일 미동의 등 프로필 부족은 400, 미설정 provider 는
    503 으로 변환한다(내부 스택트레이스·토큰은 노출하지 않는다).
    """
    config = resolve_oauth_provider(provider)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="지원하지 않는 소셜 로그인입니다",
        )
    if not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="해당 소셜 로그인이 설정되지 않았습니다",
        )

    try:
        profile = oauth.fetch_profile(provider, config, data.code, data.state)
    except OAuthProfileIncomplete as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="소셜 로그인 처리 중 오류가 발생했습니다",
        ) from exc

    user = _find_or_create_social_user(db, profile)
    return _to_response(user)


def _find_or_create_social_user(db: Session, profile: OAuthProfile) -> User:
    """소셜 식별자로 기존 유저를 찾고, 없으면 새로 만든다.

    이미 같은 이메일이 다른 경로(이메일 가입·다른 provider)로 존재하면 자동 연결하지
    않고 409 로 막는다(이메일 기반 계정 탈취·중복 생성 방지).
    """
    existing = repository.get_user_by_provider(db, profile.provider, profile.provider_id)
    if existing is not None:
        return existing

    if repository.get_user_by_email(db, profile.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 다른 방법으로 가입된 이메일입니다",
        )

    return repository.create_social_user(
        db,
        name=profile.name,
        email=profile.email,
        provider=profile.provider,
        provider_id=profile.provider_id,
    )
