# HCR (HireCareRay) — AI Backend

채용공고·기업사이트·DART 기반 **기업 분석 리포트** + **AI 모의 면접**을 제공하는 취업 준비 서비스의 **AI 백엔드(FastAPI)**.

프론트엔드(Next.js)는 별도 레포(`~/hcr-web`)에 있고, 이 레포는 LangChain + RAG 기반 AI 처리를 담당합니다.

> 📌 이 문서 하나로 **빠른 시작 + 폴더 구조 + 작업 규칙**을 모두 파악할 수 있게 정리했습니다.

---

## 빠른 시작 (5분)

```bash
# 1. 파이썬 3.12 환경 (로컬·EC2 공통)
conda activate py312

# 2. 의존성 설치
pip install -r requirements.txt          # 운영
pip install -r requirements-dev.txt      # 개발·테스트 (pytest·httpx·ruff·mypy)

# 3. 환경변수 파일 생성 (실제 .env 는 깃에 안 올림)
cp .env.example .env

# 4. 개발 서버 실행
uvicorn app.main:app --reload            # http://localhost:8000
```

| 확인 항목      | URL                                   |
| -------------- | ------------------------------------- |
| API 문서       | http://localhost:8000/docs (Swagger)  |
| 헬스체크       | http://localhost:8000/health          |
| 테스트 실행    | `pytest` (dev 의존성 설치 후)         |

> 새 의존성을 추가하면 `requirements.txt`(운영) 또는 `requirements-dev.txt`(개발)에 **반드시** 반영하세요.

---

## DB 연결 (로컬 개발)

MariaDB·MongoDB는 EC2에 Docker로 떠 있다(`mariadb:11.8` 3306, `mongo:8.2` 27017).
**EC2 보안그룹이 DB 포트를 외부에서 막고 있으므로, 로컬에서는 SSH 터널을 경유해 붙는다.**
(DB 포트를 인터넷에 직접 여는 것보다 안전 — 권장 방식)

```bash
# 1) 터널 켜기 — 개발하는 동안 별도 터미널에서 계속 띄워둔다
ssh -N -L 3306:localhost:3306 -L 27017:localhost:27017 <EC2별칭>

# 2) 서버 실행 (다른 터미널)
conda activate py312 && uvicorn app.main:app --reload

# 3) 연결 확인
curl localhost:8000/health/db   # → {"status":"ok","mariadb":true,"mongodb":true}
```

- `<EC2별칭>`은 각자 `~/.ssh/config`에 등록한다. **EC2 SSH 접속 권한이 필요** — 안 되면 DB 담당자에게 본인 SSH 공개키 등록을 요청한다.
- 터널을 경유하므로 `.env`의 DB 호스트는 공인 IP가 아니라 **`127.0.0.1`** 을 가리킨다.
- MongoDB가 계정 인증을 쓰면 URI에 **`?authSource=admin`** 을 붙인다.
- 터널을 안 켜고 서버를 띄우면 `timed out`. `/health/db`가 `degraded`면 터널부터 확인한다.
- **DB 계정·비밀번호는 깃에 올리지 않는다** — `.env.example`이 아니라 각자 `.env`에만 채우고, 값은 안전한 채널(비밀번호 관리자 등)로 공유받는다.

---

## 우리 위치 (전체 그림)

```
[브라우저]
   ▼
[Next.js 프론트 (~/hcr-web)]
   ▼
[Next.js BFF  app/api/**]   ← 지금은 더미데이터가 여기 있음
   ▼
[이 레포: FastAPI AI 백엔드]  ★ 우리가 만드는 곳
   ├─ MariaDB  (정형: 기업·채용·재무 + RAG 벡터·원문)
   ├─ MongoDB  (문서)
   └─ OpenAI API (GPT-4o mini), Whisper (STT)
```

**핵심:** 프론트는 우리를 직접 호출하지 않습니다. 항상 Next.js BFF를 거칩니다.
따라서 우리 응답은 **프론트의 타입 계약**에 맞춰야 합니다.

- 프론트 `features/[기능]/types/`의 응답 타입 = **우리가 맞춰야 할 출력 스펙**
- 프론트 BFF(`app/api/.../route.ts`)의 더미데이터 + `// TODO:` 주석이 곧 계약 형태
- **백엔드 내부는 전부 snake_case, 프론트로 나가는 최종 JSON만 camelCase** (응답 스키마의 alias로 자동 변환 — 손으로 키 이름 바꾸지 않음)
- 새 엔드포인트를 만들기 전에 **항상 프론트의 해당 `types/`와 BFF route를 먼저 확인**해 응답 형태를 맞춤

---

## 폴더 구조 (도메인별 모듈)

**레이어별이 아니라 도메인별로 묶습니다.** 한 기능(`company`)을 고치면 그 폴더 안에서
router·service·repository·schema가 다 보입니다. (프론트 `features/`와 같은 철학)

