from typing import List, Optional, Union, Literal
from pydantic import BaseModel, ConfigDict, Field

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
    created_datetime: Optional[str] = None


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
    created_datetime: Optional[str] = None


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
    created_datetime: Optional[str] = None

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
    created_datetime: Optional[str] = None

# =============================================================
# [교정 완료] 잘못된 프롬프트 차단 및 개별 문서 생성 제어용 라우팅 스키마
# (OpenAI strict 모드 호환을 위해 Union 대신 단일 Optional 구조 채택)
# =============================================================

class ResumeRoute(BaseModel):
    """이력서 체인 전용 최종 통합 구조 분기 래퍼"""
    # success 또는 fail 상태를 모델이 명확히 선택하도록 유도합니다
    response_type: Literal["success", "fail"] = Field(
        ..., 
        description="이력서가 정상 추출되면 'success', 구직 서류와 무관한 잘못된 문서라면 'fail'로 지정하세요."
    )
    
    # [성공 데이터 필드]
    resume: Optional[Resume] = Field(None, description="성공적으로 빌드된 이력서 본문 데이터 (response_type이 'success'일 때만 채움)")
    
    # [실패 거절 필드]
    reason: Optional[str] = Field(None, description="문서를 생성하거나 처리할 수 없는 구체적인 이유 설명 (response_type이 'fail'일 때만 채움)")
    suggestion: Optional[str] = Field(None, description="사용자가 올바른 요청을 할 수 있도록 돕는 유도 가이드라인 문구 (response_type이 'fail'일 때만 채움)")


class CoverLetterRoute(BaseModel):
    """자기소개서 체인 전용 최종 통합 구조 분기 래퍼"""
    response_type: Literal["success", "fail"] = Field(
        ..., 
        description="자기소개서가 정상 추출되면 'success', 잘못된 문서라면 'fail'로 지정하세요."
    )
    cover_letter: Optional[CoverLetter] = Field(None, description="성공적으로 빌드된 자기소개서 데이터")
    reason: Optional[str] = Field(None, description="문서를 처리할 수 없는 구체적인 이유")
    suggestion: Optional[str] = Field(None, description="올바른 입력을 돕는 가이드라인")


class PortfolioRoute(BaseModel):
    """포트폴리오 체인 전용 최종 통합 구조 분기 래퍼"""
    response_type: Literal["success", "fail"] = Field(
        ..., 
        description="포트폴리오가 정상 추출되면 'success', 잘못된 문서라면 'fail'로 지정하세요."
    )
    portfolio: Optional[Portfolio] = Field(None, description="성공적으로 빌드된 포트폴리오 프로젝트 데이터")
    reason: Optional[str] = Field(None, description="문서를 처리할 수 없는 구체적인 이유")
    suggestion: Optional[str] = Field(None, description="올바른 입력을 돕는 가이드라인")


class WorkExperiencesRoute(BaseModel):
    """경력기술서 체인 전용 최종 통합 구조 분기 래퍼"""
    response_type: Literal["success", "fail"] = Field(
        ..., 
        description="경력기술서가 정상 추출되면 'success', 잘못된 문서라면 'fail'로 지정하세요."
    )
    work_experiences: Optional[WorkExperiences] = Field(None, description="성공적으로 빌드된 경력기술서 전체 데이터")
    reason: Optional[str] = Field(None, description="문서를 처리할 수 없는 구체적인 이유")
    suggestion: Optional[str] = Field(None, description="올바른 입력을 돕는 가이드라인")
