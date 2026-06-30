"""인기기업 순위(랭킹) 비즈니스 로직.

조회수 적재(record_view)와 순위 조립(get_trending)을 담당한다. 순위는 MariaDB
조회수 합산 상위 회사 id 를 뽑아 MongoDB 회사 메타(이름·업종)로 보강하고, 카드용
파생값(logoText 이니셜·logoColor·logoUrl)을 붙여 프론트 TrendingCompany 형태로
만든다. logoUrl 은 큐레이션(logo_url) 우선, 없으면 website_url 도메인으로 CDN 로고
URL 을 산출한다(없으면 None - 프론트는 logoText/logoColor 이니셜 원으로 폴백).
라우터는 여기로 위임하고, 여기서만 repository(MariaDB + MongoDB)를 조합한다.
"""

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import date, timedelta
from urllib.parse import urlparse

from pymongo.database import Database
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ranking import repository

logger = logging.getLogger(__name__)

# 로고 원 배경색 팔레트 — company_id 해시로 결정적으로 하나 고른다(요청마다 동일).
# 프론트 zod 제약(#rrggbb 6자리 hex)에 맞는 값만 둔다.
_LOGO_COLORS = (
    "#e2402a", "#3182f6", "#03c75a", "#f4b400", "#2ac1bc",
    "#7c5cff", "#ff7a59", "#0aa5b8",
)


def _logo_text(name: str) -> str:
    """회사명 앞 (주)·㈜ 를 떼고 앞 두 글자 — 비면 '?'(min 1 보장)."""
    n = re.sub(r"^\(?주\)?|㈜", "", name or "").strip()
    return n[:2].upper() if n else "?"


def _logo_color(company_id: str) -> str:
    """company_id 를 해시해 팔레트에서 결정적으로 색 하나 고른다."""
    digest = hashlib.md5(company_id.encode("utf-8"), usedforsecurity=False).hexdigest()
    return _LOGO_COLORS[int(digest, 16) % len(_LOGO_COLORS)]


def _domain(url: str | None) -> str | None:
    """URL/문자열에서 호스트 도메인만 뽑는다 - scheme 없는 값도 처리.

    예: "https://www.cj.net" -> "cj.net", "www.cj.net/about" -> "cj.net".
    urlparse 는 scheme 가 없으면 netloc 을 못 채우므로, 비면 path 첫 토큰을 쓴다.
    앞의 "www." 는 떼고, 포트(":8080")가 붙으면 떼고, 공백/빈값이면 None.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.strip().lower().split(":", 1)[0]  # 포트 제거
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _logo_url(meta: dict) -> str | None:
    """회사 메타 -> 로고 이미지 URL(문자열) 또는 None.

    1) logo_url 큐레이션 값이 있으면(strip 후 비지 않으면) 그대로 쓴다.
    2) 없으면 website_url 도메인으로 CDN 로고 URL(f"{base}/{domain}")을 만든다.
       base(settings.ranking_logo_cdn_base)가 빈 문자열이면 자동 산출을 끄고 None.
    """
    curated = str(meta.get("logo_url") or "").strip()
    if curated:
        return curated
    base = settings.ranking_logo_cdn_base.strip().rstrip("/")  # 끝 슬래시 정규화
    if not base:
        return None
    domain = _domain(meta.get("website_url"))
    return f"{base}/{domain}" if domain else None


def _card(rank: int, company_id: str, meta: dict) -> dict:
    """회사 메타 → TrendingCompany 카드 dict(snake_case, 스키마가 camel 로 직렬화)."""
    name = str(meta.get("company_name") or "").strip()
    return {
        "rank": rank,
        "company_id": company_id,
        "name": name,
        "parent_name": str(meta.get("industry") or "").strip(),  # 부제 슬롯(실값=업종)
        "logo_text": _logo_text(name),
        "logo_color": _logo_color(company_id),
        "logo_url": _logo_url(meta),
    }


def record_view(session_factory: Callable[[], Session], company_id: str) -> None:
    """기업 리포트 조회 1회를 오늘자 카운터에 +1 한다(인기기업 집계용).

    BackgroundTasks 로 응답을 보낸 뒤 실행되므로 요청용 세션(get_db)을 재사용하지
    않고 여기서 새 세션을 열고 닫는다 — FastAPI 0.106+ 는 응답 후 yield 의존성을
    먼저 정리해, 그 세션을 백그라운드에서 쓰면 깨진다. 집계 실패가 사용자 흐름을
    막지 않도록 예외는 삼킨다(과금·핵심 경로 아님).
    """
    db = None
    try:
        db = session_factory()
        repository.increment_view(db, company_id, date.today())
        db.commit()
    except Exception:
        if db is not None:
            db.rollback()
        logger.warning("조회수 집계 실패 company_id=%s", company_id, exc_info=True)
    finally:
        if db is not None:
            db.close()


def get_trending(
    db: Session, mongo: Database, limit: int, window_days: int
) -> list[dict]:
    """최근 window_days 일 조회수 상위 회사 → TrendingCompany 카드 리스트.

    조회수 데이터가 비면(콜드 스타트) 회사 시드로 폴백해 빈 피드를 막는다.
    메타가 없는 id(삭제된 회사 등)는 건너뛰고 rank 는 1 부터 빈틈없이 다시 매긴다.
    """
    since = date.today() - timedelta(days=window_days - 1)
    ranked = repository.top_company_views(db, since, limit)
    ids = [cid for cid, _ in ranked]

    if ids:
        meta = repository.find_company_meta(mongo, ids)
        ordered = [(cid, meta[cid]) for cid in ids if cid in meta]
    else:
        ordered = [(str(c["_id"]), c) for c in repository.find_seed_companies(mongo, limit)]

    # 이름 없는 회사는 rank 부여 전에 제외 — rank 가 1부터 빈틈없이 매겨지도록(min 1 보장)
    named = [(cid, m) for cid, m in ordered if str(m.get("company_name") or "").strip()]
    return [_card(i + 1, cid, m) for i, (cid, m) in enumerate(named)]