```
app/
├── main.py              # FastAPI 진입점 (CORS, 라우터 등록, /health)
├── core/
│   └── config.py        # pydantic-settings — 모든 환경변수 단일 관리
├── shared/
│   └── schema.py        # CamelModel — 응답 snake_case→camelCase 변환 베이스
├── db/
│   ├── session.py       # MariaDB(SQLAlchemy) 세션 — 정형 + RAG 벡터·원문, 연동 단계에 활성화
│   └── mongo.py         # MongoDB(pymongo) 클라이언트 — 문서, 연동 단계에 활성화
│
├── company/             # ★ 기준 레퍼런스 — 다른 도메인은 이걸 복사해 채움
│   ├── router.py        #   HTTP 입출력·검증만 (비즈니스 로직 금지)
│   ├── service.py       #   비즈니스 로직 (DB·LLM·RAG 조합)
│   ├── repository.py    #   DB 접근만 (파라미터 바인딩)
│   ├── schemas.py       #   Pydantic 요청·응답 (응답은 CamelModel 상속)
│   └── models.py        #   SQLAlchemy ORM 모델
│
├── interview/           # 모의 면접 (SSE 스트리밍) — 같은 구조로 채울 예정
│   └── router.py
└── search/              # 기업 검색·자동완성 — 같은 구조로 채울 예정
    └── router.py

tests/                   # pytest 스모크 테스트 (TestClient)
requirements.txt         # 운영 의존성
requirements-dev.txt     # 개발·테스트 의존성
.env.example             # .env 템플릿 (실제 .env 는 깃 제외)
```

### 요청이 흐르는 길 (레이어 원칙)

```
router  →  service  →  repository  →  models / schemas
(HTTP만)   (로직)       (DB접근만)      (ORM / Pydantic)
```

- **라우터에 비즈니스 로직·직접 DB 쿼리 금지.** 요청 검증 → service 호출 → 응답 반환만.
- `company/`가 현재 유일하게 채워진 **기준 구현**입니다. 더미를 반환하며 실연결 지점은 `# TODO:`로 표시돼 있습니다.

---

## 새 도메인 추가하는 법

`company/`를 그대로 따라가면 됩니다.

1. `app/[도메인]/` 폴더 생성
2. 파일 5개 구성: `router.py · service.py · repository.py · schemas.py · models.py`
3. **응답 스키마는 `app/shared/schema.py`의 `CamelModel`을 상속** (camelCase 자동 변환)
4. 라우터에 `response_model_by_alias=True` 지정
5. `app/main.py`에서 `include_router`로 등록
6. 작업 전 **프론트의 `features/[기능]/types/`와 BFF route를 먼저 확인**해 응답 형태를 맞춤

```python
# 응답 스키마: 내부는 snake_case, JSON 출력만 camelCase
from app.shared.schema import CamelModel

class ReportOut(CamelModel):
    company_name: str        # → JSON: "companyName"
    revenue_growth: float    # → JSON: "revenueGrowth"
```

---

## 코드 컨벤션 (요약)

백엔드는 **Python 표준 snake_case**, 프론트로 나가는 **최종 JSON만 camelCase**(스키마에서 자동 변환).

| 대상            | 규칙          | 예시                                  |
| --------------- | ------------- | ------------------------------------- |
| 클래스          | `PascalCase`  | `class Settings`, `class CompanyRepo` |
| 함수·변수·파일  | `snake_case`  | `get_report()`, `app_name`            |
| 상수            | `UPPER_SNAKE` | `MAX_RETRY_COUNT`                     |

- **타입 힌트 필수**, **Pydantic으로 입출력 검증** (임의 dict 반환 금지)
- **시크릿은 코드에 박지 않음** — 전부 `app/core/config.py` → `.env`에서 읽음
- 함수는 작게(<50줄), 파일은 집중되게(<800줄, 보통 200–400줄)

### 커밋 / 브랜치

- 커밋 타입: `feat` / `fix` / `docs` / `style` / `test` / `refactor` / `chore`
- 메시지: **한국어, 명령문, 마침표 없음, 50자 이내**
- 브랜치: `main`(배포) ← `develop`(통합) ← `feat/[name]`·`fix/[name]` (영어 kebab-case), 긴급 수정은 `hotfix/[name]` (main에서 분기)
- **git push 전 반드시 먼저 공유할 것**

---

## 핵심 기능 흐름

```
홈 메인 피드 (트렌딩 채용공고 · 기술스택 랭킹 · 기업 이슈 브리핑)
        ↓ 기업명 검색 (search)
AI 기업 분석 리포트 (재무·문화·성장·예상질문)   ← DART + 크롤러 + 뉴스 + RAG (company)
        ↓ 분석 데이터가 면접관 컨텍스트로 주입
AI 모의 면접 (음성 STT / 텍스트 → 실시간 평가 SSE 스트리밍)   (interview)
```

---

## 현재 상태

| 영역                       | 상태                                            |
| -------------------------- | ----------------------------------------------- |
| FastAPI 뼈대 · CORS · 헬스 | ✅ 동작                                          |
| `company` 도메인           | ⚙️ 더미 응답 (실연결 `# TODO`)                  |
| `interview` / `search`     | 🔜 라우터 뼈대만 (구조 따라 채울 예정)          |
| MariaDB / MongoDB          | 🔜 `db/` 주석 처리됨 — 연동 단계에 활성화        |
| LLM (LangChain/OpenAI)     | 🔜 연동 단계에 추가                              |
