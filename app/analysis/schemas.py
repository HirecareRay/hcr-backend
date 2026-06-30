"""적합도 분석 LLM 출력 스키마.

수치 점수 없음 — 강점(strengths) vs 약점(gaps) 항목 수 비율로 프론트에서 시각화.
TODO: 필드 내용은 prompts.py 작성 후 맞춰 조정하세요.

시각화 계산식 (프론트):
  ratio = strengths.length / (strengths.length + gaps.length)  → 그라데이션/게이지 표기
"""

from pydantic import BaseModel


class DimensionEval(BaseModel):
    label: str        # 예: "직무 역량", "기술 스택", "경력 수준", "기업 문화 적합도"
    summary: str      # 해당 차원 평가 내용 (한 문장)
    isStrength: bool  # True=강점, False=개선 필요


class FitResult(BaseModel):
    overallSummary: str           # 전체 종합 평가 (한 문장)
    strengths: list[str]          # 강점 목록
    gaps: list[str]               # 부족한 부분 목록
    recommendations: list[str]    # 개선 제안 목록
    dimensions: list[DimensionEval]  # 차원별 정성 평가
