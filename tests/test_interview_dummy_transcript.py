"""더미 자막 생성기 단위 테스트 — 순수 함수, 외부 호출 없음(비용 0).

토큰 순환·누적 답변 텍스트 규칙을 검증한다. 실시간 흐름(청크당 부분 자막)은
test_interview_ws.py 의 더미 모드 왕복 테스트에서 검증한다.
"""

from app.interview import dummy_transcript


def test_token_at_returns_nonempty_token():
    assert dummy_transcript.token_at(0)


def test_token_at_cycles_when_index_exceeds_pool():
    """풀 길이를 넘어도 처음부터 순환해 같은 토큰이 다시 나온다(긴 답변 안전)."""
    pool_size = len(dummy_transcript._DUMMY_TOKENS)
    assert dummy_transcript.token_at(0) == dummy_transcript.token_at(pool_size)
    assert dummy_transcript.token_at(3) == dummy_transcript.token_at(pool_size + 3)


def test_consecutive_tokens_differ():
    """이어지는 청크는 서로 다른 토큰이라 자막이 '차오르는' 효과가 난다."""
    assert dummy_transcript.token_at(0) != dummy_transcript.token_at(1)


def test_answer_text_empty_for_zero_chunks():
    assert dummy_transcript.answer_text(0) == ''


def test_answer_text_joins_and_strips_tokens():
    """누적 답변은 토큰들을 이어 붙이고 양끝 공백을 제거한 텍스트다."""
    text = dummy_transcript.answer_text(3)
    expected = ''.join(dummy_transcript.token_at(i) for i in range(3)).strip()
    assert text == expected
    assert text == text.strip()  # 양끝 공백 없음
    assert dummy_transcript.token_at(0).strip() in text
