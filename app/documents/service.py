from datetime import datetime
import os
import fitz
import json
import logging
from pymongo.database import Database
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from fastapi import UploadFile

from app.core.config import settings
from app.documents.schemas import ResumeRoute, CoverLetterRoute, PortfolioRoute, WorkExperiencesRoute 
from app.documents.repository import save_document_field

logger = logging.getLogger(__name__)
base_rule = """
1. PDF에서 추출된 텍스트를 분석한다.
2. 오타 자동 수정
3. 띄어쓰기 자동 수정
4. 날짜 형식 통일

YYYY-MM-DD
YYYY-MM
YYYY

5. 없는 값은 null
6. 추론 금지
7. 중복 제거
8. 모든 정보를 구조화
9. 최대한 상세하게 추출."""

# ponytail: 잘못된 프롬프트(문서 관련 없음, 텍스트 훼손 등) 감지 시 LLM이 탈출할 수 있도록 지정하는 공통 예외 규칙입니다.
exception_rule = """
[중요 - 부적절한 문서 필터링 규칙]
1. 분석할 문서 본문이 비어있거나, 해당 구직 서류 종류와 전혀 무관한 텍스트(예: 상식 질문, 뉴스, 잡담, 코딩 등)인 경우 절대 데이터를 채우지 마십시오.
2. 위와 같이 문서 생성 및 파싱 규칙에 위배되는 부적절한 요청/텍스트가 유입되면, 무조건 'InvalidRequestNotice' 구조를 선택하여 response_type을 'fail'로 지정하고 그 이유와 해결 방법을 반환해야 합니다.
3. suggestion에는 doc_type을 참고하여 답하시오. """

def extract_text_from_upload_file(file: UploadFile) -> str:
    """
    FastAPI UploadFile 객체에서 바이너리 데이터를 직접 메모리로 읽어와 
    PyMuPDF(fitz)를 이용해 텍스트를 디코딩 및 추출합니다.
    """
    try:
        file_bytes = file.file.read()
        file.file.seek(0)  # 읽은 후 스트림 커서를 처음으로 리셋합니다
        
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        texts = []
        for page in doc:
            texts.append(page.get_text())
        return "\n".join(texts)
    except Exception as e:
        logger.error(f"업로드 파일 '{file.filename}'에서 텍스트 추출 중 오류가 발생했습니다: {e}")
        return ""

