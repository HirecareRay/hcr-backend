"""면접 컨텍스트 공급자 — 회사·지원자 정보를 LLM 면접관에 주입하는 유일한 경계.

질문 생성에 두 소스를 합친다 — (1) 회사 분석(실데이터)과 (2) 로그인 지원자의
문서 4종(이력서·자기소개서·포트폴리오·경력기술서). 어느 쪽이 비어도 면접이
끊기지 않도록 조회 실패·없음은 빈 문자열로 우회한다 — 가짜 컨텍스트를 끼워넣지
않는다(있는 데이터만큼만 개인화, 셋 다 없으면 호출부가 기본질문으로 폴백).

DB·외부 모듈 의존은 함수 안에서 지연 import 한다 — DB 미연결 환경(로컬·CI·기존
면접 테스트)에서 모듈 import 만으로 실 연결을 요구하지 않게 하기 위함이다.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.database import Database
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# LLM 질문 생성이 실패해도 면접이 끊기지 않도록 쓰는 안전 기본 질문.
FALLBACK_MAIN_QUESTIONS: tuple[str, ...] = (
    '간단히 자기소개 부탁드립니다.',
    '이 직무에 지원하신 동기는 무엇인가요?',
    '가장 자신 있는 기술과 그것을 활용한 경험을 말씀해 주세요.',
    '마지막으로 하고 싶은 말씀이 있나요?',
)


async def get_company_context(
    db: "Session | None" = None,
    mongo: "Database | None" = None,
    company_id: str | None = None,
) -> str:
    """면접관에 주입할 회사 컨텍스트를 반환한다.

    db·mongo·company_id 가 모두 주어지면 실제 기업 분석 데이터로 컨텍스트를 만든다.
    하나라도 없거나 조회·조립에 실패하면 빈 문자열로 우회한다 — 가짜 회사를 끼워넣지
    않는다(회사 컨텍스트 없이 진행하거나, 셋 다 없으면 호출부가 기본질문으로 폴백).
    """
    if db is None or mongo is None or not company_id:
        return ''
    try:
        from app.company import service as company_service

        context = company_service.build_interview_context(db, mongo, company_id)
        return context or ''
    except Exception as error:  # noqa: BLE001 - 회사 조회 실패가 면접을 막지 않게
        logger.error('회사 컨텍스트 조회 실패, 회사 컨텍스트 생략: %s', error)
        return ''


async def get_user_context(
    mongo: "Database | None" = None,
    user_id: str | None = None,
) -> str:
    """지원자(로그인 유저)의 이력서·포트폴리오를 면접관용 텍스트로 반환한다.

    mongo·user_id 가 없거나 문서가 없으면 빈 문자열(개인화 없이 회사 기반 질문).
    조회·포맷 실패가 면접을 막지 않도록 예외는 빈 문자열로 우회한다(데모 보호).
    """
    if mongo is None or not user_id:
        return ''
    try:
        from app.documents.repository import find_user_documents

        document = find_user_documents(mongo, user_id)
        return _format_user_context(document) if document else ''
    except Exception as error:  # noqa: BLE001 - 유저 문서 조회 실패가 면접을 막지 않게
        logger.error('유저 컨텍스트 조회 실패, 개인화 생략: %s', error)
        return ''


def _format_user_context(document: dict) -> str:
    """user_documents 문서를 면접 질문 생성용 간결 텍스트로 직렬화한다.

    문서 4종(이력서·포트폴리오·자기소개서·경력기술서)의 핵심만 추린다(전문은 토큰
    낭비). 포트폴리오는 'projects' 필드 안 'portfolio' 키, 경력기술서는
    'work_experience' 필드 안 'work_experience' 키에 리스트로 담기는 구조를 따른다.
    올라온 섹션만 잇고, 4종 모두 없으면 빈 문자열(가짜로 채우지 않는다).
    """
    sections = [
        _format_resume(document.get('resume')),
        _format_projects((document.get('projects') or {}).get('portfolio')),
        _format_cover_letter((document.get('cover_letter') or {}).get('items')),
        _format_work_experience(
            (document.get('work_experience') or {}).get('work_experience')
        ),
    ]
    return '\n'.join(section for section in sections if section)


def _names(items: Any, key: str = 'name') -> list[str]:
    """dict 리스트에서 특정 키의 비어 있지 않은 값만 모은다."""
    return [
        str(item[key]).strip()
        for item in (items or [])
        if isinstance(item, dict) and item.get(key) and str(item[key]).strip()
    ]


def _format_resume(resume: Any) -> str:
    """이력서 dict 에서 기술·경력·수상·자격증 요약을 뽑는다(없으면 빈 문자열)."""
    if not isinstance(resume, dict):
        return ''
    lines: list[str] = []
    skills = _names(resume.get('tools_skills'))
    if skills:
        lines.append('보유 기술: ' + ', '.join(skills))
    careers = [
        ' '.join(part for part in (c.get('name'), c.get('position')) if part)
        for c in (resume.get('career') or [])
        if isinstance(c, dict) and (c.get('name') or c.get('position'))
    ]
    if careers:
        lines.append('경력: ' + ', '.join(careers))
    awards = _names(resume.get('awards'))
    if awards:
        lines.append('수상: ' + ', '.join(awards))
    certs = _names(resume.get('certifications'))
    if certs:
        lines.append('자격증: ' + ', '.join(certs))
    return '[이력서]\n' + '\n'.join(lines) if lines else ''


def _format_projects(projects: Any, limit: int = 5) -> str:
    """포트폴리오 프로젝트 리스트에서 이름·역할·설명을 상위 limit 개만 뽑는다."""
    if not isinstance(projects, list):
        return ''
    lines: list[str] = []
    for project in projects[:limit]:
        if not isinstance(project, dict):
            continue
        head = ' / '.join(
            part for part in (project.get('name'), project.get('role')) if part
        )
        desc = (project.get('description') or project.get('content') or '').strip()
        if desc:
            head = f'{head}: {desc[:120]}' if head else desc[:120]
        if head:
            lines.append('- ' + head)
    return '[포트폴리오 프로젝트]\n' + '\n'.join(lines) if lines else ''


def _format_cover_letter(items: Any, limit: int = 3) -> str:
    """자기소개서 문항에서 제목·본문 발췌(앞 120자)를 상위 limit 개만 뽑는다."""
    if not isinstance(items, list):
        return ''
    lines: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        content = (item.get('content') or '').strip()
        if not content:
            continue
        title = (item.get('title') or item.get('category') or '').strip()
        lines.append(f'- {title}: {content[:120]}' if title else f'- {content[:120]}')
    return '[자기소개서 발췌]\n' + '\n'.join(lines) if lines else ''


def _format_work_experience(items: Any, limit: int = 3) -> str:
    """경력기술서에서 회사·직무·기간과 주요 업무 요약을 상위 limit 개만 뽑는다.

    각 항목은 회사명·부서·직무·기간을 한 줄로 묶고, responsibilities 가 있으면
    앞 일부를 120자로 절단해 덧붙인다(전문은 토큰 낭비). 내용이 없으면 빈 문자열.
    """
    if not isinstance(items, list):
        return ''
    lines: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        head = ' / '.join(
            part
            for part in (
                item.get('company_name'),
                item.get('department'),
                item.get('position'),
            )
            if part
        )
        period = ' ~ '.join(
            part for part in (item.get('start_date'), item.get('end_date')) if part
        )
        if period:
            head = f'{head} ({period})' if head else period
        responsibilities = [
            str(task).strip()
            for task in (item.get('responsibilities') or [])
            if str(task).strip()
        ]
        if responsibilities:
            tasks = ', '.join(responsibilities)[:120]
            head = f'{head}: {tasks}' if head else tasks
        if head:
            lines.append('- ' + head)
    return '[경력기술서]\n' + '\n'.join(lines) if lines else ''
