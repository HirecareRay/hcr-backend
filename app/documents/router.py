from typing import Optional
from fastapi import APIRouter, File, UploadFile, Request, Form
from app.db.mongo import get_mongo_db
from app.documents.service import parse_individual_document

# ponytail: 문서 도메인을 위한 전용 라우터를 선언합니다.
router = APIRouter(prefix="/documents", tags=["documents"])
@router.post(
    "/upload",
    summary="지원자 문서 개별 파싱 및 저장 API",
    description="이력서, 자기소개서, 포트폴리오, 경력기술서 중 실재 업로드된 PDF 파일들만 개별 파싱하여 MongoDB user_documents 컬렉션의 최상위 키값에 적재합니다.",
    response_description="파싱된 문서 유형 목록 및 데이터 요약본 반환"
)
async def parse_documents(
    request: Request,
    # ⚠️ 배포 시 실적용 예정인 Form user_id 수신 파라미터 (현재 개발 테스트 단계를 위해 주석 처리)
    # user_id: str = Form(...),
    resume: Optional[UploadFile] = File(None),
    cover_letter: Optional[UploadFile] = File(None),
    portfolio: Optional[UploadFile] = File(None),
    work_experience: Optional[UploadFile] = File(None),
):
    # ponytail: 개발 및 검증 편의를 위해 user_id를 "1"로 고정합니다.
    user_id = "1"
    
    # MongoDB 데이터베이스 핸들 획득
    db = get_mongo_db(request)
    parsed_results = {}
    
    # 각 문서가 업로드된 경우에만 매칭되는 LLM 파서 호출 진행
    if resume is not None:
        parsed_results["resume"] = parse_individual_document(db, user_id, "resume", resume)
        
    if cover_letter is not None:
        parsed_results["cover_letter"] = parse_individual_document(db, user_id, "cover_letter", cover_letter)
        
    if portfolio is not None:
        # 포트폴리오는 최종 스키마 스펙의 projects 리스트로 파싱 처리
        parsed_results["projects"] = parse_individual_document(db, user_id, "portfolio", portfolio)
        
    if work_experience is not None:
        parsed_results["work_experience"] = parse_individual_document(db, user_id, "work_experience", work_experience)
        
    return {
        "status": "success",
        "user_id": user_id,
        "parsed_fields": list(parsed_results.keys()),
        "data": parsed_results
    }