def parse_individual_document(
    db: Database,
    user_id: str,
    doc_type: str,
    file: UploadFile
) -> dict | list:
    """
    업로드된 개별 문서(이력서, 자기소개서, 포트폴리오, 경력기술서)에 매칭되는
    전용 LLM 체인을 구성하여 개별 파싱 및 MongoDB 적재를 수행합니다.
    """
    text = extract_text_from_upload_file(file)
    if not text.strip():
        logger.warning(f"'{doc_type}' 문서의 추출된 텍스트 내용이 비어있어 파싱을 중단합니다.")
        return {}

    # ChatOpenAI 초기화 진행
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=settings.openai_api_key
    )

    # ponytail: 4가지 문서 종류별로 전용 프롬프트 및 스키마 스펙을 최적화하여 1개만 와도 처리 가능하게 작성합니다.
    if doc_type == "resume":
        target_schema = ResumeRoute
        system_instruction = f"""
당신은 이력서(Resume)를 전문 분석하는 ATS 이력서 파서입니다.
doc_type: {doc_type}
규칙
{base_rule}
{exception_rule}
"""
        mongo_field = "resume"

    elif doc_type == "cover_letter":
        target_schema = CoverLetterRoute
        system_instruction = f"""
당신은 자기소개서(Cover Letter)를 전문 분석하는 ATS 자기소개서 파서입니다.
doc_type: {doc_type}

규칙
{base_rule}
개별 규칙
1. 자기소개서는 문단별 category/title/content 추출.
{exception_rule}
"""
        mongo_field = "cover_letter"

    elif doc_type == "portfolio":
        target_schema = PortfolioRoute
        system_instruction = f"""
당신은 포트폴리오(Portfolio)를 분석하는 ATS 프로젝트 파서입니다.
doc_type: {doc_type}

규칙
{base_rule}
{exception_rule}
"""
        # ponytail: 포트폴리오 분석 결과는 최종 데이터 모델 스펙에 맞게 'projects' 필드 키값으로 저장합니다.
        mongo_field = "projects"

    elif doc_type == "work_experience":
        target_schema = WorkExperiencesRoute
        system_instruction = f"""
당신은 상세 경력기술서(Work Experience)를 전문 분석하는 ATS 경력기술서 파서입니다.
doc_type: {doc_type}

규칙
{base_rule}
{exception_rule}
"""
        mongo_field = "work_experience"
    else:
        raise ValueError(f"지원하지 않는 문서 종류입니다: {doc_type}")

    # 구조화된 출력 체인 구성
    structured_llm = llm.with_structured_output(target_schema)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_instruction),
        ("human", f"유저 ID: {user_id}\n\n[분석할 문서 본문]\n{{text}}")
    ])
    chain = prompt | structured_llm

    logger.info(f"'{doc_type}' 문서 분석을 위해 OpenAI 호출을 진행합니다.")
    result = chain.invoke({"text": text})
    
    logger.info(f"'{doc_type}' 문서 분석을 위해 OpenAI 호출을 진행합니다.")
    result = chain.invoke({"text": text})
    
    # ponytail: 새롭게 도입된 Route 스키마의 결과 데이터를 직접 꺼내어 검증합니다. (구조가 단순해져 result.output 대신 result 자체를 사용)
    # result_dict 변환 작업 진행
    result_dict = result.model_dump(mode="json")

    # [검증 및 분기] 잘못된 문서 양식이거나 부적절한 프롬프트 텍스트 유입으로 파싱에 실패한 경우
    if result.response_type == "fail":
        logger.error(f"'{doc_type}' 문서의 부적절한 요청이 감지되어 필터링되었습니다. 사유: {result.reason}")
        return {
            "success": False,
            "error_message": result.reason,
            "suggestion": result.suggestion
        }

    # [검증 및 분기] 문서 추출에 정상적으로 성공한 경우 하위 래퍼에서 핵심 순수 데이터만 가공
    # 스키마 Wrapper의 계층 구조를 단순화하여 데이터만 추출
    if doc_type == "resume":
        final_data = result_dict.get("resume") or {}
        final_data.update({"created_datetime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    elif doc_type == "cover_letter":
        final_data = result_dict.get("cover_letter") or {}
        final_data.update({"created_datetime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    elif doc_type == "portfolio":
        # portfolio_success 라우팅 래퍼 내부의 portfolio 스키마 객체에서 다시 projects 리스트를 추출합니다.
        portfolio_obj = result_dict.get("portfolio") or {}
        final_data = portfolio_obj.get("projects") or []
        if final_data:
            final_data = {"portfolio": final_data}
            final_data.update({"created_datetime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    elif doc_type == "work_experience":
        # work_experiences_success 라우팅 래퍼 내부의 work_experiences 스키마 객체에서 다시 work_experience 리스트를 추출합니다.
        work_exp_obj = result_dict.get("work_experiences") or {}
        final_data = work_exp_obj.get("work_experience") or []
        if final_data:
            final_data = {"work_experience": final_data}
            final_data.update({"created_datetime": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    else:
        final_data = result_dict


    # # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # # [개발 중 테스트 확인용 로컬 파일 저장 블록]
    # # ⚠️ 나중에 운영 배포 시 이 블록 전체를 삭제하시기 편리하도록 표시해 둡니다.
    # try:
    #     outputs_dir = os.path.join(os.path.dirname(__file__), "outputs")
    #     os.makedirs(outputs_dir, exist_ok=True)
    #     local_file_path = os.path.join(outputs_dir, f"parsed_{user_id}_{mongo_field}.json")
    #     with open(local_file_path, "w", encoding="utf-8") as f:
    #         json.dump(final_data, f, ensure_ascii=False, indent=2)
    #     logger.info(f"[개발용 확인 로그] 분석 결과를 로컬에 임시 저장했습니다: {local_file_path}")
    # except Exception as file_err:
    #     logger.warning(f"로컬 임시 파일 백업 실패 (파싱 흐름엔 영향 없음): {file_err}")
    # # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # MongoDB 특정 최상위 필드에 적재
    if db is not None:
        save_document_field(db, user_id, mongo_field, final_data)
    else:
        logger.warning("MongoDB 데이터베이스 연결이 불가능하여 DB 저장을 건너뛰었습니다.")

    return final_data
