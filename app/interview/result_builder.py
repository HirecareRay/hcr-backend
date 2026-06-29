"""면접 결과 조립 — LLM 종합 dict + 재료 → InterviewResult (순수 변환, 부수효과 없음).

요약 시점에 모은 재료(턴 히스토리·LLM 종합·비언어/음성 지표·메타)를 결과 계약
(result_schemas.InterviewResult)으로 조립한다. LLM 출력은 신뢰 불가 입력이라 모든
필드를 안전 파싱·clamp 하고, 누락·형식 오류는 정직한 기본값으로 우회한다(가짜로
채우지 않음 — 데이터 없으면 빈 지표·빈 코멘트).

레이어 원칙: 여기서는 변환만 한다. LLM 호출은 llm.py, 저장·조회·비교는
result_service.py, 비언어/음성 → ModalFeedback 매핑은 nonverbal.py/voice.py 가 한다.
comparison 은 직전 세션 조회가 필요하므로 여기서 None 으로 두고 service 가 주입한다.
"""

from app.interview.result_schemas import (
    FeedbackGroup,
    FeedbackMetric,
    ImprovementItem,
    InterviewResult,
    ModalFeedback,
    OverallScore,
    RecommendedQuestions,
    ResultMeta,
    ScriptEvaluation,
    ScriptItem,
)
from app.interview.service import Turn

# 비언어/음성 지표가 아직 없을 때(미수신·미구현 슬라이스) 쓰는 정직한 빈 모달.
_NO_DATA_SUMMARY = '분석 데이터가 충분하지 않아 이 영역은 비워 둡니다.'


def empty_modal() -> ModalFeedback:
    """데이터가 없는 모달 피드백(표정·음성 미수신 시) — 가짜로 채우지 않는다."""
    return ModalFeedback(score=0, summary=_NO_DATA_SUMMARY, metrics=[])


def build_result(
    *,
    meta: ResultMeta,
    history: tuple[Turn, ...],
    report: dict,
    expression: ModalFeedback | None = None,
    voice: ModalFeedback | None = None,
) -> InterviewResult:
    """재료를 InterviewResult 로 조립한다(comparison 은 service 가 이후 주입).

    expression·voice 가 None 이면 빈 모달로 둔다(해당 슬라이스 미완·데이터 미수신).
    answer 모달과 overall·강약점·보완점·추천 질문·스크립트 평가는 LLM report 에서
    안전 파싱한다.
    """
    return InterviewResult(
        meta=meta,
        overall=_overall(report.get('overall')),
        feedback=FeedbackGroup(
            expression=expression or empty_modal(),
            voice=voice or empty_modal(),
            answer=_modal(report.get('answer_feedback')),
        ),
        strengths=_str_list(report.get('strengths')),
        weaknesses=_str_list(report.get('weaknesses')),
        improvements=_improvements(report.get('improvements')),
        script=_script(history, report.get('script')),
        recommended_questions=_recommended(report.get('recommended_questions')),
        comparison=None,
    )


def _overall(data: object) -> OverallScore:
    """overall 섹션을 안전 파싱한다(점수 clamp, 누락 라벨은 점수 기반 기본 등급)."""
    obj = data if isinstance(data, dict) else {}
    score = _clamp_int(obj.get('score'))
    grade = _str(obj.get('grade')) or _default_grade(score)
    return OverallScore(
        score=score,
        grade=grade,
        headline=_str(obj.get('headline')) or '면접 결과 총평을 생성하지 못했습니다.',
    )


def _modal(data: object) -> ModalFeedback:
    """answer_feedback 섹션을 ModalFeedback 으로 안전 파싱한다."""
    obj = data if isinstance(data, dict) else {}
    metrics = [_metric(item) for item in _as_list(obj.get('metrics'))]
    return ModalFeedback(
        score=_clamp_int(obj.get('score')),
        summary=_str(obj.get('summary')) or '답변 피드백을 생성하지 못했습니다.',
        metrics=[m for m in metrics if m is not None],
    )


def _metric(item: object) -> FeedbackMetric | None:
    """metrics 한 항목을 FeedbackMetric 으로 파싱한다(label 없으면 버린다)."""
    if not isinstance(item, dict):
        return None
    label = _str(item.get('label'))
    if not label:
        return None
    return FeedbackMetric(
        label=label,
        score=_clamp_int(item.get('score')),
        value=_str(item.get('value')),
        comment=_str(item.get('comment')),
    )


