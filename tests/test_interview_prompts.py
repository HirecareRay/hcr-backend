"""면접 프롬프트(app/interview/prompts.py) 단위 테스트 — 메인 질문·꼬리질문 메시지 조립."""

from app.interview import prompts
from app.interview.personas import CULTURE, TECH, assign_interviewers

# 슬롯 4개(인사→기술→실무→인사) — 메인 질문 프롬프트 테스트 공용 패널.
_PANEL4 = assign_interviewers(4)


def test_main_questions_messages_includes_count_and_self_intro_rule():
    """슬롯 개수(=personas 길이)와 자기소개 규칙이 시스템 프롬프트에 들어간다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '', '', _PANEL4)
    system = messages[0]['content']
    assert '정확히 4개' in system
    assert '자기소개' in system


def test_main_questions_messages_includes_panel_role_labels():
    """3인 패널의 담당 라벨·주제가 슬롯 목록으로 시스템 프롬프트에 실린다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '', '', _PANEL4)
    system = messages[0]['content']
    assert '인사담당자' in system
    assert '기술담당자' in system
    assert '실무담당자' in system
    # 담당 주제(focus)도 슬롯 라인에 함께 실린다.
    assert CULTURE.focus in system


def test_main_questions_messages_forbids_plural_audience():
    """1:1 면접이므로 '여러분' 같은 복수 청자 표현 금지 규칙이 프롬프트에 들어간다."""
    system = prompts.main_questions_messages('회사 컨텍스트', '', '', _PANEL4)[0]['content']
    assert '여러분' in system  # 금지 규칙으로 명시
    assert '1:1' in system


def test_follow_up_messages_forbids_plural_audience():
    """꼬리질문도 지원자 한 명에게 존댓말 — '여러분' 금지 규칙이 프롬프트에 들어간다."""
    system = prompts.follow_up_messages('질문', '답변', TECH)[0]['content']
    assert '여러분' in system


def test_main_questions_messages_omits_applicant_block_when_no_user_context():
    """지원자 정보가 없으면 user 메시지에 회사 컨텍스트만 담긴다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '', '', _PANEL4)
    user = messages[1]['content']
    assert '회사 컨텍스트' in user
    assert '지원자 정보' not in user
    assert '지원 직무' not in user


def test_main_questions_messages_appends_applicant_block_when_present():
    """지원자 정보가 있으면 user 메시지에 이력서 컨텍스트가 덧붙고, 개인화 규칙이 켜진다."""
    messages = prompts.main_questions_messages(
        '회사 컨텍스트', '[이력서]\n보유 기술: Python', '', _PANEL4
    )
    system, user = messages[0]['content'], messages[1]['content']
    assert '개인화 질문' in system  # 개인화 규칙 존재
    assert '지원자 정보' in user
    assert '보유 기술: Python' in user


def test_main_questions_messages_appends_job_title_when_present():
    """지원 직무가 있으면 user 메시지에 직무가 덧붙는다."""
    messages = prompts.main_questions_messages('', '', '데이터 엔지니어', _PANEL4)
    user = messages[1]['content']
    assert '지원 직무: 데이터 엔지니어' in user


def test_main_questions_messages_no_context_uses_placeholder():
    """회사·지원자·직무가 모두 비면 user 메시지는 안내 문구만 담는다."""
    messages = prompts.main_questions_messages('', '', '', _PANEL4)
    user = messages[1]['content']
    assert '회사 컨텍스트' not in user
    assert '지원자 정보' not in user
    assert '추가 정보 없음' in user


def test_follow_up_messages_carries_persona_role_and_tone():
    """꼬리질문 프롬프트에 담당 면접관의 라벨·말투가 실린다."""
    messages = prompts.follow_up_messages('직전 질문', '지원자 답변', TECH)
    system = messages[0]['content']
    assert TECH.role_label in system
    assert TECH.focus in system
    assert 'SKIP' in system  # 부실 답변 스킵 규칙 유지


def test_follow_up_messages_default_generate_bias():
    """꼬리질문 기본값은 '생성' — 애매하면 생성하고, 사실상 빈 답변만 SKIP 한다."""
    system = prompts.follow_up_messages('직전 질문', '지원자 답변', TECH)[0]['content']
    assert '애매하면 SKIP 하지 말고 생성' in system


def test_report_messages_embeds_scoring_rubric():
    """결과 리포트 프롬프트에 채점 구간·감점 규칙(루브릭)이 실린다."""
    system = prompts.report_messages('면접 기록', '백엔드')[0]['content']
    assert prompts.SCORING_RUBRIC in system
    assert '무조건 0점' in system  # 무응답 0점 규칙


def test_evaluation_messages_embeds_scoring_rubric():
    """스트림 평가 피드백도 같은 채점 잣대로 톤을 맞춘다."""
    system = prompts.evaluation_messages('질문', '답변')[0]['content']
    assert prompts.SCORING_RUBRIC in system
