"""면접 프롬프트(app/interview/prompts.py) 단위 테스트 — 메인 질문 메시지 조립."""

from app.interview import prompts


def test_main_questions_messages_includes_count_and_self_intro_rule():
    """count 와 자기소개 규칙이 시스템 프롬프트에 들어간다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '', 4)
    system = messages[0]['content']
    assert '정확히 4개' in system
    assert '자기소개' in system


def test_main_questions_messages_omits_applicant_block_when_no_user_context():
    """지원자 정보가 없으면 user 메시지에 회사 컨텍스트만 담긴다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '', 4)
    user = messages[1]['content']
    assert '회사 컨텍스트' in user
    assert '지원자 정보' not in user


def test_main_questions_messages_appends_applicant_block_when_present():
    """지원자 정보가 있으면 user 메시지에 이력서 컨텍스트가 덧붙고, 개인화 규칙이 켜진다."""
    messages = prompts.main_questions_messages('회사 컨텍스트', '[이력서]\n보유 기술: Python', 4)
    system, user = messages[0]['content'], messages[1]['content']
    assert '개인화 질문' in system  # 개인화 규칙 존재
    assert '지원자 정보' in user
    assert '보유 기술: Python' in user
