"""MongoDB access layer using Motor (asyncio) with connection pooling and caching."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

from .cache import SEARCH_CACHE

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri: str, database_name: str, collection_name: str = "Telegram_files"):
        self.uri = uri
        self.database_name = database_name
        self.collection_name = collection_name
        self._client: AsyncIOMotorClient | None = None
        self._db: AsyncIOMotorDatabase | None = None

    def connect(self) -> None:
        if self._client is None:
            # Connection pooling configuration for high concurrency (50k+ daily users)
            self._client = AsyncIOMotorClient(
                self.uri,
                maxPoolSize=100,
                minPoolSize=10,
                maxIdleTimeMS=45000,
                serverSelectionTimeoutMS=5000,
            )
            self._db = self._client[self.database_name]
            logger.info("MongoDB client connected to database: %s", self.database_name)

    @property
    def db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            self.connect()
        return self._db  # type: ignore

    @property
    def files(self) -> AsyncIOMotorCollection:
        return self.db[self.collection_name]

    @property
    def channels(self) -> AsyncIOMotorCollection:
        return self.db["library_channels"]

    @property
    def users(self) -> AsyncIOMotorCollection:
        return self.db["bot_users"]

    @property
    def join_reqs(self) -> AsyncIOMotorCollection:
        return self.db["join_requests"]

    async def ensure_indexes(self) -> None:
        """Create helpful compound and background indexes on collection."""
        try:
            await self.files.create_index([("file_name", 1)], background=True)
            await self.users.create_index([("_id", 1)], background=True)
            await self.join_reqs.create_index([("user_id", 1), ("chat_id", 1)], background=True)
            logger.info("MongoDB indexes verified.")
        except Exception as exc:
            logger.warning("Could not ensure indexes: %s", exc)

    async def save_join_request(self, user_id: int, chat_id: int) -> None:
        """Persist a live chat join request so user stays authorized across restarts."""
        try:
            await self.join_reqs.update_one(
                {"user_id": int(user_id), "chat_id": int(chat_id)},
                {
                    "$set": {"requested_at": datetime.now(timezone.utc)},
                    "$setOnInsert": {"user_id": int(user_id), "chat_id": int(chat_id)},
                },
                upsert=True,
            )
        except Exception as exc:
            logger.warning("Error saving join request for user %s: %s", user_id, exc)

    async def has_join_request(self, user_id: int, chat_id: int) -> bool:
        """Check if user has an active/pending join request for this chat."""
        try:
            doc = await self.join_reqs.find_one({
                "$or": [
                    {"user_id": int(user_id), "chat_id": int(chat_id)},
                    {"id": int(user_id)},
                ]
            })
            return bool(doc)
        except Exception:
            return False

    async def save_user(self, user_id: int, username: str | None = None, first_name: str = "") -> None:
        """Upsert a bot user into bot_users collection for broadcasting."""
        try:
            await self.users.update_one(
                {"_id": int(user_id)},
                {
                    "$set": {
                        "username": username,
                        "first_name": first_name,
                        "last_active": datetime.now(timezone.utc),
                    },
                    "$setOnInsert": {
                        "joined_at": datetime.now(timezone.utc),
                    },
                },
                upsert=True,
            )
        except Exception as exc:
            logger.warning("Error saving user %s: %s", user_id, exc)

    async def get_all_user_ids(self) -> list[int]:
        """Fetch distinct user IDs who have interacted with this Primary Bot."""
        user_ids: set[int] = set()
        try:
            async for doc in self.users.find({}, {"_id": 1}):
                if doc.get("_id"):
                    user_ids.add(int(doc["_id"]))
        except Exception as exc:
            logger.warning("Error loading from bot_users: %s", exc)

        return list(user_ids)

    async def get_user_count(self) -> int:
        """Return total active Primary Bot users count."""
        try:
            return await self.users.count_documents({})
        except Exception:
            return 0

    async def search_files(self, query: str, max_results: int = 100) -> list[dict[str, Any]]:
        """Search files in MongoDB by movie/series keyword.
        Uses exact phrase sequence matching with flexible word separators (matching ImoviesMagicBot).
        """
        query_clean = query.strip()
        if not query_clean:
            return []

        cache_key = f"q:{query_clean.lower()}:{max_results}"
        cached = SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return cached

        # Words in query
        words = [re.escape(w) for w in query_clean.split() if w]
        if not words:
            return []

        if len(words) == 1:
            raw_pattern = r"(\b|[\.\+\-_])" + words[0] + r"(\b|[\.\+\-_])"
        else:
            # Matches words in ordered sequence with flexible separators (spaces, dots, underscores, dashes)
            raw_pattern = r"(\b|[\.\+\-_])" + r".*[\s\.\+\-_]".join(words) + r"(\b|[\.\+\-_])"

        filter_query = {"file_name": {"$regex": raw_pattern, "$options": "i"}}

        try:
            cursor = self.files.find(filter_query).sort("_id", -1).limit(max_results)
            results = await cursor.to_list(length=max_results)

            # If no results found with word-boundary pattern, try relaxed sequence
            if not results and len(words) > 1:
                relaxed_pattern = r".*".join(words)
                cursor = self.files.find({"file_name": {"$regex": relaxed_pattern, "$options": "i"}}).sort("_id", -1).limit(max_results)
                results = await cursor.to_list(length=max_results)

            SEARCH_CACHE.set(cache_key, results, ttl_seconds=300)
            return results
        except Exception as exc:
            logger.error("Error searching MongoDB for %r: %s", query, exc)
            return []

    async def get_file_by_id(self, file_id: str) -> dict[str, Any] | None:
        try:
            return await self.files.find_one({"_id": file_id})
        except Exception as exc:
            logger.error("Error fetching file %s: %s", file_id, exc)
            return None

    async def get_today_files(self) -> list[dict[str, Any]]:
        """Fetch all files added during today (UTC) for the Day-End Summary."""
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)
        start_ts = int(start_of_day.timestamp())

        # Check by date field if present, or fallback to latest documents
        try:
            cursor = self.files.find({
                "$or": [
                    {"date": {"$gte": start_of_day}},
                    {"created_at": {"$gte": start_of_day}},
                    {"indexed_at": {"$gte": start_of_day}},
                    {"date": {"$gte": start_ts}},
                    {"timestamp": {"$gte": start_ts}},
                ]
            }).limit(200)
            docs = await cursor.to_list(length=200)
            return docs
        except Exception as exc:
            logger.warning("Error fetching today files: %s", exc)
            return []

    async def get_all_channel_ids(self) -> set[int]:
        """Fetch all dynamic library channel IDs from MongoDB."""
        try:
            cursor = self.channels.find({}, {"_id": 1})
            docs = await cursor.to_list(length=100)
            return {int(d["_id"]) for d in docs if isinstance(d.get("_id"), (int, str)) and str(d["_id"]).lstrip("-").isdigit()}
        except Exception as exc:
            logger.warning("Error fetching library channel IDs: %s", exc)
            return set()

