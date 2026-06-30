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
    job_title: str
    responsibilities: list[str] = []
    requirements: list[str] = []
    preferred_qualifications: list[str] = []


@router.post("/fit")
def analyze_fit(
    request: Request,
    body: FitRequest,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
):
    mongo = get_mongo_db(request)
    try:
        result = service.analyze_fit(db, mongo, user_id, body.model_dump())
        return {"success": True, "data": result}
    except service.NoDocumentsFound:
        raise HTTPException(
            status_code=404,
            detail="분석할 서류가 없습니다. 마이페이지에서 이력서를 등록해 주세요.",
        )
