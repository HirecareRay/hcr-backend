"""면접 WS 입장 티켓 저장소 — 단기·1회용 입장권.

브라우저 WebSocket 은 Authorization 헤더를 못 붙이고, 프론트는 JWT 를 httpOnly
쿠키에 둬 JS 로 읽지 못한다. 그래서 면접 입장 직전 일반 HTTP(POST /interviews/
ws-ticket, Bearer JWT)로 불투명·단기 티켓을 발급받아 WS 쿼리(?ticket=...)로만
실어 보낸다 — JWT 자체를 URL 에 노출하지 않는 표준 패턴(WebSocket ticket).

티켓은 (1) 불투명 랜덤 문자열, (2) TTL 60초 권장 단기, (3) 한 번 소비하면 폐기
되는 1회용이다. 무효·만료·없는 티켓은 None 을 돌려준다 — 면접 WS 핸들러는 이때
연결을 거절한다(면접은 로그인 사용자 전용, 빌린 OpenAI 키 비용 남용 차단).

# TODO: 운영은 Redis 로 교체 — 현재 인메모리 dict 는 멀티 워커·재시작 시 티켓이
# 유실된다(워커 A 가 발급한 티켓을 워커 B 가 못 본다). 단일 프로세스 개발·데모 전제.
"""

import secrets
import time

from app.core.config import settings

# 티켓 → (user_id, 만료시각[monotonic 초]). 모듈 전역 1개 — 프로세스 수명 동안 유지.
_store: dict[str, tuple[str, float]] = {}

# 단조 시계 — 벽시계(time.time)와 달리 시스템 시간 조정에 영향받지 않아 TTL 비교에
# 안전하다. 테스트는 이 심볼을 monkeypatch 해 시간을 결정론으로 만든다.
_now = time.monotonic


def _sweep_expired(now: float) -> None:
    """만료된 티켓을 청소한다(발급 시점의 lazy GC).

    consume 은 소비된 티켓만 제거하므로, 발급만 되고 소비되지 않은 티켓은 만료돼도
    영구히 남아 메모리가 단조 증가한다(인증 사용자가 발급을 반복하면 약한 DoS).
    발급 때마다 만료분을 함께 비워 인메모리 저장소의 무한 증식을 막는다.
    순회 중 삭제로 인한 RuntimeError 를 피하려 키 스냅샷을 먼저 뜬다.
    """
    expired = [key for key, (_, expires_at) in list(_store.items()) if now >= expires_at]
    for key in expired:
        _store.pop(key, None)


def issue_ticket(user_id: str, ttl_seconds: int | None = None) -> tuple[str, int]:
    """user_id 에 대해 1회용 단기 티켓을 발급한다.

    불투명 랜덤 문자열을 만들어 (user_id, 만료시각) 매핑을 저장하고, 티켓과
    만료초(expires_in)를 돌려준다. ttl_seconds 를 생략하면 settings 기본값을 쓴다.
    발급 전 만료된 티켓을 청소해 저장소가 무한히 커지지 않게 한다.
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.interview_ws_ticket_ttl_seconds
    now = _now()
    _sweep_expired(now)
    ticket = secrets.token_urlsafe(32)
    _store[ticket] = (user_id, now + ttl)
    return ticket, ttl


def consume_ticket(ticket: str | None) -> str | None:
    """티켓을 1회 소비해 user_id 를 돌려준다(무효·만료·없음이면 None).

    존재하면 즉시 매핑을 제거(pop)해 재사용을 막고, 만료 전이면 user_id 를,
    만료됐으면 None 을 돌려준다. 호출부(WS 핸들러)는 None 이면 연결을 거절한다.
    """
    if not ticket:
        return None
    entry = _store.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if _now() >= expires_at:
        return None
    return user_id


def clear() -> None:
    """저장소를 비운다 — 테스트 격리 전용(운영 경로에서 호출하지 않는다)."""
    _store.clear()
