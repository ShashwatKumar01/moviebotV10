"""AutoFilter Engine: Two-step search flow in Groups and PM.
Step 1: Displays clean buttons for distinct movie/series titles sorted by Year descending (e.g. 2026, 2025...).
Step 2: When user clicks a title, fetches TMDB poster & metadata, lists quality download buttons pointing
to the secondary file delivery bot, with a [🔙 Back to Titles] button.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import os
import re
import urllib.parse
from typing import Any
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError

from ..config import Config
from ..core.database import Database
from ..core.release_parse import parse_release
from ..core.tmdb import (
    MediaMeta,
    fetch_media_meta,
    format_autofilter_caption,
)
from .keyboards import (
    build_title_selection_keyboard,
    build_autofilter_keyboard,
)
from .channel_poster import post_to_updates_channel, post_to_public_channel
from .force_sub import is_user_authorized, send_force_sub_prompt, send_force_sub_prompt_callback

logger = logging.getLogger(__name__)

# Active session cache:
# {query_key: {"grouped": [...], "query": str, "title_page": int, "active_idx": int}}
AUTOFILTER_RESULTS: dict[str, dict[str, Any]] = {}


async def _auto_delete_message(client: Client, chat_id: int | str, message_id: int, delay_seconds: int) -> None:
    """Safely delete search response in groups after delay to prevent chat clutter."""
    if delay_seconds <= 0:
        return
    await asyncio.sleep(delay_seconds)
    try:
        await client.delete_messages(chat_id, message_id)
    except RPCError:
        pass


_QUERY_CLEAN_RE = re.compile(
    r"\b(pl(i|e)*?(s|z+|ease|se|ese)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|hello|that|find|dubbed|link|film|all\.episodes|full|print|file)\b",
    re.IGNORECASE,
)


def clean_query_text(text: str) -> str:
    cleaned = _QUERY_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"[\._\+\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or text.strip()


async def process_search_query(
    client: Client,
    message: Message,
    raw_query: str,
    config: Config,
    db: Database,
) -> None:
    """Core search execution engine used by PM text, group queries, and /start getfile- links."""
    user = message.from_user
    if user:
        await db.save_user(user.id, user.username, user.first_name)

    # Completely ignore channels, anonymous channel senders, and bots
    if message.chat.type == ChatType.CHANNEL or not user or user.is_bot or getattr(message, "sender_chat", None):
        return

    # Ignore chat if it looks like a logging channel/group or matches configured channels
    chat_title = (getattr(message.chat, "title", "") or "").lower()
    if any(keyword in chat_title for keyword in ("log", "dump", "backup", "trash", "bin")):
        return

    # Ignore configured admin/log/broadcast/library channels
    ignored_chats = {
        str(c) for c in (
            config.log_channel,
            config.update_channel,
            config.public_channel,
            *config.library_channels,
            *config.auth_channels,
        ) if c is not None
    }
    if str(message.chat.id) in ignored_chats:
        return

    query_text = clean_query_text(raw_query)
    if len(query_text) < 2 or query_text.startswith(("/", "!", ".", "#", "-")):
        return

    # Ignore log bot texts, bot formatting, user IDs, hashtags and join requests
    lower_raw = raw_query.lower()
    if (
        "#" in raw_query
        or "user id" in lower_raw
        or "content name" in lower_raw
        or any(tag in lower_raw for tag in ("requested_content", "newuser", "join request", "joined", "subscriber"))
    ):
        return

    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    # Immediate feedback message while query is processed
    wait_msg: Message | None = None
    try:
        wait_msg = await message.reply_text("⏳ **Searching library, please wait...**")
    except RPCError:
        pass

    # 1. Search MongoDB files
    files = await db.search_files(query_text, max_results=100)
    if not files and query_text != raw_query.strip():
        files = await db.search_files(raw_query.strip(), max_results=100)

    if not files:
        if wait_msg:
            try:
                await wait_msg.delete()
            except RPCError:
                pass
        if not is_group:
            await message.reply_text(
                f"❌ **No files found for** `{query_text}`.\n\n"
                f"💡 **Tips:**\n"
                f"• Check the spelling\n"
                f"• Try searching with just the main title (e.g., `Akka` or `Chaar Yaar`)\n"
                f"• Or browse upcoming & recent releases with `/new`"
            )
        return

    grouped_list = group_files_by_title(files, query_text)

    # Generate unique session key
    query_key = f"{message.chat.id}_{message.id}_{abs(hash(query_text)) % 100000}"
    AUTOFILTER_RESULTS[query_key] = {
        "grouped": grouped_list,
        "query": query_text,
        "title_page": 1,
        "active_idx": None,
    }

    # 4. Build Step 1: Clean Title Selection List (No TMDB info)
    bot_username = getattr(getattr(client, "me", None), "username", None) or config.file_bot_username
    keyboard = build_title_selection_keyboard(
        grouped_titles=grouped_list,
        query_key=query_key,
        page=1,
        page_size=8,
        bot_username=bot_username,
        query_text=query_text,
    )

    match_count = len(grouped_list)
    plural_title = "title" if match_count == 1 else "titles"
    total_matched_files = sum(item["file_count"] for item in grouped_list)
    plural_file = "file" if total_matched_files == 1 else "files"

    text = (
        f"🔍 **Search Results for:** `{query_text}`\n"
        f"Found **{match_count}** {plural_title} ({total_matched_files} {plural_file}) in library.\n\n"
        f"👇 **Select your movie / series below to get files:**"
    )

    try:
        if wait_msg:
            try:
                await wait_msg.delete()
            except RPCError:
                pass

        sent = await message.reply_text(text=text, reply_markup=keyboard)

        # Auto-delete search results after configured seconds (default 30 mins)
        if config.auto_delete_seconds > 0:
            asyncio.create_task(
                _auto_delete_message(client, sent.chat.id, sent.id, config.auto_delete_seconds)
            )
    except RPCError as exc:
        logger.warning("Error replying to search: %s", exc)


def group_files_by_title(files: list[dict[str, Any]], query_text: str) -> list[dict[str, Any]]:
    """Group and sort files by distinct Title, Season (for TV), Year, and Media Type."""
    query_words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", query_text)]

    grouped_map: dict[tuple[str, int | None, str | None, bool], dict[str, Any]] = {}
    for f in files:
        fname = f.get("file_name", "")
        parsed = parse_release(fname)
        actual_title = parsed.title.strip() if parsed.title else fname[:30].strip()

        # Double check: actual title must contain query words
        t_lower = actual_title.lower()
        if query_words and not all(w in t_lower for w in query_words):
            continue

        season_val = parsed.season if parsed.is_tv else None
        key = (actual_title.lower(), season_val, parsed.year, parsed.is_tv)
        if key not in grouped_map:
            grouped_map[key] = {
                "title": actual_title,
                "year": parsed.year,
                "season": season_val,
                "is_tv": parsed.is_tv,
                "files": [],
            }
        grouped_map[key]["files"].append(f)

    # Fallback if strict title matching yielded no results
    if not grouped_map:
        for f in files:
            fname = f.get("file_name", "")
            parsed = parse_release(fname)
            actual_title = parsed.title.strip() if parsed.title else fname[:30].strip()
            season_val = parsed.season if parsed.is_tv else None
            key = (actual_title.lower(), season_val, parsed.year, parsed.is_tv)
            if key not in grouped_map:
                grouped_map[key] = {
                    "title": actual_title,
                    "year": parsed.year,
                    "season": season_val,
                    "is_tv": parsed.is_tv,
                    "files": [],
                }
            grouped_map[key]["files"].append(f)

    grouped_list: list[dict[str, Any]] = []
    for grp in grouped_map.values():
        grouped_list.append({
            "title": grp["title"],
            "year": grp["year"],
            "season": grp.get("season"),
            "is_tv": grp["is_tv"],
            "file_count": len(grp["files"]),
            "files": grp["files"],
        })

    # Sort by relevance to query + Year Descending + Season
    clean_q = query_text.lower().strip()

    def get_relevance(title: str) -> int:
        t = title.lower().strip()
        if t == clean_q:
            return 100
        if t.startswith(clean_q):
            return 80
        if clean_q in t:
            return 60
        return 40

    def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        rel = get_relevance(item["title"])
        raw_y = item.get("year")
        y = int(raw_y) if raw_y and str(raw_y).isdigit() else 0
        s = item.get("season") or 0
        return (rel, y, s)

    grouped_list.sort(key=sort_key, reverse=True)

    for idx, item in enumerate(grouped_list):
        item["index"] = idx

    return grouped_list


async def render_selected_title(
    client: Client,
    query: CallbackQuery,
    query_key: str,
    group_idx: int,
    config: Config,
    db: Database,
) -> None:
    """Render Step 2: TMDB poster, movie metadata, and quality download buttons."""
    user = query.from_user
    if user:
        await db.save_user(user.id, user.username, user.first_name)

    data = AUTOFILTER_RESULTS.get(query_key)
    if not data:
        return await query.answer("Session expired. Please search again.", show_alert=True)

    grouped_list = data["grouped"]
    if group_idx >= len(grouped_list):
        return await query.answer("Invalid selection.", show_alert=True)

    selected = grouped_list[group_idx]
    data["active_idx"] = group_idx
    files = selected["files"]

    try:
        await query.answer("Loading details & download links...")
    except RPCError:
        pass

    # Fetch TMDB metadata for this specific selected title
    meta: MediaMeta | None = selected.get("meta")
    if not meta:
        meta = await fetch_media_meta(
            config.tmdb_api_key,
            title=selected["title"],
            year=selected["year"],
            is_tv=selected["is_tv"],
        )
        if not meta:
            meta = MediaMeta(
                title=selected["title"],
                year=selected["year"],
                overview="",
                poster_url=None,
                media_type="tv" if selected["is_tv"] else "movie",
            )
        selected["meta"] = meta

    is_admin = bool(user and user.id in config.admin_ids)
    safe_bot = (getattr(getattr(client, "me", None), "username", None) or config.file_bot_username).strip().lstrip("@")
    clean_slug = re.sub(r"[^a-zA-Z0-9]+", "-", selected["title"]).strip("-") or "movie"
    target_link = f"https://t.me/{safe_bot}?start=getfile-{clean_slug}"
    share_text = f"Watch {selected['title']} on iMovies"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(target_link, safe='')}&text={urllib.parse.quote(share_text)}"

    caption = format_autofilter_caption(meta, file_count=len(files), is_admin=is_admin)
    keyboard = build_autofilter_keyboard(
        files=files,
        file_bot_username=config.file_bot_username,
        page=1,
        page_size=8,
        query_key=query_key,
        title_page=data.get("title_page", 1),
        trailer_url=meta.trailer_url,
        is_admin=is_admin,
        share_url=share_url,
    )

    photo = meta.poster_url or config.default_thumbnail_path
    chat_id = query.message.chat.id

    try:
        if photo and (photo.startswith("http") or os.path.exists(photo)):
            sent = await client.send_photo(chat_id, photo=photo, caption=caption, reply_markup=keyboard)
            try:
                await query.message.delete()
            except RPCError:
                pass
            if config.auto_delete_seconds > 0:
                asyncio.create_task(
                    _auto_delete_message(client, sent.chat.id, sent.id, config.auto_delete_seconds)
                )
        else:
            await query.message.edit_text(caption, reply_markup=keyboard)
    except RPCError as exc:
        logger.warning("Error rendering Step 2 search: %s", exc)


def register_autofilter(app: Client, config: Config, db: Database) -> None:

    @app.on_message(
        (filters.private | filters.group)
        & filters.text
        & ~filters.bot
        & ~filters.command(["start", "help", "new", "today", "search", "stats", "channels", "digest", "broadcast", "check_releases"])
    )
    async def handle_search_query(client: Client, message: Message) -> None:
        if not message.from_user or message.from_user.is_bot or getattr(message, "sender_chat", None):
            return
        query_text = (message.text or "").strip()
        if len(query_text) < 2 or query_text.startswith(("/", "!", ".", "#", "-")):
            return
        await process_search_query(client, message, query_text, config, db)

    # -------------------------------------------------------------
    # Step 1: Title Pagination (page_t:{page}:{query_key})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^page_t:"))
    async def handle_title_pagination(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        parts = query.data.split(":")
        page = int(parts[1])
        query_key = parts[2]

        data = AUTOFILTER_RESULTS.get(query_key)
        if not data:
            return await query.answer("Session expired. Please search again.", show_alert=True)

        data["title_page"] = page
        grouped_list = data["grouped"]
        query_text = data.get("query", "")
        bot_username = getattr(getattr(client, "me", None), "username", None) or config.file_bot_username

        keyboard = build_title_selection_keyboard(
            grouped_titles=grouped_list,
            query_key=query_key,
            page=page,
            page_size=8,
            bot_username=bot_username,
            query_text=query_text,
        )

        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
            await query.answer()
        except RPCError:
            pass

    # -------------------------------------------------------------
    # Step 2: User Selects Title (sel_t:{query_key}:{group_idx})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^sel_t:"))
    async def handle_title_selection(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        parts = query.data.split(":")
        query_key = parts[1]
        group_idx = int(parts[2])

        # Force sub verification before revealing movie banner and files
        is_group = query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if not is_group and user:
            if not await is_user_authorized(client, user.id, config, db):
                await send_force_sub_prompt_callback(
                    client, query, config, db, retry_payload=f"sel_t:{query_key}:{group_idx}"
                )
                return

        await render_selected_title(client, query, query_key, group_idx, config, db)

    # -------------------------------------------------------------
    # Step 2 -> Step 1: Back to Titles (back_t:{query_key}:{title_page})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^back_t:"))
    async def handle_back_to_titles(client: Client, query: CallbackQuery) -> None:
        try:
            await query.answer()
        except RPCError:
            pass

        user = query.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        parts = query.data.split(":")
        query_key = parts[1]
        title_page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

        data = AUTOFILTER_RESULTS.get(query_key)
        if not data:
            # Auto-recovery: recover query text from caption/text or message
            msg_text = (query.message.caption or query.message.text or "")
            match = re.search(r"\*\*(.+?)\*\*", msg_text)
            extracted_title = match.group(1).strip() if match else ""
            if extracted_title:
                files = await db.search_files(extracted_title)
                if files:
                    grouped_list = group_files_by_title(files, extracted_title)
                    data = {
                        "grouped": grouped_list,
                        "query": extracted_title,
                        "title_page": title_page,
                        "active_idx": None,
                    }
                    AUTOFILTER_RESULTS[query_key] = data

        if not data:
            return await query.answer("Session expired. Please search again.", show_alert=True)

        data["title_page"] = title_page
        data["active_idx"] = None
        grouped_list = data["grouped"]
        query_text = data.get("query", "")

        bot_username = getattr(getattr(client, "me", None), "username", None) or config.file_bot_username
        keyboard = build_title_selection_keyboard(
            grouped_titles=grouped_list,
            query_key=query_key,
            page=title_page,
            page_size=8,
            bot_username=bot_username,
            query_text=query_text,
        )

        match_count = len(grouped_list)
        plural_title = "title" if match_count == 1 else "titles"
        total_matched_files = sum(item["file_count"] for item in grouped_list)
        plural_file = "file" if total_matched_files == 1 else "files"

        text = (
            f"🔍 **Search Results for:** `{query_text}`\n"
            f"Found **{match_count}** {plural_title} ({total_matched_files} {plural_file}) in library.\n\n"
            f"👇 **Select your movie / series below to get files:**"
        )

        chat_id = query.message.chat.id

        try:
            if query.message.photo:
                try:
                    await query.message.delete()
                except RPCError:
                    pass
                sent = await client.send_message(chat_id, text=text, reply_markup=keyboard)
                if config.auto_delete_seconds > 0:
                    asyncio.create_task(
                        _auto_delete_message(client, sent.chat.id, sent.id, config.auto_delete_seconds)
                    )
            else:
                await query.message.edit_text(text=text, reply_markup=keyboard)
        except RPCError as exc:
            logger.warning("Error in back to titles: %s", exc)

    # -------------------------------------------------------------
    # Step 2: File Pagination (page_f:{page}:{query_key})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^page_f:"))
    async def handle_file_pagination(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if user:
            await db.save_user(user.id, user.username, user.first_name)

        parts = query.data.split(":")
        page = int(parts[1])
        query_key = parts[2]

        data = AUTOFILTER_RESULTS.get(query_key)
        if not data:
            return await query.answer("Session expired. Please search again.", show_alert=True)

        active_idx = data.get("active_idx")
        if active_idx is None or active_idx >= len(data["grouped"]):
            return await query.answer("Invalid selection.", show_alert=True)

        selected = data["grouped"][active_idx]
        files = selected["files"]
        meta: MediaMeta | None = selected.get("meta")

        is_admin = bool(user and user.id in config.admin_ids)
        safe_bot = (getattr(getattr(client, "me", None), "username", None) or config.file_bot_username).strip().lstrip("@")
        clean_slug = re.sub(r"[^a-zA-Z0-9]+", "-", selected["title"]).strip("-") or "movie"
        target_link = f"https://t.me/{safe_bot}?start=getfile-{clean_slug}"
        share_text = f"Watch {selected['title']} on iMovies"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(target_link, safe='')}&text={urllib.parse.quote(share_text)}"

        keyboard = build_autofilter_keyboard(
            files=files,
            file_bot_username=config.file_bot_username,
            page=page,
            page_size=8,
            query_key=query_key,
            title_page=data.get("title_page", 1),
            trailer_url=meta.trailer_url if meta else None,
            is_admin=is_admin,
            share_url=share_url,
        )

        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
            await query.answer()
        except RPCError:
            pass

    # -------------------------------------------------------------
    # Step 2 Admin Actions: Post to Updates / Public Channels
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^af_post_up:"))
    async def cb_af_post_updates(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if not user or user.id not in config.admin_ids:
            return await query.answer("⚠️ Admin only feature!", show_alert=True)

        query_key = query.data.split(":")[1]
        data = AUTOFILTER_RESULTS.get(query_key)
        if not data:
            return await query.answer("⚠️ Session expired, please search again.", show_alert=True)

        active_idx = data.get("active_idx")
        if active_idx is None or active_idx >= len(data["grouped"]):
            return await query.answer("⚠️ Invalid title selection.", show_alert=True)

        selected = data["grouped"][active_idx]
        meta = selected.get("meta")
        if not meta:
            return await query.answer("⚠️ No metadata found.", show_alert=True)

        ok = await post_to_updates_channel(client, config, meta)
        if ok:
            await query.answer("🚀 Successfully posted to Updates Channel!", show_alert=True)
        else:
            await query.answer("❌ Failed to post. Check channel ID & bot admin permissions.", show_alert=True)

    @app.on_callback_query(filters.regex(r"^af_post_pub:"))
    async def cb_af_post_public(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if not user or user.id not in config.admin_ids:
            return await query.answer("⚠️ Admin only feature!", show_alert=True)

        query_key = query.data.split(":")[1]
        data = AUTOFILTER_RESULTS.get(query_key)
        if not data:
            return await query.answer("⚠️ Session expired, please search again.", show_alert=True)

        active_idx = data.get("active_idx")
        if active_idx is None or active_idx >= len(data["grouped"]):
            return await query.answer("⚠️ Invalid title selection.", show_alert=True)

        selected = data["grouped"][active_idx]
        meta = selected.get("meta")
        if not meta:
            return await query.answer("⚠️ No metadata found.", show_alert=True)

        ok = await post_to_public_channel(client, config, meta)
        if ok:
            await query.answer("📢 Successfully posted to Public Channel!", show_alert=True)
        else:
            await query.answer("❌ Failed to post. Check channel ID & bot admin permissions.", show_alert=True)
