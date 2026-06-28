"""MongoDB connection helpers.

The app keeps one shared synchronous PyMongo client for FastAPI routes.
PyMongo's client is thread-safe, so a single process-wide client is the
recommended shape for this backend.
"""

from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings

_client: MongoClient | None = None


def connectMongo() -> MongoClient | None:
    """Create the Mongo client if a URI is configured."""
    global _client
    if _client is not None:
        return _client
    if not settings.mongodbUri:
        return None
    _client = MongoClient(settings.mongodbUri, serverSelectionTimeoutMS=5000)
    return _client


def closeMongo() -> None:
    """Close the shared Mongo client."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def getMongoClient() -> MongoClient:
    """Return a connected Mongo client or fail with a clear error."""
    client = connectMongo()
    if client is None:
        raise RuntimeError("MONGODB_URI is not configured")
    return client


def getMongoDatabase() -> Database:
    """Return the configured application database."""
    return getMongoClient()[settings.mongodbDatabase]


def pingMongo() -> bool:
    """Check whether MongoDB is reachable."""
    client = getMongoClient()
    client.admin.command("ping")
    return True
