"""뉴스 비즈니스 로직 — MariaDB news 를 최신순 평탄 리스트로 조립.

라우터는 여기로 위임한다. 표기 변환(camelCase)은 응답 스키마(CamelModel)가 한다.
회사 리포트 growth.news 와 동일 컬렉션(news)을 전 기업 가로질러 조회한다.
"""

from sqlalchemy.orm import Session

from app.news import repository

# headline 앞머리에서 회사명 뒤에 붙는 구분자(중복 태그 제거용).
_PREFIX_TRIM = " \t,·:;∙|─-—–[]()<>「」『』\"'"


def _strip_company_prefix(title: str, company: str) -> str:
    """headline 앞머리에 회사명이 중복되면 제거(프론트가 태그로 이미 노출).

    제거 후 남는 게 없으면 원문을 그대로 둔다.
    """
    text = (title or "").strip()
    tag = (company or "").strip()
    if tag and text.startswith(tag):
        rest = text[len(tag):].lstrip(_PREFIX_TRIM)
        if rest:
            return rest
    return text


def build_news_list(db: Session, limit: int) -> dict:
    """전 기업 최신 뉴스 → 홈 브리핑 아이템(최신순, 기사 중복 제거)."""
    rows = repository.find_latest_news(db, limit)
    items: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        key = str(r.article_id or r.id or "")
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "id": str(r.id or ""),
            "company_tag": str(r.company or ""),
            "headline": _strip_company_prefix(str(r.title or ""), str(r.company or "")),
            "url": str(r.url or ""),
            "published_at": r.date.isoformat() if r.date else "",
        })
        if len(items) >= limit:
            break
    return {"items": items}
