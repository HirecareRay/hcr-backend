"""소셜 로그인 provider 어댑터 — code→token 교환 + 프로필 조회.

provider(kakao·google·naver)마다 엔드포인트·응답 스키마만 다르고 흐름은 같다:
  authorization code → access token → 사용자 프로필(email·name·provider_id).
router·service 는 provider 세부를 몰라도 되게, 여기서 통일된 OAuthProfile 로 돌려준다.

시크릿(client_secret)은 config(.env)에서만 읽고, 실패는 OAuthError 로 알린다
(스택트레이스·토큰 평문을 응답·로그에 노출하지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import OAuthProviderConfig

# provider 통신 전체 타임아웃(초) — 무한 대기 대신 명확한 실패로 떨어지게 한다.
_TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class OAuthProfile:
    """provider 무관하게 통일된 최소 프로필. service 의 find-or-create 입력."""

    provider: str
    provider_id: str
    email: str
    name: str


class OAuthError(Exception):
    """provider 통신·응답 처리 실패 — 서비스가 502(Bad Gateway)로 변환한다."""


class OAuthProfileIncomplete(OAuthError):
    """프로필에 필수 정보(email 등)가 없음 — 서비스가 400 으로 변환한다.

    provider 자체는 정상 응답했으나(예: 이메일 제공 미동의) 로그인에 필요한 값이
    빠진 경우다. 통신 실패(502)와 구분해 사용자에게 원인을 안내한다.
    """


def fetch_profile(
    provider: str,
    config: OAuthProviderConfig,
    code: str,
    state: str | None = None,
) -> OAuthProfile:
    """provider 에서 authorization code 로 프로필을 조회한다.

    state 는 naver 토큰 교환에만 필요하다(kakao·google 은 무시). 지원하지 않는
    provider 는 OAuthError 로 막는다(라우터에서 이미 걸러지지만 방어적으로).
    """
    if provider == "kakao":
        return _fetch_kakao(config, code)
    if provider == "google":
        return _fetch_google(config, code)
    if provider == "naver":
        return _fetch_naver(config, code, state)
    raise OAuthError(f"지원하지 않는 provider: {provider}")


def _request_json(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    """provider HTTP 호출 후 JSON dict 로 파싱(실패는 OAuthError 로 변환)."""
    try:
        res = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise OAuthError("provider 통신에 실패했습니다") from exc

    if res.status_code != 200:
        raise OAuthError(f"provider 응답 오류(status={res.status_code})")

    try:
        payload = res.json()
    except ValueError as exc:
        raise OAuthError("provider 응답을 해석하지 못했습니다") from exc

    if not isinstance(payload, dict):
        raise OAuthError("provider 응답 형식이 올바르지 않습니다")
    return payload


def _access_token(token: dict, provider: str) -> str:
    """토큰 응답에서 access_token 을 꺼낸다(error·누락은 OAuthError)."""
    # naver 는 실패 시에도 200 에 error 필드를 실어 보내므로 함께 검사한다.
    if token.get("error"):
        raise OAuthError(f"{provider} 토큰 교환 실패")
    access_token = token.get("access_token")
    if not access_token:
        raise OAuthError(f"{provider} access_token 이 없습니다")
    return str(access_token)


def _fallback_name(name: str, email: str) -> str:
    """표시 이름이 비면 이메일 로컬파트로 채운다(name 은 DB 에서 NOT NULL)."""
    return name or email.split("@")[0]


def _fetch_kakao(config: OAuthProviderConfig, code: str) -> OAuthProfile:
    """카카오: token 교환 → v2/user/me 로 프로필 조회."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        token = _request_json(
            client,
            "POST",
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
                "code": code,
            },
        )
        access_token = _access_token(token, "kakao")
        profile = _request_json(
            client,
            "GET",
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    provider_id = profile.get("id")
    if provider_id is None:
        raise OAuthError("kakao 프로필 id 가 없습니다")
    account = profile.get("kakao_account") or {}
    email = account.get("email")
    if not email:
        raise OAuthProfileIncomplete("kakao 이메일 제공에 동의해야 로그인할 수 있습니다")
    name = (account.get("profile") or {}).get("nickname") or ""
    return OAuthProfile("kakao", str(provider_id), email, _fallback_name(name, email))


def _fetch_google(config: OAuthProviderConfig, code: str) -> OAuthProfile:
    """구글: token 교환 → userinfo 로 프로필 조회."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        token = _request_json(
            client,
            "POST",
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
                "code": code,
            },
        )
        access_token = _access_token(token, "google")
        profile = _request_json(
            client,
            "GET",
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    provider_id = profile.get("id")
    if not provider_id:
        raise OAuthError("google 프로필 id 가 없습니다")
    email = profile.get("email")
    if not email:
        raise OAuthProfileIncomplete("google 이메일을 가져오지 못했습니다")
    name = profile.get("name") or ""
    return OAuthProfile("google", str(provider_id), email, _fallback_name(name, email))


def _fetch_naver(config: OAuthProviderConfig, code: str, state: str | None) -> OAuthProfile:
    """네이버: token 교환(state 필수) → v1/nid/me 로 프로필 조회."""
    if not state:
        raise OAuthProfileIncomplete("naver 로그인에는 state 값이 필요합니다")

    with httpx.Client(timeout=_TIMEOUT) as client:
        token = _request_json(
            client,
            "POST",
            "https://nid.naver.com/oauth2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "state": state,
            },
        )
        access_token = _access_token(token, "naver")
        profile = _request_json(
            client,
            "GET",
            "https://openapi.naver.com/v1/nid/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    resp = profile.get("response") or {}
    provider_id = resp.get("id")
    if not provider_id:
        raise OAuthError("naver 프로필 id 가 없습니다")
    email = resp.get("email")
    if not email:
        raise OAuthProfileIncomplete("naver 이메일 제공에 동의해야 로그인할 수 있습니다")
    name = resp.get("name") or resp.get("nickname") or ""
    return OAuthProfile("naver", str(provider_id), email, _fallback_name(name, email))
