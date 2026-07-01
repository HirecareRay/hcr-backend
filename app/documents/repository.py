import logging
from datetime import datetime
from pymongo.database import Database

logger = logging.getLogger(__name__)

# MongoDB 컬렉션 이름 정의
# ponytail: 요구사항 변경에 맞추어 user_documents 컬렉션을 사용합니다.
COLLECTION_NAME = "user_documents"

def verify_and_get_collection(db: Database):
    """
    MongoDB에 해당 컬렉션이 존재하는지 확인하고,
    존재하지 않는다면 컬렉션을 생성하고 user_id에 대한 고유 인덱스를 설정합니다.
    """
    existing_collections = db.list_collection_names()
    if COLLECTION_NAME not in existing_collections:
        logger.info(f"컬렉션 '{COLLECTION_NAME}'이(가) 존재하지 않습니다. 새로 생성합니다.")
        db.create_collection(COLLECTION_NAME)
        # ponytail: 조회 속도 향상 및 고유 키 보장을 위해 user_id 필드에 고유 인덱스를 생성합니다.
        db[COLLECTION_NAME].create_index([("user_id", 1)], unique=True)
    return db[COLLECTION_NAME]

def save_document_field(db: Database, user_id: str, field_name: str, field_data: dict | list) -> str:
    """
    사용자의 특정 문서 키(resume, cover_letter, projects, work_experience)에 해당하는 필드값만
    MongoDB 컬렉션에 개별적으로 적재(upsert)합니다.
    """
    collection = verify_and_get_collection(db)
    
    # ponytail: 4가지 종류의 문서를 최상위 키값에 넣어 개별 수정/조회 가능하도록 $set으로 업데이트합니다.
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 저장/수정 시간 반영 — created_datetime은 프런트의 "최종 수정" 표시에 사용
    if isinstance(field_data, dict):
        field_data = {**field_data, "created_datetime": now}

    result = collection.update_one(
        {"user_id": user_id},
        {"$set": {field_name: field_data, "docs_updated_at": now}},
        upsert=True
    )
    if result.matched_count > 0:
        logger.info(f"user_id: {user_id}의 '{field_name}' 필드 데이터를 업데이트했습니다.")
    else:
        logger.info(f"user_id: {user_id}의 '{field_name}' 필드에 새 데이터를 저장했습니다.")
        
    return user_id

def find_user_documents(db: Database, user_id: str) -> dict | None:
    """사용자의 파싱된 문서 1건(resume·cover_letter·projects·work_experience)을 조회한다.

    면접 질문 개인화 등 읽기 전용 소비자가 쓴다. 없으면 None — 호출부가 개인화를
    생략하도록 한다(컬렉션이 없으면 verify_and_get_collection 이 생성만 하고 None 반환).
    """
    collection = verify_and_get_collection(db)
    return collection.find_one({"user_id": user_id})

def delete_document_field(db: Database, user_id: str, field_name: str) -> bool:
    collection = verify_and_get_collection(db)
    result = collection.update_one(
        {"user_id": user_id},
        {
            "$unset": {field_name: ""},
            "$set":   {"docs_updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        }
    )
    return result.matched_count > 0
