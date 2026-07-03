"""면접관 3인 페르소나(app/interview/personas.py) 단위 테스트.

데이터·배정 규칙만 검증한다(LLM·WS 미개입). 라운드로빈 배정과 id 폴백이 핵심.
"""

from app.interview import personas
from app.interview.personas import CULTURE, PANEL, PRACTICAL, TECH


def test_panel_has_three_distinct_interviewers():
    """패널은 인사·기술·실무 3인이며 id 가 모두 다르다."""
    assert PANEL == (CULTURE, TECH, PRACTICAL)
    assert len({p.id for p in PANEL}) == 3
    assert [p.role_label for p in PANEL] == ['인사담당자', '기술담당자', '실무담당자']


def test_assign_interviewers_starts_with_culture_then_rotates():
    """Q1=인사(자기소개), 이후 기술→실무→인사 라운드로빈."""
    assigned = personas.assign_interviewers(5)
    assert [p.id for p in assigned] == [
        'culture_fit', 'tech_pressure', 'practical', 'culture_fit', 'tech_pressure'
    ]


def test_assign_interviewers_exact_length():
    """반환 길이는 요청 슬롯 수와 정확히 같다."""
    assert len(personas.assign_interviewers(1)) == 1
    assert len(personas.assign_interviewers(3)) == 3
    assert personas.assign_interviewers(1) == [CULTURE]


def test_assign_interviewers_zero_or_negative_is_empty():
    """슬롯이 0 이하면 빈 배정(호출부 폴백 경로 방어)."""
    assert personas.assign_interviewers(0) == []
    assert personas.assign_interviewers(-3) == []


def test_persona_by_id_resolves_known_ids():
    """알려진 id 는 해당 페르소나로 복원된다."""
    assert personas.persona_by_id('tech_pressure') is TECH
    assert personas.persona_by_id('practical') is PRACTICAL


def test_persona_by_id_falls_back_to_culture():
    """모르는 id 는 진행자 CULTURE 로 폴백한다(데모 안전)."""
    assert personas.persona_by_id('unknown') is CULTURE
    assert personas.persona_by_id('') is CULTURE


def test_persona_is_frozen():
    """페르소나는 불변(전역 상수)이라 필드를 바꿀 수 없다."""
    import dataclasses

    try:
        CULTURE.role_label = '변경 시도'  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError('frozen 이어야 한다')
