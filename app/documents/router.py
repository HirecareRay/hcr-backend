from typing import Optional
from fastapi import APIRouter, Body, File, Query, UploadFile, Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from app.db.mongo import get_mongo_db
from app.documents.service import parse_individual_document
from app.documents.repository import delete_document_field, save_document_field
from app.auth.security import decode_access_token

VALID_DOC_TYPES = {"resume", "cover_letter", "portfolio", "work_experience"}
# portfolio는 MongoDB에 'projects' 키로 저장됨
DOC_TYPE_TO_FIELD = {
    "resume": "resume",
    "cover_letter": "cover_letter",
    "portfolio": "projects",
    "work_experience": "work_experience",
}

# ponytail: 문서 도메인을 위한 전용 라우터를 선언합니다.
router = APIRouter(prefix="/documents", tags=["documents"])

# ➡️ [추가] HTTP Bearer 스키마 설정 (Swagger UI 보안 인터페이스 활성화)
security_scheme = HTTPBearer(auto_error=True)

async def get_current_user_id(cred: HTTPAuthorizationCredentials = Depends(security_scheme)) -> str:
    token = cred.credentials
    try:
        return decode_access_token(token)
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않거나 만료된 토큰입니다: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.post(
    "/upload",
    summary="지원자 문서 개별 파싱 및 저장 API",
    description="이력서, 자기소개서, 포트폴리오, 경력기술서 중 실재 업로드된 PDF 파일들만 개별 파싱하여 MongoDB user_documents 컬렉션의 최상위 키값에 적재합니다.",
    response_description="파싱된 문서 유형 목록 및 데이터 요약본 반환"
)
async def parse_documents(
    request: Request,
    # ⚠️ 배포 시 실적용 예정인 Form user_id 수신 파라미터 (현재 개발 테스트 단계를 위해 주석 처리)
    user_id: str = Depends(get_current_user_id),
    resume: Optional[UploadFile] = File(None),
    cover_letter: Optional[UploadFile] = File(None),
    portfolio: Optional[UploadFile] = File(None),
    work_experience: Optional[UploadFile] = File(None),
):
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

@router.get(
    "/exists",
    summary="사용자 문서 존재 여부 조회 API",
)
async def get_document_exists(
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    db = get_mongo_db(request)
    # ponytail: projection으로 created_datetime만 추출, 문서 본문 로드 없음
    doc = db.user_documents.find_one(
        {"user_id": user_id},
        {
            "resume.created_datetime": 1,
            "cover_letter.created_datetime": 1,
            "projects.created_datetime": 1,
            "work_experience.created_datetime": 1,
            "_id": 0,
        },
    )
    if not doc:
        return {"resume": None, "cover_letter": None, "portfolio": None, "work_experience": None}
    return {
        "resume": (doc.get("resume") or {}).get("created_datetime"),
        "cover_letter": (doc.get("cover_letter") or {}).get("created_datetime"),
        "portfolio": (doc.get("projects") or {}).get("created_datetime"),
        "work_experience": (doc.get("work_experience") or {}).get("created_datetime"),
    }


@router.get(
    "/read",
    summary="사용자 파싱 문서 조회 API",
    description="로그인한 사용자의 파싱된 문서 데이터를 조회합니다. doc_type이 주어지면 해당 문서 타입의 데이터만 반환하고, 없으면 전체 문서를 반환합니다.",
    response_description="조회된 문서 데이터 반환"
)
async def get_user_documents(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    doc_type: Optional[str] = Query(None, description="조회할 문서 타입 (resume, cover_letter, projects, work_experience)")
):
    # MongoDB 데이터베이스 핸들 획득
    db = get_mongo_db(request)
    
    # 1. 해당 유저의 문서 가져오기 (컬렉션 이름은 기존 설명의 user_documents 기준)
    document = db.user_documents.find_one({"user_id": user_id})
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 사용자의 문서 데이터가 존재하지 않습니다."
        )
        
    # MongoDB의 고유 ID(_id)는 JSON 직렬화가 안 되므로 문자열 변환 또는 제외
    if "_id" in document:
        document["_id"] = str(document["_id"])

    # 2. 특정 doc_type만 요청받은 경우 필터링
    if doc_type:
        field_name = DOC_TYPE_TO_FIELD.get(doc_type, doc_type)  # portfolio → projects
        if field_name not in document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"요청하신 '{doc_type}' 타입의 문서 데이터가 없습니다."
            )
        return {
            "status": "success",
            "user_id": user_id,
            "doc_type": doc_type,
            "data": document[field_name]
        }

    # 3. doc_type이 없을 경우 전체 데이터 반환
    return {
        "status": "success",
        "user_id": user_id,
        "data": document
    }


@router.put(
    "/update",
    summary="사용자 문서 텍스트 편집 저장 API",
)
async def update_document(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    doc_type: str = Query(..., description="수정할 문서 타입 (resume, cover_letter, portfolio, work_experience)"),
    data: dict = Body(...),
):
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="잘못된 문서 타입입니다.")
    db = get_mongo_db(request)
    field_name = DOC_TYPE_TO_FIELD[doc_type]
    save_document_field(db, user_id, field_name, data)
    return {"status": "success", "user_id": user_id, "doc_type": doc_type}


@router.delete(
    "/delete",
    summary="사용자 문서 삭제 API",
)
async def delete_document(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    doc_type: str = Query(..., description="삭제할 문서 타입 (resume, cover_letter, portfolio, work_experience)"),
):
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="잘못된 문서 타입입니다.")
    db = get_mongo_db(request)
    field_name = DOC_TYPE_TO_FIELD[doc_type]
    deleted = delete_document_field(db, user_id, field_name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="삭제할 문서가 존재하지 않습니다.")
    return {"status": "success", "user_id": user_id, "doc_type": doc_type}