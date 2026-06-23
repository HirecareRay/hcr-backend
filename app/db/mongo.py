"""MongoDB 연결 (pymongo) — 문서 / RAG(원문·벡터).

DB 연동 단계에서 활성화한다:
  1. requirements.txt 의 pymongo 주석을 푼다
  2. app/core/config.py 에 mongodb_uri 필드를 추가한다
  3. .env 에 MONGODB_URI 를 채운다
  4. 아래 주석을 푼다
"""

# from pymongo import MongoClient
#
# from app.core.config import settings
#
# mongo_client = MongoClient(settings.mongodb_uri)
# mongo_db = mongo_client["hcr"]
