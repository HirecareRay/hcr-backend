"""인증 라우터 — HTTP 입출력·검증만. 로직은 service 로 위임한다.

프론트 BFF(app/api/auth/login·signup)가 호출하는 엔드포인트.
응답은 response_model_by_alias=True 로 camelCase(프론트 계약)로 내보낸다.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.deps import get_current_user
from app.auth.models import User
from app.auth.schemas import AuthResponse, AuthUserOut, LoginIn, SignupIn
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=AuthResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
def signup(data: SignupIn, db: Session = Depends(get_db)) -> AuthResponse:
    """회원가입 — 성공 시 토큰과 사용자 정보를 반환한다."""
    return service.signup(db, data)


@router.post(
    "/login",
    response_model=AuthResponse,
    response_model_by_alias=True,
)
def login(data: LoginIn, db: Session = Depends(get_db)) -> AuthResponse:
    """로그인 — 성공 시 토큰과 사용자 정보를 반환한다."""
    return service.login(db, data)


@router.get(
    "/me",
    response_model=AuthUserOut,
    response_model_by_alias=True,
)
def read_me(current_user: User = Depends(get_current_user)) -> AuthUserOut:
    """현재 로그인한 사용자 정보 — 토큰 검증용."""
    return AuthUserOut(
        id=str(current_user.id),
        name=current_user.name,
        email=current_user.email,
    )
