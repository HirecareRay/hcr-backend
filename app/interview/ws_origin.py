"""면접 WS Origin 허용 검사 — CSWSH(Cross-Site WebSocket Hijacking) 방어.

브라우저는 WebSocket 핸드셰이크에 Origin 헤더를 자동으로(JS 로 위조 불가, 브라우저가
강제) 싣는다. 일반 HTTP 는 CORS 가 교차 출처 요청을 막아주지만 WebSocket 엔 CORS 가
적용되지 않으므로, 서버가 직접 Origin 을 보고 허용된 프론트 도메인에서 온 연결만
받아야 한다. 이 검사가 없으면 악성 사이트가 로그인된 사용자의 브라우저로 우리 WS 에
몰래 붙어(쿠키·세션 편승) 면접 세션을 가로채거나 빌린 OpenAI 키 비용을 태울 수 있다.

허용 목록은 settings.interview_allowed_origins(콤마 구분)에서 온다. 비어 있으면
'개발 모드'로 보고 로컬 프론트(localhost:3000·127.0.0.1:3000)만 허용한다 — 운영
에선 .env 로 실제 도메인을 반드시 채운다(미설정 시 호출부가 로그로 경고).
"""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# 개발 모드(허용 목록 미설정) 기본 허용 출처 — 로컬 프론트(Next.js dev :3000).
_DEV_ORIGINS: frozenset[str] = frozenset(
    {"http://localhost:3000", "http://127.0.0.1:3000"}
)

# 개발 모드 경고를 프로세스당 1회만 내기 위한 가드(연결마다 로그 스팸 방지).
_dev_mode_warned = False


def _parse_allowed(raw: str) -> frozenset[str]:
    """콤마 구분 허용 Origin 문자열을 정규화한다 — split·trim·빈값 제거."""
    return frozenset(origin.strip() for origin in raw.split(",") if origin.strip())


def is_dev_mode() -> bool:
    """허용 목록이 비어 있으면 개발 모드(로컬 프론트만 허용)로 본다."""
    return not _parse_allowed(settings.interview_allowed_origins)


def warn_once_if_dev_mode() -> None:
    """운영 오설정(허용목록 미설정 → 조용히 개발 모드)을 프로세스당 1회 경고한다.

    INTERVIEW_ALLOWED_ORIGINS 를 운영에서 비우면 개발 모드로 떨어져 로컬 프론트만
    허용된다 — 가장 흔한 오설정 실패 모드다. 주석·.env.example 만으로는 런타임에
    눈치채기 어려우므로, 첫 WS 연결 때 한 번 경고를 남긴다(연결마다 스팸 방지).
    """
    global _dev_mode_warned
    if _dev_mode_warned or not is_dev_mode():
        return
    _dev_mode_warned = True
    logger.warning(
        'INTERVIEW_ALLOWED_ORIGINS 미설정 — 면접 WS 가 개발 모드로 동작한다'
        '(로컬 프론트만 허용). 운영에선 반드시 허용 도메인을 채워라(CSWSH 방어).'
    )


def is_allowed_origin(origin: str | None) -> bool:
    """이 Origin 에서 온 WS 연결을 받아도 되는지 판정한다.

    - 허용 목록이 설정돼 있으면(운영): 목록에 정확히 일치할 때만 허용한다. Origin
      헤더가 아예 없으면(비브라우저 클라이언트 등) 거절한다 — 브라우저는 정상
      연결 시 항상 Origin 을 보내므로, 없음은 우회 시도로 보고 막는다.
    - 목록이 비어 있으면(개발 모드): 로컬 프론트(localhost:3000)만 허용하되, Origin
      헤더가 없는 경우(python websockets·테스트 도구 등)는 개발 편의로 허용한다.
    """
    allowed = _parse_allowed(settings.interview_allowed_origins)
    if allowed:
        # 운영: 명시 목록만 통과. Origin 없음(None)·불일치는 모두 거절한다.
        return origin in allowed
    # 개발 모드: 로컬 프론트, 또는 Origin 헤더가 없는 비브라우저 도구를 허용한다.
    if origin is None:
        return True
    return origin in _DEV_ORIGINS
