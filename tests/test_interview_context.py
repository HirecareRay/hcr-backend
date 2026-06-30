"""면접 컨텍스트 공급자(app/interview/context.py) 단위 테스트.

회사 분석·지원자 문서를 면접관 컨텍스트로 합치는 경계. DB·외부 모듈은 monkeypatch
로 대체해 실 연결·실 OpenAI 호출 없이 검증한다. async 함수는 asyncio.run 으로 실행.
"""

import asyncio

from app.interview import context


# ── get_company_context ────────────────────────────────────────────


def test_company_context_empty_without_db():
    """db·mongo·company_id 가 없으면 빈 문자열(가짜 회사 끼우지 않는다)."""
    result = asyncio.run(context.get_company_context())
    assert result == ''


def test_company_context_empty_when_company_id_missing():
    """db·mongo 가 있어도 company_id 가 비면 빈 문자열로 우회한다."""
    result = asyncio.run(
        context.get_company_context(db=object(), mongo=object(), company_id='')
    )
    assert result == ''


def test_company_context_delegates_to_company_service(monkeypatch):
    """식별자가 다 있으면 company.service 의 실데이터 컨텍스트를 사용한다."""
    from app.company import service as company_service

    monkeypatch.setattr(
        company_service, 'build_interview_context', lambda db, mongo, cid: '회사명: CJ ENM'
    )
    result = asyncio.run(
        context.get_company_context(db=object(), mongo=object(), company_id='abc123')
    )
    assert result == '회사명: CJ ENM'


def test_company_context_falls_back_to_empty_on_error(monkeypatch):
    """회사 조회·조립이 실패하면 빈 문자열로 우회한다(면접 안 끊김)."""
    from app.company import service as company_service

    def _boom(db, mongo, cid):
        raise RuntimeError('db down')

    monkeypatch.setattr(company_service, 'build_interview_context', _boom)
    result = asyncio.run(
        context.get_company_context(db=object(), mongo=object(), company_id='abc123')
    )
    assert result == ''


# ── get_user_context ───────────────────────────────────────────────


def test_user_context_empty_without_mongo_or_user():
    """mongo·user_id 가 없으면 빈 문자열(개인화 생략)."""
    assert asyncio.run(context.get_user_context()) == ''
    assert asyncio.run(context.get_user_context(mongo=object(), user_id='')) == ''


def test_user_context_empty_when_no_document(monkeypatch):
    """유저 문서가 없으면 빈 문자열."""
    from app.documents import repository

    monkeypatch.setattr(repository, 'find_user_documents', lambda db, uid: None)
    assert asyncio.run(context.get_user_context(mongo=object(), user_id='u1')) == ''


def test_user_context_formats_document(monkeypatch):
    """이력서·포트폴리오 문서를 면접용 텍스트로 직렬화한다."""
    from app.documents import repository

    document = {
        'resume': {
            'tools_skills': [{'name': 'Python'}, {'name': 'FastAPI'}],
            'career': [{'name': '카카오', 'position': '백엔드'}],
            'awards': [{'name': '해커톤 대상'}],
            'certifications': [{'name': '정보처리기사'}],
        },
        'projects': {
            'portfolio': [
                {'name': 'HCR', 'role': '리드', 'description': '실시간 면접 백엔드'}
            ]
        },
        'cover_letter': {
            'items': [{'title': '지원동기', 'content': '성장하는 회사에서 일하고 싶습니다'}]
        },
        'work_experience': {
            'work_experience': [
                {
                    'company_name': '네이버',
                    'position': '백엔드 개발자',
                    'start_date': '2021-01',
                    'end_date': '2023-12',
                    'responsibilities': ['검색 API 개발', '대용량 트래픽 처리'],
                }
            ]
        },
    }
    monkeypatch.setattr(repository, 'find_user_documents', lambda db, uid: document)

    result = asyncio.run(context.get_user_context(mongo=object(), user_id='u1'))

    assert 'Python' in result and 'FastAPI' in result
    assert '카카오 백엔드' in result
    assert '해커톤 대상' in result
    assert '정보처리기사' in result
    assert 'HCR / 리드: 실시간 면접 백엔드' in result
    assert '지원동기' in result
    assert '[경력기술서]' in result
    assert '네이버' in result and '백엔드 개발자' in result
    assert '검색 API 개발' in result


