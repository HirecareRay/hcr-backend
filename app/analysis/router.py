from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db.mongo import get_mongo_db
from app.db.session import get_db
from app.analysis import service

router = APIRouter(prefix="/analysis", tags=["analysis"])
_bearer = HTTPBearer(auto_error=True)


async def _user_id(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    try:
        return decode_access_token(cred.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


class FitRequest(BaseModel):
    company_id: str
    job_posting_id: str


@router.post("/fit")
async def analyze_fit(
    request: Request,
    body: FitRequest,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
):
    mongo = get_mongo_db(request)
    try:
        result = await service.analyze_fit(db, mongo, user_id, body.job_posting_id, body.company_id)
        # job_url은 분석 캐시에 스냅샷으로 저장하지 않고 매 요청마다 여기서 라이브로 붙인다
        # — 캐시 HIT/MISS·분석 생성 시점과 무관하게 항상 최신 원본 링크를 보장한다.
        job_doc = mongo.job_postings.find_one(
            {"_id": ObjectId(body.job_posting_id)}, {"source_url": 1}
        )
        result["job_url"] = (job_doc or {}).get("source_url") or ""
        return {"success": True, "data": result}
    except service.NoDocumentsFound:
        raise HTTPException(
            status_code=404,
            detail="이력서가 없습니다. 마이페이지에서 이력서를 등록해 주세요.",
        )
    except service.JobPostingNotFound:
        raise HTTPException(status_code=404, detail="채용공고를 찾을 수 없습니다.")


@router.get("/fit/history")
async def list_fit_history(
    request: Request,
    user_id: str = Depends(_user_id),
):
    """그 유저의 적합도 분석 기록을 최신순 카드 목록으로 조회한다(적합도 분석 탭)."""
    mongo = get_mongo_db(request)
    return {"success": True, "data": service.list_fit_history(mongo, user_id)}
