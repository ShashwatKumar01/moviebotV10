"""Background Scheduler:
1. Day-End Library Summary (Daily Digest) at 23:59 UTC — sends list of all files indexed today to Admin & Log Channel.
2. Daily TMDB Releases notification at configured daily_check_hour (09:00 UTC) — sends hot OTT/theatrical release cards to Admin & Log Channel with 1-click posting & broadcast buttons.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pyrogram import Client
from pyrogram.errors import RPCError

from ..config import Config
from ..core.database import Database
from ..core.release_parse import parse_release
from ..core.tmdb import (
    MediaMeta,
    fetch_daily_releases,
    format_media_caption,
)
from .keyboards import daily_release_card_keyboard

logger = logging.getLogger(__name__)


async def send_daily_library_digest(client: Client, config: Config, db: Database) -> int:
    """Compile and send day-end summary of all files added to library today."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%d %B %Y")

    logger.info("Generating Day-End Library Summary for %s...", today_str)

    today_files = await db.get_today_files()
    if not today_files:
        logger.info("No files added today.")
        return 0

    # Group files by movie/series title
    grouped: dict[str, int] = defaultdict(int)
    for f in today_files:
        name = f.get("file_name", "")
        if name:
            parsed = parse_release(name)
            title_key = f"{parsed.title} ({parsed.year})" if parsed.year else parsed.title
            grouped[title_key] += 1

    total_files = len(today_files)
    total_titles = len(grouped)

    lines = [
        f"📊 **Library Daily Digest — {today_str}**\n",
        f"Total Files Added Today: **{total_files}**",
        f"Total Unique Titles: **{total_titles}**\n",
        "🎬 **Releases Added to Library:**",
    ]

    bot_username = getattr(getattr(client, "me", None), "username", None) or "iMoviesAutoFilterBot"
    for idx, (title, count) in enumerate(sorted(grouped.items(), key=lambda x: x[1], reverse=True)[:30], 1):
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-")
        lines.append(f"{idx}. 🎬 [{title}](https://t.me/{bot_username}?start=getfile-{slug}) — {count} file(s)")

    lines.append("\n⚡️ All files are indexed and searchable across groups & PM!")

    summary_text = "\n".join(lines)
    digest_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Search Movies in Bot", url=f"https://t.me/{bot_username}?start=home"),
            InlineKeyboardButton("🔥 New Releases", url=f"https://t.me/{bot_username}?start=new"),
        ]
    ])

    recipients: list[int | str] = []
    if config.log_channel:
        recipients.append(config.log_channel)
    for admin_id in config.admin_ids:
        recipients.append(admin_id)

    for target in recipients:
        try:
            await client.send_message(target, summary_text, reply_markup=digest_keyboard, disable_web_page_preview=True)
            await asyncio.sleep(0.3)
        except RPCError as exc:
            logger.warning("Could not send daily digest to %s: %s", target, exc)

    logger.info("Day-End Library Summary sent successfully to %d recipients.", len(recipients))
    return total_files


async def send_daily_tmdb_releases(client: Client, config: Config) -> int:
    """Fetch today's new releases from TMDB and send a consolidated actionable report with deep hyperlinks to Admin & Log Channel."""
    if not config.tmdb_api_key:
        logger.warning("TMDB_API_KEY not configured. Skipping daily TMDB releases check.")
        return 0

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%d %B %Y")
    logger.info("Running automated daily TMDB releases check for %s...", today_str)

    releases = await fetch_daily_releases(
        config.tmdb_api_key,
        country=config.tmdb_region or "in",
        lang=config.tmdb_orig_lang or "all",
    )

    if not releases:
        logger.info("No new TMDB releases found for today.")
        return 0

    bot_username = getattr(getattr(client, "me", None), "username", None) or "iMoviesAutoFilterBot"

    lines = [
        f"🌟 **DAILY RELEASES REPORT — {today_str}**\n",
        f"Found **{len(releases)}** new OTT/Theatrical release(s) today:\n",
    ]

    for idx, meta in enumerate(releases[:15], 1):
        icon = "📺" if meta.media_type == "tv" else "🎬"
        rating_str = f"⭐ {meta.vote_average:.1f}/10" if meta.vote_average else "N/A"
        date_str = meta.release_date or (meta.year or "N/A")
        link = f"https://t.me/{bot_username}?start=rel_{meta.media_type}_{meta.id}"
        lines.append(f"{idx}. {icon} [{meta.title} ({meta.year or 'N/A'})]({link}) — {rating_str} • 📅 `{date_str}`")

    lines.append("\nℹ️ *Click any title above to open in bot, view trailer, and check available files.*")
    lines.append("\n📢 **Choose where to broadcast this release:**")

    report_text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Post to Updates Channel", callback_data="daily_post:upd"),
            InlineKeyboardButton("📢 Post to Public Channel", callback_data="daily_post:pub"),
        ],
        [
            InlineKeyboardButton("🎬 Browse in Bot (/new)", url=f"https://t.me/{bot_username}?start=new"),
        ],
    ])

    recipients: list[int | str] = []
    if config.log_channel:
        recipients.append(config.log_channel)
    for admin_id in config.admin_ids:
        recipients.append(admin_id)

    for target in recipients:
        try:
            await client.send_message(target, report_text, reply_markup=keyboard, disable_web_page_preview=True)
            await asyncio.sleep(0.3)
        except RPCError as exc:
            logger.warning("Failed to send consolidated daily releases report to %s: %s", target, exc)

    logger.info("Consolidated daily TMDB releases report sent (%d releases listed).", len(releases))
    return len(releases)


async def daily_scheduler_loop(client: Client, config: Config, db: Database) -> None:
    """Runs continuously:
    - 23:50 UTC: Day-End Library Summary
    - daily_check_hour UTC (default 09:00 UTC): Daily TMDB Releases Notification
    """
    last_digest_date: str | None = None
    last_tmdb_check_date: str | None = None

    logger.info(
        "Daily scheduler loop started. (Library Digest: 23:50 UTC, Daily Releases: %02d:00 UTC)",
        config.daily_check_hour,
    )
    await asyncio.sleep(15)

    while True:
        try:
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")

            # 1. Day-End Library Summary at 23:50+ UTC
            if last_digest_date != today_str and now.hour == 23 and now.minute >= 50:
                last_digest_date = today_str
                await send_daily_library_digest(client, config, db)

            # 2. Daily TMDB Releases at config.daily_check_hour UTC
            if last_tmdb_check_date != today_str and now.hour == config.daily_check_hour:
                last_tmdb_check_date = today_str
                await send_daily_tmdb_releases(client, config)

        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled.")
            break
        except Exception as exc:
            logger.exception("Error in scheduler loop: %s", exc)

        await asyncio.sleep(300)
