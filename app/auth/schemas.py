"""인증 요청·응답 Pydantic 스키마.

요청(SignupIn·LoginIn): 프론트가 보내는 입력을 검증한다.
응답(AuthResponse·AuthUserOut): 프론트 계약 { token, user: { id, name, email } } 에
맞춰 CamelModel 로 내보낸다(단어 하나짜리 필드라 표기는 동일하지만 베이스는 통일).
"""

from pydantic import BaseModel, EmailStr, Field

from app.shared.schema import CamelModel


class SignupIn(BaseModel):
    """회원가입 요청 — 프론트 SignupFormValues 의 백엔드 계약."""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)  # bcrypt 72바이트 한계


class LoginIn(BaseModel):
    """로그인 요청 — 프론트 LoginFormValues 의 백엔드 계약."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class SocialLoginIn(BaseModel):
    """소셜 로그인 요청 — 프론트 콜백이 provider 인가코드를 넘긴다.

    code: provider 가 콜백으로 준 authorization code.
    state: CSRF 방어용 값. naver 토큰 교환에는 필수라 함께 받는다(kakao·google 은 무시).
    """

    code: str = Field(min_length=1, max_length=2048)
    state: str | None = Field(default=None, max_length=512)


class AuthUserOut(CamelModel):
    """응답에 실리는 사용자 정보 (비밀번호 제외)."""

    id: str
    name: str
    email: str


class AuthResponse(CamelModel):
    """로그인·회원가입 성공 응답 — 프론트 LoginResponse/SignupResponse 계약."""

    token: str
    user: AuthUserOut
