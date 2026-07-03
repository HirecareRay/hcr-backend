"""company.service.build_interview_context 단위 테스트.

build_company_report(8섹션 dict)에서 면접용 요약 텍스트를 추리는 변환만 검증한다.
리포트 조립(DB 조회)은 monkeypatch 로 대체해 DB 없이 변환 로직만 확인한다.
"""

from app.company import service


def _fake_report() -> dict:
    """build_company_report 가 돌려주는 형태의 최소 리포트(필요 섹션만 채움)."""
    return {
        'company': {'name': 'CJ ENM', 'industry': '미디어/엔터테인먼트'},
        'overview': {'businessDescription': '콘텐츠 제작과 커머스를 운영한다'},
        'financial': {'summary': '매출 성장세 지속'},
        'growth': {
            'summary': '글로벌 콘텐츠 확장 중',
            'news': [{'title': '신작 드라마 흥행'}, {'title': '해외 진출 가속'}, {'title': ''}],
        },
        'hiring': {'openings': [{'title': '백엔드 엔지니어'}, {'title': '데이터 분석가'}]},
        'insight': {
            'keyPoints': ['콘텐츠 IP 강점', '글로벌 확장'],
            'swot': {'strengths': ['강력한 IP', '제작 역량']},
        },
    }


def test_build_interview_context_extracts_summary_fields(monkeypatch):
    """리포트의 요약 필드를 라벨 붙은 텍스트로 직렬화한다."""
    monkeypatch.setattr(service, 'build_company_report', lambda db, mongo, cid: _fake_report())

    text = service.build_interview_context(object(), object(), 'abc123')

    assert '회사명: CJ ENM' in text
    assert '업종: 미디어/엔터테인먼트' in text
    assert '사업 개요: 콘텐츠 제작과 커머스를 운영한다' in text
    assert '핵심 포인트: 콘텐츠 IP 강점 / 글로벌 확장' in text
    assert '재무 요약: 매출 성장세 지속' in text
    assert '성장성 요약: 글로벌 콘텐츠 확장 중' in text
    assert '최근 뉴스: 신작 드라마 흥행 / 해외 진출 가속' in text  # 빈 제목 제외
    assert '채용 중 포지션: 백엔드 엔지니어 / 데이터 분석가' in text
    assert '강점(SWOT): 강력한 IP / 제작 역량' in text


def test_build_interview_context_omits_empty_sections(monkeypatch):
    """비어 있는 섹션은 줄을 만들지 않는다(회사명만 남는다)."""
    sparse = {
        'company': {'name': '스타트업', 'industry': ''},
        'overview': {'businessDescription': ''},
        'financial': {'summary': ''},
        'growth': {'summary': '', 'news': []},
        'hiring': {'openings': []},
        'insight': {'keyPoints': [], 'swot': {'strengths': []}},
    }
    monkeypatch.setattr(service, 'build_company_report', lambda db, mongo, cid: sparse)

    text = service.build_interview_context(object(), object(), 'x')

    assert text == '회사명: 스타트업'
