"""Configuration loader for Primary AutoFilter Bot."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^.\d+$")


def _optional_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if not val:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _parse_id_set(raw: str) -> frozenset[int]:
    out: set[int] = set()
    for item in raw.replace(",", " ").split():
        item = item.strip()
        if not item:
            continue
        try:
            out.add(int(item))
        except ValueError:
            pass
    return frozenset(out)


def _parse_channel_id(raw: str | None) -> int | str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    file_bot_username: str
    database_uri: str
    database_name: str
    collection_name: str
    admin_ids: frozenset[int]
    log_channel: int | str | None
    update_channel: int | str | None
    public_channel: int | str | None
    library_channels: frozenset[int | str]
    tmdb_api_key: str
    updates_channel_link: str
    movie_group_1_url: str
    movie_group_2_url: str
    port: int = 8080
    auto_delete_seconds: int = 1800
    default_thumbnail_path: str = "assets/default_banner.jpg"
    daily_check_hour: int = 9
    tmdb_region: str = "IN"
    tmdb_orig_lang: str = "all"


def load_primary_config(file_bot_username: str | None = None) -> Config | None:
    """Load Primary AutoFilter Bot config from environment.
    Returns None if PRIMARY_BOT_TOKEN is not configured.
    """
    bot_token = os.getenv("PRIMARY_BOT_TOKEN") or os.getenv("AUTOFILTER_BOT_TOKEN")
    if not bot_token or not bot_token.strip():
        logger.info("PRIMARY_BOT_TOKEN not set; Primary AutoFilter Bot is disabled.")
        return None

    bot_token = bot_token.strip()

    api_id_raw = os.getenv("PRIMARY_BOT_API_ID") or os.getenv("API_ID")
    if not api_id_raw:
        logger.warning("API_ID is required to start Primary AutoFilter Bot.")
        return None
    api_id = int(str(api_id_raw).strip())

    api_hash = (os.getenv("PRIMARY_BOT_API_HASH") or os.getenv("API_HASH") or "").strip()
    if not api_hash:
        logger.warning("API_HASH is required to start Primary AutoFilter Bot.")
        return None

    db_uri = os.getenv("DATABASE_URI", "").strip()
    if not db_uri:
        logger.warning("DATABASE_URI is required to start Primary AutoFilter Bot.")
        return None

    db_name = os.getenv("DATABASE_NAME", "Cluster0").strip()
    coll_name = os.getenv("COLLECTION_NAME", "Telegram_files").strip()

    admin_raw = os.getenv("ADMINS") or os.getenv("ADMIN_IDS", "")
    admin_ids = _parse_id_set(admin_raw)

    log_channel = _parse_channel_id(os.getenv("LOG_CHANNEL"))
    update_channel = _parse_channel_id(os.getenv("UPDATE_CHANNEL", "-1004277825107"))
    public_channel = _parse_channel_id(os.getenv("PUBLIC_CHANNEL", "-1002025864946"))

    lib_raw = os.getenv("LIBRARY_CHANNELS") or os.getenv("CHANNELS", "-1002414281459")
    library_channels = frozenset(
        _parse_channel_id(ch)
        for ch in lib_raw.replace(",", " ").split()
        if ch and _parse_channel_id(ch) is not None
    )

    tmdb_api_key = os.getenv("TMDB_API_KEY", "").strip()

    clean_file_bot = (file_bot_username or os.getenv("FILE_BOT_USERNAME", "iMoviesMagic_Bot")).strip().lstrip("@")

    updates_channel_link = os.getenv("UPDATES_CHANNEL_LINK", "https://t.me/imoviesmagic").strip()
    movie_group_1 = os.getenv("MOVIE_GROUP_1_URL", "https://t.me/+t6AsfQHAiqM2NDM1").strip()
    movie_group_2 = os.getenv("MOVIE_GROUP_2_URL", "").strip()

    port = _optional_int("PORT", 8080)
    auto_delete_seconds = _optional_int("AUTO_DELETE_SECONDS", 1800)
    daily_check_hour = _optional_int("DAILY_CHECK_HOUR", 9)

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        file_bot_username=clean_file_bot,
        database_uri=db_uri,
        database_name=db_name,
        collection_name=coll_name,
        admin_ids=admin_ids,
        log_channel=log_channel,
        update_channel=update_channel,
        public_channel=public_channel,
        library_channels=library_channels,
        tmdb_api_key=tmdb_api_key,
        updates_channel_link=updates_channel_link,
        movie_group_1_url=movie_group_1,
        movie_group_2_url=movie_group_2,
        port=port,
        auto_delete_seconds=auto_delete_seconds,
        daily_check_hour=daily_check_hour,
    )

