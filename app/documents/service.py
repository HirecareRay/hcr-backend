import os
import fitz
import json
import logging
from pymongo.database import Database
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from fastapi import UploadFile

from app.core.config import settings
from app.documents.schemas import Resume, CoverLetter, Portfolio, WorkExperiences
from app.documents.repository import save_document_field

logger = logging.getLogger(__name__)

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
        target_schema = Resume
        system_instruction = """
당신은 이력서(Resume)를 전문 분석하는 ATS 이력서 파서입니다.
제공된 이력서 텍스트에서 학력 정보, 간단한 경력 사항, 자격증, 수상 경력, 교육 이수, 보유 기술 스택을 정확하게 추출하여 JSON 구조로 반환하십시오.
오타와 띄어쓰기를 자동으로 적절하게 교정하며, 날짜 포맷은 YYYY-MM-DD, YYYY-MM, YYYY 중 하나로 통일하고 없는 값은 null 처리하십시오.
"""
        mongo_field = "resume"

    elif doc_type == "cover_letter":
        target_schema = CoverLetter
        system_instruction = """
당신은 자기소개서(Cover Letter)를 전문 분석하는 ATS 자기소개서 파서입니다.
제공된 자기소개서 텍스트에서 문단별 주제에 따라 카테고리(category), 제목(title), 내용(content)을 정확하게 분류하고 구분하여 리스트 형태로 반환하십시오.
비정상적인 띄어쓰기와 맞춤법 오타를 자동으로 교정하고 없는 값은 null 처리하십시오.
"""
        mongo_field = "cover_letter"

    elif doc_type == "portfolio":
        target_schema = Portfolio
        system_instruction = """
당신은 포트폴리오(Portfolio)를 분석하는 ATS 프로젝트 파서입니다.
포트폴리오 파일에 나열된 모든 개별 프로젝트들을 분석하여 프로젝트명, 참여 인원, 기간, 사용 기술 스택, 담당 역할, 주요 기여 내용 및 상세 성과를 정확히 추출하여 projects 리스트 구조로 반환하십시오.
날짜 형식을 통일하고, 임의의 지레짐작 및 과장 추론을 엄격히 금지합니다.
"""
        # ponytail: 포트폴리오 분석 결과는 최종 데이터 모델 스펙에 맞게 'projects' 필드 키값으로 저장합니다.
        mongo_field = "projects"

    elif doc_type == "work_experience":
        target_schema = WorkExperiences
        system_instruction = """
당신은 상세 경력기술서(Work Experience)를 전문 분석하는 ATS 경력기술서 파서입니다.
각 재직 기업별 부서명, 직책, 재직 기간, 주요 담당 업무 리스트, 그리고 참여 프로젝트 정보(수행 기간, 내용, 성과 포함)를 정밀 분석하여 리스트 구조로 반환하십시오.
기본 이력서의 약식 경력 정보와 혼동하지 않도록 상세 내용들을 추출해 내야 합니다.
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
    result_dict = result.model_dump(mode="json")

    # 스키마 Wrapper의 계층 구조를 단순화하여 데이터만 추출
    if doc_type == "portfolio":
        final_data = result_dict.get("projects") or []
    elif doc_type == "work_experience":
        final_data = result_dict.get("work_experience") or []
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
