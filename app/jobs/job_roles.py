"""직군 분류(jobRole) 사전 + 키워드 규칙 기반 분류기 + 기술 태그 추출.

채용공고의 직무명·제목·자격/우대 텍스트를 키워드 규칙으로 분류한다(LLM 불필요).
규칙은 사전(dictionary)으로 관리해 확장 가능하게 둔다 — 추후 `data`·`mobile`·
`devops` 등을 사전에 한 줄씩 추가하면 된다.

- 제목·직무명(강한 신호)은 자격/우대 본문(약한 신호)보다 가중치를 높게 준다.
- 여러 직군에 걸리면 점수가 가장 높은 1개(primary)를 고르고, 동점은 고정
  우선순위(`_ROLE_PRIORITY`)로 일관되게 깬다.
- 어디에도 안 걸리면 `etc`(홈 카드에선 노출 안 함).
"""

import re

# ── 직군 enum(문자열) · 한글 라벨 ─────────────────────────────────────
# 문자열 enum 이라 사전에 항목을 더하면 그대로 확장된다.
ROLE_LABELS: dict[str, str] = {
    "backend": "백엔드",
    "frontend": "프론트엔드",
    "ai": "AI",
    "etc": "기타",
}

# 홈 카드 기본 노출 직군(순서 = 표시 순서).
DEFAULT_ROLES: tuple[str, ...] = ("backend", "frontend", "ai")

# 동점 시 우선순위(앞이 강함). AI 키워드가 가장 구체적이라 먼저,
# 풀스택 성격이면 backend 를 frontend 보다 앞세운다 — 재량이되 일관되게.
_ROLE_PRIORITY: tuple[str, ...] = ("ai", "backend", "frontend")

# ── 직군별 키워드 사전 ────────────────────────────────────────────────
# 값은 소문자로 관리(영문). 한글은 그대로. 매칭은 아래 규칙 참고:
#  - 영문/숫자 토큰: 앞뒤 알파벳 경계로만 매칭(email 의 'ai', html 의 'ml' 오탐 방지)
#  - 한글 토큰: 부분 문자열 포함 매칭
_ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "backend": (
        "백엔드", "서버개발", "서버 개발", "서버", "server",
        "java", "spring", "node", "nodejs", "node.js", "go", "golang",
        "kotlin", "api", "msa", "django", "flask", "fastapi", "php",
        "ruby", "rails", "grpc", "kafka", "nestjs", "express",
    ),
    "frontend": (
        "프론트엔드", "프론트", "웹개발", "웹 개발", "웹퍼블리셔", "퍼블리싱",
        "화면 개발", "ui 개발", "frontend", "front-end",
        "react", "vue", "next", "nextjs", "next.js", "typescript",
        "javascript", "angular", "svelte", "tailwind", "redux", "webpack",
    ),
    "ai": (
        "인공지능", "머신러닝", "딥러닝", "데이터사이언스", "데이터 사이언스",
        "자연어처리", "추천시스템", "추천 시스템", "추천",
        "ai", "ml", "llm", "mlops", "nlp", "rag", "gpt",
        "pytorch", "tensorflow", "transformer", "recommendation",
    ),
}

# ── 기술 태그 사전(매칭 소문자 → 표준 표기) ───────────────────────────
# tags 필드용. 원문에 tags 가 없어 텍스트에서 기술 토큰만 뽑아 채운다.
_TECH_TAGS: dict[str, str] = {
    "java": "Java", "kotlin": "Kotlin", "spring": "Spring", "python": "Python",
    "node": "Node.js", "nodejs": "Node.js", "node.js": "Node.js",
    "go": "Go", "golang": "Go", "php": "PHP", "ruby": "Ruby",
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "nestjs": "NestJS", "express": "Express",
    "react": "React", "vue": "Vue", "next": "Next.js", "nextjs": "Next.js",
    "next.js": "Next.js", "angular": "Angular", "svelte": "Svelte",
    "typescript": "TypeScript", "javascript": "JavaScript",
    "tailwind": "Tailwind", "redux": "Redux", "webpack": "Webpack",
    "aws": "AWS", "gcp": "GCP", "azure": "Azure", "docker": "Docker",
    "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "mysql": "MySQL", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
    "mongodb": "MongoDB", "redis": "Redis", "kafka": "Kafka",
    "elasticsearch": "Elasticsearch", "graphql": "GraphQL", "grpc": "gRPC",
    "pytorch": "PyTorch", "tensorflow": "TensorFlow", "llm": "LLM",
    "mlops": "MLOps", "nlp": "NLP", "rag": "RAG",
}

_MAX_TAGS = 8


def _is_ascii_token(token: str) -> bool:
    """영문/숫자/일부 기호로만 이뤄진 토큰인지(경계 매칭 대상)."""
    return all(ord(ch) < 128 for ch in token)


def _compile(keyword: str) -> re.Pattern:
    """키워드 → 매칭 정규식. 영문 토큰은 알파벳 경계로 감싸 오탐을 막는다."""
    esc = re.escape(keyword)
    if _is_ascii_token(keyword):
        return re.compile(rf"(?<![a-z0-9]){esc}(?![a-z0-9])", re.IGNORECASE)
    return re.compile(esc, re.IGNORECASE)


# 사전을 정규식으로 미리 컴파일(요청마다 재컴파일 방지).
_ROLE_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    role: tuple(_compile(kw) for kw in kws) for role, kws in _ROLE_KEYWORDS.items()
}
_TAG_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (_compile(kw), label) for kw, label in _TECH_TAGS.items()
)

_STRONG_WEIGHT = 3  # 제목·직무명 매치 가중치
_WEAK_WEIGHT = 1    # 자격/우대 본문 매치 가중치


def _count_hits(patterns: tuple[re.Pattern, ...], text: str) -> int:
    """text 에 매칭되는 키워드 개수(중복 키워드는 1로 계수)."""
    return sum(1 for p in patterns if p.search(text))


def classify_job_role(strong_text: str, weak_text: str = "") -> str:
    """직무명·제목(strong) + 자격/우대(weak) → 직군 1개.

    각 직군 점수 = strong 매치수*3 + weak 매치수*1. 최고 점수 직군을 고르고,
    0점이면 `etc`, 동점은 `_ROLE_PRIORITY` 순으로 깬다.
    """
    scores: dict[str, int] = {}
    for role, patterns in _ROLE_PATTERNS.items():
        score = (
            _count_hits(patterns, strong_text) * _STRONG_WEIGHT
            + _count_hits(patterns, weak_text) * _WEAK_WEIGHT
        )
        if score > 0:
            scores[role] = score
    if not scores:
        return "etc"
    best = max(scores.values())
    winners = [r for r, s in scores.items() if s == best]
    if len(winners) == 1:
        return winners[0]
    for role in _ROLE_PRIORITY:
        if role in winners:
            return role
    return winners[0]


def extract_tech_tags(text: str) -> list[str]:
    """텍스트에서 기술 스택 태그를 추출(표준 표기, 중복 제거, 최대 8개)."""
    tags: list[str] = []
    for pattern, label in _TAG_PATTERNS:
        if label not in tags and pattern.search(text):
            tags.append(label)
            if len(tags) >= _MAX_TAGS:
                break
    return tags