def _improvements(data: object) -> list[ImprovementItem]:
    """improvements 리스트를 안전 파싱한다(area 없는 항목은 버린다)."""
    items: list[ImprovementItem] = []
    for raw in _as_list(data):
        if not isinstance(raw, dict):
            continue
        area = _str(raw.get('area'))
        if not area:
            continue
        items.append(
            ImprovementItem(
                area=area,
                problem=_str(raw.get('problem')),
                method=_str(raw.get('method')),
            )
        )
    return items


def _script(history: tuple[Turn, ...], data: object) -> list[ScriptItem]:
    """턴 히스토리(질문·답변)와 LLM 평가·분류를 no 기준으로 매핑한다.

    질답 사실(question·answer)은 우리 히스토리가 권위다. 평가(score·good·improve)와
    분류(category)는 LLM report.script 에서 no 로 찾아 붙인다 — LLM 이 질문 텍스트를
    보고 회사/직무/공통을 분류한다(후분류). category 가 없으면 turn.category(기본
    common)로, 평가가 없으면 안전 기본값으로 둔다.
    """
    by_no = _script_by_no(data)
    items: list[ScriptItem] = []
    for index, turn in enumerate(history, start=1):
        raw = by_no.get(index, {})
        items.append(
            ScriptItem(
                no=index,
                category=_category(_str(raw.get('category')) or turn.category),
                question=turn.question,
                answer=turn.answer,
                evaluation=_eval(raw),
            )
        )
    return items


def _script_by_no(data: object) -> dict[int, dict]:
    """report.script 를 {no: raw_dict} 로 안전 파싱한다(no 없는 항목은 버린다)."""
    result: dict[int, dict] = {}
    for raw in _as_list(data):
        if not isinstance(raw, dict):
            continue
        no = _to_int(raw.get('no'))
        if no is not None:
            result[no] = raw
    return result


def _eval(raw: dict) -> ScriptEvaluation:
    """report.script 한 항목의 평가를 파싱한다(비면 안전 기본값)."""
    if not raw:
        return _empty_eval()
    return ScriptEvaluation(
        score=_clamp_int(raw.get('score')),
        good=_str(raw.get('good')),
        improve=_str(raw.get('improve')) or '평가를 생성하지 못했습니다.',
    )


def _empty_eval() -> ScriptEvaluation:
    """LLM 평가가 매칭되지 않은 턴의 안전 기본 평가."""
    return ScriptEvaluation(score=0, good='', improve='평가를 생성하지 못했습니다.')


def _recommended(data: object) -> RecommendedQuestions:
    """recommended_questions 를 회사/직무 문자열 리스트로 안전 파싱한다."""
    obj = data if isinstance(data, dict) else {}
    return RecommendedQuestions(
        company=_str_list(obj.get('company')),
        job=_str_list(obj.get('job')),
    )


def _category(value: str) -> str:
    """Turn.category 를 결과 계약의 허용값(company/job/common)으로 좁힌다."""
    return value if value in ('company', 'job', 'common') else 'common'


def _default_grade(score: int) -> str:
    """등급 라벨이 없을 때 점수로 기본 등급을 파생한다."""
    thresholds = ((90, 'A'), (80, 'B+'), (70, 'B'), (60, 'C+'), (50, 'C'))
    for cutoff, label in thresholds:
        if score >= cutoff:
            return label
    return 'D'


def _str_list(data: object) -> list[str]:
    """리스트의 비어 있지 않은 문자열만 추린다(문자열 외 타입은 버린다)."""
    return [text for text in (_str(item) for item in _as_list(data)) if text]


def _as_list(data: object) -> list:
    """리스트면 그대로, 아니면 빈 리스트(LLM 이 dict/None 을 줘도 안전)."""
    return data if isinstance(data, list) else []


def _str(value: object) -> str:
    """값을 다듬은 문자열로(None·비문자열은 빈 문자열)."""
    return str(value).strip() if value is not None else ''


def _to_int(value: object) -> int | None:
    """정수로 강제(파싱 불가면 None)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _clamp_int(value: object) -> int:
    """점수를 0~100 정수로 강제한다(파싱 불가·범위 밖은 안전 보정)."""
    number = _to_int(value)
    if number is None:
        return 0
    return max(0, min(number, 100))
