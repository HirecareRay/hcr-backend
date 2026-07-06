"""검색어 정규화 + 별칭(동의어) 확장 — 기업명/직무명 검색의 "유사 검색"용.

범위: 법인표기(주식회사·㈜ 등)·띄어쓰기·특수문자 무시, 사전 등록된 별칭만 확장한다.
오타 허용(fuzzy)·초성 검색은 범위 밖(요청 시 별도 추가).
사전에 없는 별칭은 매칭하지 않는다 — 안 보이는 것이 오매칭보다 낫다.
"""

import re
import unicodedata

_STRIP_RE = re.compile(
    r"주식회사|㈜|\(주\)|\(유\)|유한회사"
    r"|co\.,?\s*ltd\.?|inc\.?|corp\.?|corporation|holdings|group"
    r"|[\s.,\-_()]",
    re.IGNORECASE,
)

# 검색용 별칭 — 정규화된 입력 → 정규화된 별칭. 필요할 때 한 줄씩 추가.
ALIASES: dict[str, str] = {
    "씨제이이앤엠": "cjenm",
    "씨제이대한통운": "cj대한통운",
    "씨제이올리브영": "cj올리브영",
    "씨제이제일제당": "cj제일제당",
    "ff": "f&f",
    "jyp엔터테인먼트": "jyp",
}


def normalize(name: str) -> str:
    """법인표기·공백·특수문자 제거 + 소문자화 + 유니코드 NFKC 정규화.

    NFKC 두 가지를 동시에 잡는다: ① 한글 완성형/조합형 차이(NFC로도 해결), ② 전각(％
    같은) ↔ 반각(%) 차이. MariaDB 콜레이션은 전각/반각을 같은 값으로 보는데 파이썬 문자열
    비교는 그렇지 않아서, NFKC로 미리 접어두지 않으면 파이썬에선 새 값인데 DB PK 충돌이
    난다(실제로 "111%"와 "111％"가 이 문제로 부딪힘).
    """
    n = unicodedata.normalize("NFKC", name or "")
    return _STRIP_RE.sub("", n).lower()


def search_terms(q: str) -> list[str]:
    """검색어 → [정규화된 원본, (별칭 있으면) 정규화된 별칭 대상]. 중복 제거."""
    n = normalize(q)
    if not n:
        return []
    terms = [n]
    alt = ALIASES.get(n)
    if alt and alt not in terms:
        terms.append(alt)
    return terms
