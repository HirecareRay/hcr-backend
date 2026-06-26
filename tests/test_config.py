"""앱 설정(Settings) 검증 테스트 — 범위 제약이 잘못된 값을 거르는지 확인."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_main_question_count_rejects_below_min():
    """0·음수면 첫 질문 송신이 깨지므로 ge=1 로 거른다."""
    with pytest.raises(ValidationError):
        Settings(interview_main_question_count=0)


def test_main_question_count_rejects_above_max():
    """과도한 값은 토큰 비용이 급증하므로 le=10 으로 거른다."""
    with pytest.raises(ValidationError):
        Settings(interview_main_question_count=50)


def test_main_question_count_accepts_in_range():
    """1~10 범위는 통과한다."""
    assert Settings(interview_main_question_count=4).interview_main_question_count == 4
