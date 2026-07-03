"""result_builder 단위 테스트 — LLM dict + 재료 → InterviewResult 안전 변환.

LLM 출력은 신뢰 불가 입력이라, 누락·형식 오류·범위 밖 점수가 와도 결과 계약을
깨지 않고 정직한 기본값으로 우회하는지를 검증한다(가짜로 채우지 않음).
"""

from app.interview import result_builder
from app.interview.result_schemas import ResultMeta
from app.interview.service import Turn


def _meta() -> ResultMeta:
    return ResultMeta(
        result_id='r1',
        company_id='c1',
        company_name='CJ ENM',
        job_title='마케팅',
        conducted_at='2026-06-29T00:00:00+00:00',
        duration_sec=120,
        mode='voice',
        question_count=2,
    )


def _history() -> tuple[Turn, ...]:
    return (
        Turn('자기소개 해주세요', '안녕하세요', '명확함', 'common'),
        Turn('지원 동기는?', '브랜드가 좋아서', '구체성 부족', 'company'),
    )


def _full_report() -> dict:
    return {
        'overall': {'score': 78, 'grade': 'B+', 'headline': '안정적'},
        'answer_feedback': {
            'score': 82,
            'summary': '논리 좋음',
            'metrics': [
                {'label': '논리 구조', 'score': 85, 'value': '우수', 'comment': '두괄식'}
            ],
        },
        'strengths': ['두괄식 답변', ''],  # 빈 문자열은 걸러져야 한다
        'weaknesses': ['수치 부족'],
        'improvements': [
            {'area': '구체성', 'problem': '수치 없음', 'method': '숫자 준비'},
            {'problem': 'area 없음'},  # area 없으면 버려진다
        ],
        'script': [
            {'no': 1, 'score': 80, 'good': '명확', 'improve': '마무리'},
            {'no': 2, 'score': 70, 'good': '열정', 'improve': '구체화'},
        ],
        'recommended_questions': {'company': ['Q회사1'], 'job': ['Q직무1']},
    }


def test_build_result_maps_full_report():
    result = result_builder.build_result(
        meta=_meta(), history=_history(), report=_full_report()
    )
    assert result.overall.score == 78
    assert result.overall.grade == 'B+'
    assert result.feedback.answer.score == 82
    assert result.feedback.answer.metrics[0].label == '논리 구조'
    # 빈 문자열 강점은 걸러진다
    assert result.strengths == ['두괄식 답변']
    # area 없는 보완점은 버려진다
    assert len(result.improvements) == 1
    assert result.improvements[0].area == '구체성'
    assert result.recommended_questions.company == ['Q회사1']


def test_script_maps_history_with_eval_by_no():
    result = result_builder.build_result(
        meta=_meta(), history=_history(), report=_full_report()
    )
    assert [(s.no, s.category) for s in result.script] == [(1, 'common'), (2, 'company')]
    # 질답 사실은 히스토리가 권위, 평가는 report.script 에서 no 로 매칭
    assert result.script[0].question == '자기소개 해주세요'
    assert result.script[0].answer == '안녕하세요'
    assert result.script[0].evaluation.score == 80
    assert result.script[1].evaluation.improve == '구체화'


def test_empty_report_yields_safe_defaults():
    result = result_builder.build_result(meta=_meta(), history=_history(), report={})
    assert result.overall.score == 0
    assert result.overall.grade  # 점수 기반 기본 등급이 채워진다(빈 문자열 아님)
    assert result.strengths == []
    assert result.improvements == []
    # 스크립트는 히스토리 길이만큼, 평가는 안전 기본값
    assert len(result.script) == 2
    assert result.script[0].evaluation.score == 0


def test_expression_voice_empty_when_not_provided():
    result = result_builder.build_result(meta=_meta(), history=_history(), report={})
    assert result.feedback.expression.score == 0
    assert result.feedback.voice.score == 0
    assert result.feedback.expression.metrics == []


def test_category_narrowed_to_allowed_values():
    history = (Turn('q', 'a', 'e', 'weird-category'),)
    result = result_builder.build_result(meta=_meta(), history=history, report={})
    assert result.script[0].category == 'common'


def test_metric_without_label_dropped_and_score_clamped():
    report = {
        'answer_feedback': {
            'score': 200,  # 범위 밖 → 100 으로 clamp
            'summary': 's',
            'metrics': [
                {'score': 50},  # label 없음 → 버려짐
                {'label': '직무 적합성', 'score': -5},  # 음수 → 0 으로 clamp
            ],
        }
    }
    result = result_builder.build_result(
        meta=_meta(), history=_history(), report=report
    )
    assert result.feedback.answer.score == 100
    assert len(result.feedback.answer.metrics) == 1
    assert result.feedback.answer.metrics[0].score == 0


def test_default_grade_thresholds():
    assert result_builder._default_grade(95) == 'A'
    assert result_builder._default_grade(82) == 'B+'
    assert result_builder._default_grade(40) == 'D'


def test_unanswered_turn_forces_no_answer_eval_over_llm():
    """무응답 턴은 LLM 이 높은 점수·'잘한 점'을 줘도 결정론적으로 무응답 평가로 덮는다."""
    history = (
        Turn('자기소개 해주세요', '', '', 'common'),  # 무응답
        Turn('지원 동기는?', '브랜드가 좋아서', '구체성 부족', 'company'),
    )
    report = {
        'script': [
            # LLM 이 무응답 턴에 가짜 호평을 줘도 무시돼야 한다
            {'no': 1, 'score': 90, 'good': '논리적으로 훌륭함', 'improve': '없음'},
            {'no': 2, 'score': 70, 'good': '열정', 'improve': '구체화'},
        ],
    }
    result = result_builder.build_result(meta=_meta(), history=history, report=report)
    assert result.script[0].evaluation.score == 0
    assert result.script[0].evaluation.good == ''
    assert '답변이 없어' in result.script[0].evaluation.improve
    # 답변한 턴은 LLM 평가를 그대로 반영한다
    assert result.script[1].evaluation.score == 70
    assert result.script[1].evaluation.good == '열정'


def test_whitespace_only_answer_treated_as_no_answer():
    """공백만 있는 답변도 무응답으로 보고 가짜 평가를 막는다."""
    history = (Turn('q', '   ', '', 'common'),)
    report = {'script': [{'no': 1, 'score': 88, 'good': '좋음', 'improve': 'x'}]}
    result = result_builder.build_result(meta=_meta(), history=history, report=report)
    assert result.script[0].evaluation.score == 0
    assert result.script[0].evaluation.good == ''
