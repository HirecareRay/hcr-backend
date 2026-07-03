"""면접 결과 리포트 응답 스키마 — 프론트 InterviewResult 계약의 백엔드 미러.

프론트 ``features/interview/types/interviewResult.ts`` 의 ``InterviewResult`` 를
1:1 미러한다(검증은 ``interviewResultSchema.ts``). 결과는 회사가 아니라 "세션"
단위이므로 ``meta.result_id`` 가 1급 식별자다.

표기 규칙: 전부 CamelModel 상속 — 내부는 snake_case, 직렬화 시 camelCase 로 나간다
(라우터에서 ``response_model_by_alias=True``). 판별 라벨(``mode``·``category``·
``direction``)의 "값"은 Literal 문자열이라 alias 영향을 받지 않고 그대로 나간다.

⚠️ replay(다시보기)는 계약에서 제외했다 — 녹화 인프라가 없어 가짜로 채우지 않는다
(프론트 타입·zod 에서도 제거, 양쪽 CLAUDE.md 계약 동기화).
"""

from typing import Literal

from pydantic import Field

from app.shared.schema import CamelModel

InterviewMode = Literal['text', 'voice']
DeltaDirection = Literal['up', 'down', 'same']
QuestionCategory = Literal['company', 'job', 'common']


class ResultMeta(CamelModel):
    """세션 식별 정보. result_id 는 회사가 아닌 세션 단위의 1급 식별자."""

    result_id: str
    company_id: str
    company_name: str
    job_title: str
    conducted_at: str  # ISO 8601 문자열
    duration_sec: int
    mode: InterviewMode
    question_count: int


class OverallScore(CamelModel):
    """종합 점수(히어로) — 0~100 점수·등급 라벨·AI 한 줄 총평."""

    score: int = Field(ge=0, le=100)
    grade: str
    headline: str


class FeedbackMetric(CamelModel):
    """세부 지표 — 0~100 정규화 점수 + 사람이 읽는 원본값(value) 보존."""

    label: str
    score: int = Field(ge=0, le=100)
    value: str
    comment: str


class ModalFeedback(CamelModel):
    """한 모달(표정·음성·답변)의 종합 점수·총평·세부 지표."""

    score: int = Field(ge=0, le=100)
    summary: str
    metrics: list[FeedbackMetric] = Field(default_factory=list)


class FeedbackGroup(CamelModel):
    """멀티모달 피드백 묶음 — 표정·음성·답변."""

    expression: ModalFeedback
    voice: ModalFeedback
    answer: ModalFeedback


class ImprovementItem(CamelModel):
    """보완점 — 영역·무엇이 부족했나(problem)·어떻게 보완하나(method)."""

    area: str
    problem: str
    method: str


class ScriptEvaluation(CamelModel):
    """질답 스크립트의 답변별 평가 — 점수·잘한 점·개선점."""

    score: int = Field(ge=0, le=100)
    good: str
    improve: str


class ScriptItem(CamelModel):
    """질답 스크립트 한 줄 — 질문 번호·분류·질문·답변 전사·평가."""

    no: int
    category: QuestionCategory
    question: str
    answer: str
    evaluation: ScriptEvaluation


class RecommendedQuestions(CamelModel):
    """추천 예상 질문 — 회사 관련·직무 관련."""

    company: list[str] = Field(default_factory=list)
    job: list[str] = Field(default_factory=list)


class MetricDelta(CamelModel):
    """이전 세션 대비 지표 변화 — direction 은 current-previous 부호와 일치시킨다."""

    label: str
    previous: int = Field(ge=0, le=100)
    current: int = Field(ge=0, le=100)
    delta: int
    direction: DeltaDirection


class InterviewComparison(CamelModel):
    """직전 세션과의 비교 — 첫 면접이면 결과 전체에서 None 으로 둔다."""

    previous_result_id: str
    previous_date: str  # ISO 8601 문자열
    attempt_count: int
    deltas: list[MetricDelta] = Field(default_factory=list)
    summary: str


class InterviewResult(CamelModel):
    """면접 결과 리포트 전체 — REST GET /interviews/results/* 의 응답 계약.

    replay(다시보기)는 계약에서 제외했다(녹화 인프라 미존재 — 모듈 docstring 참고).
    comparison 은 직전 세션이 없으면(첫 면접) None.
    """

    meta: ResultMeta
    overall: OverallScore
    feedback: FeedbackGroup
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    improvements: list[ImprovementItem] = Field(default_factory=list)
    script: list[ScriptItem] = Field(default_factory=list)
    recommended_questions: RecommendedQuestions
    comparison: InterviewComparison | None = None


class InterviewHistoryItem(CamelModel):
    """마이페이지 "AI 면접 기록" 카드 한 장 — 저장된 세션 결과의 meta+overall 요약.

    상세(InterviewResult)의 축약본이다 — 목록 화면 카드 미리보기용이라 점수·등급·
    한 줄 총평만 담고, 카드 클릭 시 result_id 로 상세를 조회한다(계약 ②). 새 계산 없이
    저장된 완성품에서 뽑는다(계약 ④ — 재조회 시 LLM 재호출 0).

    company_id·company_name 은 일반 면접(회사 미지정)이면 'general'·'일반 면접'으로
    라벨링한다(서비스에서 매핑) — 프론트 카드가 회사 없는 세션도 표시할 수 있게.
    """

    result_id: str
    company_id: str
    company_name: str
    job_title: str
    conducted_at: str  # ISO 8601 문자열
    mode: InterviewMode
    score: int = Field(ge=0, le=100)
    grade: str
    headline: str
    question_count: int


class InterviewHistoryList(CamelModel):
    """면접 기록 목록 응답 — 최신순 카드 배열 + 전체 세션 수(페이지네이션 대비).

    기록이 없으면 items=[]·total=0 (빈 목록은 정상 — 404 아님).
    """

    items: list[InterviewHistoryItem] = Field(default_factory=list)
    total: int
