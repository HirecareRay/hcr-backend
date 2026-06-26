from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    """지정되지 않은 임의의 필드 유입을 방지하는 엄격한 베이스 모델"""
    model_config = ConfigDict(
        extra="forbid"
    )

# =========================
# 이력서 (Resume) 관련 모델
# =========================

class University(StrictModel):
    """학력 사항에 대응하는 검증 스키마"""
    name: Optional[str] = None
    score: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = None
    major: Optional[str] = None
    graduate: Optional[str] = None


class Career(StrictModel):
    """이력서 내 기재된 간략한 직장 경력 사항 검증 스키마"""
    name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    responsibilities: Optional[str] = None
    leaving_reason: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class Certification(StrictModel):
    """자격증 정보 검증 스키마"""
    name: Optional[str] = None
    organization: Optional[str] = None
    date: Optional[str] = None


class Award(StrictModel):
    """수상 내역 검증 스키마"""
    date: Optional[str] = None
    name: Optional[str] = None
    organization: Optional[str] = None
    description: Optional[str] = None


class Education(StrictModel):
    """교육 이수 사항 검증 스키마"""
    name: Optional[str] = None
    organization: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None


class ToolSkill(StrictModel):
    """기술 스택 및 역량 수준 정보 검증 스키마"""
    name: Optional[str] = None
    proficiency: Optional[str] = None


class Resume(StrictModel):
    """이력서 전체 구조를 취합하는 검증 스키마"""
    school: List[University] = None
    career: List[Career] = None
    certifications: List[Certification] = None
    awards: List[Award] = None
    education: List[Education] = None
    tools_skills: List[ToolSkill] = None
    modify_datetime: Optional[str] = None


# =========================
# 자기소개서 (Cover Letter) 관련 모델
# =========================

class CoverLetterItem(StrictModel):
    """자기소개서 내 개별 문항 문단 정보를 구성하는 검증 스키마"""
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None


class CoverLetter(StrictModel):
    """자기소개서 전체 문항들을 담는 검증 스키마"""
    items: List[CoverLetterItem] = None
    modify_datetime: Optional[str] = None


# =========================
# 프로젝트 (Project) 관련 모델
# =========================

class Etc(StrictModel):
    """기타 부가 커스텀 데이터 저장을 위한 검증 스키마"""
    custom_key: str | None = None
    custom_content: str | None = None

class Project(StrictModel):
    """개별 프로젝트 상세 정보 검증 스키마 (포트폴리오 등과 매핑)"""
    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    members: Optional[int] = None
    role: Optional[str] = None
    tools_skills: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    result: Optional[str] = None
    etc: Etc | None = None


# 포트폴리오 파싱을 통해 추출할 프로젝트 목록 스키마
class Portfolio(StrictModel):
    """포트폴리오 PDF 파싱을 전담할 독립 스키마 Wrapper"""
    projects: List[Project] = None


# =========================
# 경력기술서 (Work Experience) 관련 모델
# =========================

class WorkExperience(StrictModel):
    """경력기술서 내 특정 기업 경력과 수행 프로젝트 목록을 취합하는 검증 스키마"""
    company_name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current_job: Optional[bool] = None
    responsibilities: List[str] = None
    projects: List[Project] = None
    reason_for_leaving: Optional[str] = None

class WorkExperiences(StrictModel):
    """경력기술서 전체 파싱을 전담할 독립 스키마 Wrapper"""
    work_experience: List[WorkExperience] = None

# =========================
# 최종 통합 데이터 출력 모델
# =========================

class ApplicantDocument(StrictModel):
    """4개 문서 파싱 결과를 최종 병합 및 표현할 때 사용하는 종합 스키마"""
    user_id: str | None = None
    resume: Resume | None = None
    cover_letter: CoverLetter | None = None
    projects: List[Project] = None
    work_experience: List[WorkExperience] = None
    etc: Etc | None = None
