"""인증 보안 유틸 — 비밀번호 해시와 JWT 토큰.

순수 함수 모음이라 DB 를 모른다. service 가 이 함수들을 조합해 쓴다.
시크릿·만료시간은 settings(.env) 에서 읽는다.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# bcrypt 는 72바이트까지만 처리한다. 한글 등 멀티바이트 비번이 한계를 넘으면
# 에러가 나므로, 해시·검증 양쪽에서 동일하게 72바이트로 자른다.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plain: str) -> bytes:
    """평문을 bcrypt 입력용 바이트로(72바이트 초과분은 절단)."""
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """평문 비밀번호를 bcrypt 해시로 바꾼다(저장용)."""
    return bcrypt.hashpw(_to_bcrypt_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """평문과 저장된 해시가 일치하는지 확인한다."""
    return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    """사용자 id 를 sub 로 담은 서명된 JWT 를 만든다.

    JWT_SECRET 이 비어 있으면 토큰을 서명할 수 없으므로 명확히 막는다.
    """
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET이 설정되지 않았습니다 (.env 확인)")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """JWT 를 검증하고 sub(사용자 id)를 돌려준다.

    만료·서명오류 등 검증 실패 시 jwt.PyJWTError 를 던진다(호출부가 401 처리).
    """
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET이 설정되지 않았습니다 (.env 확인)")

    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    sub = payload.get("sub")
    if not sub:
        raise jwt.InvalidTokenError("sub 클레임이 없습니다")
    return str(sub)
