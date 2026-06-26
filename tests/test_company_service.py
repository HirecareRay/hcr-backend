"""company.service 의 JSON 파싱 헬퍼 단위 테스트.

company_analyses 의 longtext(JSON) 컬럼을 FE 계약 모양으로 뽑는 부분이
비자명 로직이라 여기만 검증한다. (DB 조립 전체는 엔드포인트로 확인)
"""

from app.company.service import _summary, _texts


def test_summary_extracts_field():
    assert _summary('{"summary": "안녕"}') == "안녕"


def test_summary_handles_empty_and_bad():
    assert _summary(None) == ""
    assert _summary("") == ""
    assert _summary("{not json") == ""
    assert _summary('{"other": 1}') == ""  # summary 키 없음


def test_texts_extracts_text_list():
    raw = '[{"text": "a", "evidence_type": "news"}, {"text": "b"}]'
    assert _texts(raw) == ["a", "b"]


def test_texts_handles_empty_and_bad():
    assert _texts(None) == []
    assert _texts("") == []
    assert _texts("[bad") == []
    assert _texts('["not a dict"]') == []  # dict 아닌 원소는 건너뜀