def test_user_context_falls_back_to_empty_on_error(monkeypatch):
    """문서 조회가 예외를 던져도 빈 문자열로 우회한다(면접 안 끊김)."""
    from app.documents import repository

    def _boom(db, uid):
        raise RuntimeError('mongo down')

    monkeypatch.setattr(repository, 'find_user_documents', _boom)
    assert asyncio.run(context.get_user_context(mongo=object(), user_id='u1')) == ''


# ── 포맷터(순수 함수) ──────────────────────────────────────────────


def test_format_resume_skips_empty_fields():
    """빈/None 필드는 줄을 만들지 않고, 내용이 하나도 없으면 빈 문자열."""
    assert context._format_resume({}) == ''
    assert context._format_resume(None) == ''
    resume = {'tools_skills': [{'name': 'Go'}], 'career': [], 'awards': None}
    out = context._format_resume(resume)
    assert out == '[이력서]\n보유 기술: Go'


def test_format_projects_limits_and_truncates():
    """프로젝트는 상위 limit 개만, 설명은 120자로 절단한다."""
    projects = [{'name': f'P{i}', 'description': 'x' * 200} for i in range(10)]
    out = context._format_projects(projects, limit=2)
    assert out.count('\n- ') == 2  # limit=2 → 항목 2개만
    assert 'P2' not in out  # 상위 2개(P0·P1)만 — limit 초과분 제외
    assert out.startswith('[포트폴리오 프로젝트]')
    assert 'x' * 120 in out and 'x' * 121 not in out


def test_format_cover_letter_extracts_titled_snippets():
    """자기소개서는 제목+본문 발췌(최대 limit개)만 뽑는다."""
    items = [{'title': '성장과정', 'content': '꾸준함'}, {'category': '장단점', 'content': '집중력'}]
    out = context._format_cover_letter(items, limit=5)
    assert '성장과정: 꾸준함' in out
    assert '장단점: 집중력' in out  # title 없으면 category 사용


def test_format_work_experience_extracts_company_role_and_tasks():
    """경력기술서는 회사·직무·기간 + 주요 업무 발췌를 상위 limit 개만 뽑는다."""
    assert context._format_work_experience(None) == ''
    assert context._format_work_experience([]) == ''
    items = [
        {
            'company_name': '카카오',
            'position': '서버 개발자',
            'start_date': '2020-03',
            'end_date': '2022-02',
            'responsibilities': ['결제 시스템 운영', '장애 대응'],
        }
    ]
    out = context._format_work_experience(items)
    assert out.startswith('[경력기술서]')
    assert '카카오 / 서버 개발자' in out
    assert '2020-03 ~ 2022-02' in out
    assert '결제 시스템 운영' in out


def test_format_work_experience_limits_and_truncates():
    """경력은 상위 limit 개만, 업무 요약은 120자로 절단한다."""
    items = [
        {'company_name': f'C{i}', 'responsibilities': ['x' * 200]} for i in range(10)
    ]
    out = context._format_work_experience(items, limit=2)
    assert out.count('\n- ') == 2
    assert 'C2' not in out
    assert 'x' * 120 in out and 'x' * 121 not in out


def test_format_user_context_joins_present_sections(monkeypatch):
    """존재하는 섹션만 줄바꿈으로 잇는다(없는 섹션은 생략)."""
    document = {'resume': {'tools_skills': [{'name': 'Rust'}]}}
    out = context._format_user_context(document)
    assert out == '[이력서]\n보유 기술: Rust'  # 포폴·자소서·경력기술서 없음 → 이력서만
