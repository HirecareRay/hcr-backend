"""인증 의존성 — 보호된 엔드포인트에서 현재 사용자를 꺼낸다.

라우터에서 Depends(get_current_user) 로 주입하면, Authorization: Bearer <token>
헤더를 검증해 User 를 돌려준다. 토큰이 없거나 유효하지 않으면 401.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth import repository
from app.auth.models import User
from app.auth.security import decode_access_token
from app.db.session import get_db

# Authorization: Bearer <token> 헤더를 파싱 (없으면 403/401)
_bearer = HTTPBearer(auto_error=True)

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="유효하지 않은 인증 정보입니다",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Bearer 토큰을 검증하고 해당 사용자를 반환한다."""
    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _credentials_error from exc

    user = repository.get_user_by_id(db, int(user_id))
    if user is None:
        raise _credentials_error
    return user
