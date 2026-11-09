"""Releases Explorer: Implements /new and /today commands for ALL users with interactive
pagination, media-type tabs, country tabs, and language tabs.
Includes role-based controls: regular users browse releases & trailers, while Admins
get 1-click buttons to post to Updates Channel (Screenshot 1), Public Channel (Screenshot 2),
or Broadcast to Bot Users.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError

from ..config import Config
from ..core.database import Database
from ..core.tmdb import (
    MediaMeta,
    COUNTRY_CODE_MAP,
    LANGUAGE_CODE_MAP,
    create_http_session,
    fetch_details_by_id,
    fetch_recent_releases,
    fetch_daily_releases,
    format_media_caption,
)
from .keyboards import (
    new_releases_keyboard,
    release_preview_keyboard,
)
from .channel_poster import post_to_updates_channel, post_to_public_channel
from .force_sub import is_user_authorized, send_force_sub_prompt

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8

# User session cache for releases explorer:
# {user_id: {"releases": [...], "days": int, "media_filter": str, "country": str, "lang": str, "page": int}}
RECENT_RELEASES_CACHE: dict[int, dict[str, Any]] = {}

# Set of already posted (media_type, media_id) to avoid duplicates
POSTED_RELEASES: set[tuple[str, str]] = set()


def _format_new_list_text(
    days: int,
    total_count: int,
    page: int,
    total_pages: int,
    media_filter: str,
    country_filter: str,
    lang_filter: str,
) -> str:
    media_label = {"all": "All", "movie": "Movies", "tv": "Series"}.get(media_filter, "All")
    country_label = {"all": "Global", "in": "India", "us": "USA", "kr": "Korea"}.get(country_filter, "Global")
    lang_label = {"all": "All Languages", "hi": "Hindi", "south": "South Indian", "en": "English"}.get(lang_filter, "All Languages")

    return (
        f"🎬 **Releases of Last {days} Days**\n"
        f"🔍 Filter: **{media_label}** | **{country_label}** | **{lang_label}**\n"
        f"Found **{total_count}** releases. Page **{page}** of **{total_pages}**:\n\n"
        f"👇 Click any release below to preview details and watch trailer:"
    )


async def _refetch_and_render_new(client: Client, query: CallbackQuery, config: Config, cache: dict[str, Any]) -> None:
    days = cache.get("days", 15)
    media_filter = cache.get("media_filter", "all")
    country = cache.get("country", "all")
    lang = cache.get("lang", "all")

    await query.answer("Fetching releases...")

    releases = await fetch_recent_releases(
        config.tmdb_api_key or "",
        days=days,
        media_filter=media_filter,
        country=country,
        lang=lang,
    )
    cache["releases"] = releases
    cache["page"] = 1

    total_pages = max(1, (len(releases) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page_items = releases[:_PAGE_SIZE]

    text = _format_new_list_text(
        days=days,
        total_count=len(releases),
        page=1,
        total_pages=total_pages,
        media_filter=media_filter,
        country_filter=country,
        lang_filter=lang,
    )
    keyboard = new_releases_keyboard(
        page_items,
        page=1,
        total_pages=total_pages,
        media_filter=media_filter,
        country_filter=country,
        lang_filter=lang,
    )

    try:
        await query.message.edit_text(text, reply_markup=keyboard)
    except RPCError:
        pass


async def trigger_new_releases(client: Client, message: Message, config: Config, db: Database, args: list[str] | None = None) -> None:
    """Execute /new releases workflow for command or /start deep-link."""
    try:
        user = message.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        if message.chat.type == ChatType.PRIVATE and user:
            if not await is_user_authorized(client, user.id, config, db):
                await send_force_sub_prompt(client, message, config, db, retry_payload="new")
                return

        if not config.tmdb_api_key:
            await message.reply_text("❌ TMDB_API_KEY is not configured in .env.")
            return

        days = 15
        country = config.tmdb_region or "all"
        lang = config.tmdb_orig_lang or "all"
        media_filter = "all"

        if args is None:
            args = (message.text or "").split()[1:]

        for arg in args:
            val = arg.strip().lower()
            if val.isdigit():
                parsed_days = int(val)
                if 1 <= parsed_days <= 60:
                    days = parsed_days
            elif val in COUNTRY_CODE_MAP:
                country = val
            elif val in LANGUAGE_CODE_MAP:
                lang = val
            elif val in ("movie", "movies"):
                media_filter = "movie"
            elif val in ("tv", "series", "show", "shows"):
                media_filter = "tv"

        status = await message.reply_text("⏳ Loading recent releases from TMDB...")

        releases = await fetch_recent_releases(
            config.tmdb_api_key,
            days=days,
            media_filter=media_filter,
            country=country,
            lang=lang,
        )

        user_id = message.from_user.id if message.from_user else message.chat.id
        RECENT_RELEASES_CACHE[user_id] = {
            "releases": releases,
            "days": days,
            "media_filter": media_filter,
            "country": country,
            "lang": lang,
            "page": 1,
        }

        total_pages = max(1, (len(releases) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page_items = releases[:_PAGE_SIZE]

        if not releases:
            country_label = {"all": "Global", "in": "India", "us": "USA", "kr": "Korea"}.get(country, "Global")
            text = (
                f"🎬 **Releases of Last {days} Days**\n"
                f"🔍 Filter: **{country_label}**\n\n"
                f"ℹ️ No new releases found in TMDB for this filter in the last {days} days.\n\n"
                f"👇 Tap **• All •** or **🌍 Global** below to browse hot releases:"
            )
        else:
            text = _format_new_list_text(
                days=days,
                total_count=len(releases),
                page=1,
                total_pages=total_pages,
                media_filter=media_filter,
                country_filter=country,
                lang_filter=lang,
            )
        keyboard = new_releases_keyboard(
            page_items,
            page=1,
            total_pages=total_pages,
            media_filter=media_filter,
            country_filter=country,
            lang_filter=lang,
        )

        try:
            await status.edit_text(text, reply_markup=keyboard)
        except RPCError:
            await message.reply_text(text, reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Error executing /new releases: %s", exc)
        await message.reply_text(f"❌ Could not load releases: {exc}")


async def trigger_today_releases(client: Client, message: Message, config: Config, db: Database, args: list[str] | None = None) -> None:
    """Execute /today releases workflow for command or /start deep-link."""
    try:
        user = message.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        if message.chat.type == ChatType.PRIVATE and user:
            if not await is_user_authorized(client, user.id, config, db):
                await send_force_sub_prompt(client, message, config, db, retry_payload="today")
                return

        if not config.tmdb_api_key:
            await message.reply_text("❌ TMDB_API_KEY is not configured in .env.")
            return

        country = config.tmdb_region or "all"
        lang = config.tmdb_orig_lang or "all"

        if args is None:
            args = (message.text or "").split()[1:]

        for arg in args:
            val = arg.strip().lower()
            if val in COUNTRY_CODE_MAP:
                country = val
            elif val in LANGUAGE_CODE_MAP:
                lang = val

        status = await message.reply_text("⏳ Fetching today's OTT & theatrical releases...")

        releases = await fetch_daily_releases(config.tmdb_api_key, country=country, lang=lang)

        user_id = message.from_user.id if message.from_user else message.chat.id
        RECENT_RELEASES_CACHE[user_id] = {
            "releases": releases,
            "days": 1,
            "media_filter": "all",
            "country": country,
            "lang": lang,
            "page": 1,
        }

        total_pages = max(1, (len(releases) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page_items = releases[:_PAGE_SIZE]

        text = (
            f"📅 **Today's Hot Releases**\n"
            f"Found **{len(releases)}** releases today. Page **1** of **{total_pages}**:\n\n"
            f"👇 Click any release below to preview details:"
        )
        keyboard = new_releases_keyboard(
            page_items,
            page=1,
            total_pages=total_pages,
            media_filter="all",
            country_filter=country,
            lang_filter=lang,
        )

        try:
            await status.edit_text(text, reply_markup=keyboard)
        except RPCError:
            await message.reply_text(text, reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Error executing /today releases: %s", exc)
        await message.reply_text(f"❌ Could not load today releases: {exc}")


def register_release_explorer(app: Client, config: Config, db: Database) -> None:

    # -------------------------------------------------------------
    # /new command (for all users)
    # -------------------------------------------------------------
    @app.on_message(filters.command(["new", "releases"]))
    async def cmd_new(client: Client, message: Message) -> None:
        await trigger_new_releases(client, message, config, db)

    # -------------------------------------------------------------
    # /today command (for all users)
    # -------------------------------------------------------------
    @app.on_message(filters.command(["today", "daily"]))
    async def cmd_today(client: Client, message: Message) -> None:
        await trigger_today_releases(client, message, config, db)
            country_filter=country,
            lang_filter=lang,
        )

        try:
            await status.edit_text(text, reply_markup=keyboard)
        except RPCError:
            await message.reply_text(text, reply_markup=keyboard)

    # -------------------------------------------------------------
    # Filter & Pagination Callbacks
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^new_p:"))
    async def cb_pagination(client: Client, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1

        cache = RECENT_RELEASES_CACHE.get(query.from_user.id)
        if not cache:
            return await query.answer("Session expired. Please run /new again.", show_alert=True)

        releases = cache["releases"]
        total_pages = max(1, (len(releases) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(1, min(total_pages, page))
        cache["page"] = page

        start_idx = (page - 1) * _PAGE_SIZE
        page_items = releases[start_idx : start_idx + _PAGE_SIZE]

        text = _format_new_list_text(
            days=cache.get("days", 15),
            total_count=len(releases),
            page=page,
            total_pages=total_pages,
            media_filter=cache.get("media_filter", "all"),
            country_filter=cache.get("country", "all"),
            lang_filter=cache.get("lang", "all"),
        )
        keyboard = new_releases_keyboard(
            page_items,
            page=page,
            total_pages=total_pages,
            media_filter=cache.get("media_filter", "all"),
            country_filter=cache.get("country", "all"),
            lang_filter=cache.get("lang", "all"),
        )

        try:
            await query.message.edit_text(text, reply_markup=keyboard)
            await query.answer()
        except RPCError:
            pass

    @app.on_callback_query(filters.regex(r"^new_m:"))
    async def cb_media_filter(client: Client, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        media_type = parts[1] if len(parts) > 1 else "all"

        cache = RECENT_RELEASES_CACHE.get(query.from_user.id)
        if not cache:
            return await query.answer("Session expired. Please run /new again.", show_alert=True)

        cache["media_filter"] = media_type
        await _refetch_and_render_new(client, query, config, cache)

    @app.on_callback_query(filters.regex(r"^new_c:"))
    async def cb_country_filter(client: Client, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        country = parts[1] if len(parts) > 1 else "all"

        cache = RECENT_RELEASES_CACHE.get(query.from_user.id)
        if not cache:
            return await query.answer("Session expired. Please run /new again.", show_alert=True)

        cache["country"] = country
        await _refetch_and_render_new(client, query, config, cache)

    @app.on_callback_query(filters.regex(r"^new_l:"))
    async def cb_lang_filter(client: Client, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        lang = parts[1] if len(parts) > 1 else "all"

        cache = RECENT_RELEASES_CACHE.get(query.from_user.id)
        if not cache:
            return await query.answer("Session expired. Please run /new again.", show_alert=True)

        cache["lang"] = lang
        await _refetch_and_render_new(client, query, config, cache)

    @app.on_callback_query(filters.regex(r"^close_new$"))
    async def cb_close_new(_: Client, query: CallbackQuery) -> None:
        try:
            await query.message.delete()
        except RPCError:
            pass

    # -------------------------------------------------------------
    # Release Preview (prev_rel:{media_type}:{media_id}:{back_page})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^prev_rel:"))
    async def cb_preview_release(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        parts = query.data.split(":")
        media_type = parts[1]
        media_id = parts[2]
        back_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        is_admin = query.from_user.id in config.admin_ids

        await query.answer("Fetching release preview...")

        async with create_http_session() as session:
            meta = await fetch_details_by_id(session, config.tmdb_api_key or "", media_type, media_id)

        if not meta:
            return await query.answer("Failed to load details from TMDB.", show_alert=True)

        caption = format_media_caption(meta)
        is_posted = (media_type, str(media_id)) in POSTED_RELEASES

        keyboard = release_preview_keyboard(
            media_type=media_type,
            media_id=media_id,
            back_page=back_page,
            is_admin=is_admin,
            is_upd_posted=is_posted,
            is_pub_posted=is_posted,
            trailer_url=meta.trailer_url,
        )

        photo = meta.poster_url or config.default_thumbnail_path
        chat_id = query.message.chat.id

        try:
            if photo and (photo.startswith("http") or os.path.exists(photo)):
                await client.send_photo(chat_id, photo=photo, caption=caption, reply_markup=keyboard)
                try:
                    await query.message.delete()
                except RPCError:
                    pass
            else:
                await query.message.edit_text(caption, reply_markup=keyboard)
        except RPCError as exc:
            logger.warning("Error showing release preview: %s", exc)

    # -------------------------------------------------------------
    # Back to Releases List (back_new:{page})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^back_new:"))
    async def cb_back_to_new(client: Client, query: CallbackQuery) -> None:
        parts = query.data.split(":")
        page = 1
        for p in reversed(parts[1:]):
            if p.isdigit():
                page = int(p)
                break

        cache = RECENT_RELEASES_CACHE.get(query.from_user.id)
        if not cache:
            releases = await fetch_recent_releases(
                config.tmdb_api_key or "",
                days=15,
                media_filter="all",
                country=config.tmdb_region or "all",
                lang=config.tmdb_orig_lang or "all",
            )
            cache = {
                "releases": releases,
                "days": 15,
                "media_filter": "all",
                "country": config.tmdb_region or "all",
                "lang": config.tmdb_orig_lang or "all",
                "page": page,
            }
            RECENT_RELEASES_CACHE[query.from_user.id] = cache
        else:
            releases = cache["releases"]

        total_pages = max(1, (len(releases) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(1, min(total_pages, page))
        cache["page"] = page

        start_idx = (page - 1) * _PAGE_SIZE
        page_items = releases[start_idx : start_idx + _PAGE_SIZE]

        text = _format_new_list_text(
            days=cache.get("days", 15),
            total_count=len(releases),
            page=page,
            total_pages=total_pages,
            media_filter=cache.get("media_filter", "all"),
            country_filter=cache.get("country", "all"),
            lang_filter=cache.get("lang", "all"),
        )
        keyboard = new_releases_keyboard(
            page_items,
            page=page,
            total_pages=total_pages,
            media_filter=cache.get("media_filter", "all"),
            country_filter=cache.get("country", "all"),
            lang_filter=cache.get("lang", "all"),
        )

        try:
            await query.message.delete()
        except RPCError:
            pass

        await client.send_message(query.message.chat.id, text, reply_markup=keyboard)

    # -------------------------------------------------------------
    # Admin Channel Posting from /new
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^post_rel_(upd|pub):"))
    async def cb_post_channel_release(client: Client, query: CallbackQuery) -> None:
        if query.from_user.id not in config.admin_ids:
            return await query.answer("❌ Only admins can post to channels.", show_alert=True)

        parts = query.data.split(":")
        target_channel_type = parts[0].split("_")[-1]  # 'upd' or 'pub'
        media_type = parts[1]
        media_id = parts[2]
        back_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        await query.answer("Posting release to channel...")

        async with create_http_session() as session:
            meta = await fetch_details_by_id(session, config.tmdb_api_key or "", media_type, media_id)

        if not meta:
            return await query.answer("Could not fetch release from TMDB.", show_alert=True)

        if target_channel_type == "upd":
            success = await post_to_updates_channel(client, config, meta)
            target_name = "Updates Channel"
        else:
            success = await post_to_public_channel(client, config, meta)
            target_name = "Public Channel"

        if success:
            POSTED_RELEASES.add((media_type, str(media_id)))
            await query.answer(f"✅ Successfully posted to {target_name}!", show_alert=True)

            updated_keyboard = release_preview_keyboard(
                media_type=media_type,
                media_id=media_id,
                back_page=back_page,
                is_admin=True,
                is_upd_posted=target_channel_type == "upd",
                is_pub_posted=target_channel_type == "pub",
                trailer_url=meta.trailer_url,
            )
            try:
                await query.edit_message_reply_markup(reply_markup=updated_keyboard)
            except RPCError:
                pass
        else:
            await query.answer(f"❌ Failed to post to {target_name}. Check logs.", show_alert=True)

