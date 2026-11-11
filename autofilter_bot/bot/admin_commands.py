"""Admin and general commands for iMoviesAutoFilterBot."""

from __future__ import annotations

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

from ..config import Config
from ..core.database import Database
from .scheduler import send_daily_library_digest, send_daily_tmdb_releases
from .force_sub import is_user_authorized, send_force_sub_prompt

logger = logging.getLogger(__name__)


def register_admin_commands(app: Client, config: Config, db: Database) -> None:

    @app.on_message(filters.command(["start"]))
    async def start_cmd(client: Client, message: Message) -> None:
        user = message.from_user
        name = user.first_name if user else "Friend"

        if user:
            await db.save_user(user.id, user.username, user.first_name)

        # Handle deep links like /start getfile-Kappi-Squad-S01, /start rel_movie_123, /start new
        if len(message.command) > 1:
            raw_param = message.command[1].strip()

            # 1. /start new or /start releases
            if raw_param.lower() in ("new", "releases"):
                from .release_explorer import trigger_new_releases
                await trigger_new_releases(client, message, config, db)
                return

            # 2. /start today or /start daily
            if raw_param.lower() in ("today", "daily"):
                from .release_explorer import trigger_today_releases
                await trigger_today_releases(client, message, config, db)
                return

            # 3. /start rel_movie_12345 or /start rel_tv_67890
            if raw_param.startswith("rel_"):
                try:
                    rel_parts = raw_param[4:].split("_", 1)
                    if len(rel_parts) == 2:
                        media_type, media_id = rel_parts
                        from ..core.tmdb import create_http_session, fetch_details_by_id, format_autofilter_caption
                        from .keyboards import build_autofilter_keyboard
                        import os

                        async with create_http_session() as session:
                            meta = await fetch_details_by_id(session, config.tmdb_api_key, media_type, media_id)

                        if meta:
                            files = await db.search_files(meta.title, max_results=50)
                            is_admin = bool(user and user.id in config.admin_ids)
                            bot_username = getattr(getattr(client, "me", None), "username", None) or config.file_bot_username
                            photo = meta.poster_url or config.default_thumbnail_path

                            if files:
                                caption = format_autofilter_caption(meta, file_count=len(files), is_admin=is_admin)
                                kb = build_autofilter_keyboard(
                                    files=files,
                                    file_bot_username=config.file_bot_username,
                                    page=1,
                                    page_size=8,
                                    trailer_url=meta.trailer_url,
                                    is_admin=is_admin,
                                )
                            else:
                                caption = format_autofilter_caption(meta, file_count=0, is_admin=is_admin)
                                caption += "\n\nℹ️ **Library Status:** Files not yet in library."
                                buttons = []
                                if meta.trailer_url and meta.trailer_url.strip().startswith(("http://", "https://")) and " " not in meta.trailer_url.strip():
                                    buttons.append([InlineKeyboardButton("▶️ Watch Trailer", url=meta.trailer_url.strip())])
                                safe_bot = bot_username.strip().lstrip("@")
                                buttons.append([
                                    InlineKeyboardButton("🔍 Search Movies", url=f"https://t.me/{safe_bot}?start=home"),
                                    InlineKeyboardButton("🔥 New Releases", url=f"https://t.me/{safe_bot}?start=new"),
                                ])
                                kb = InlineKeyboardMarkup(buttons)

                            if photo and (photo.startswith("http") or os.path.exists(photo)):
                                await message.reply_photo(photo=photo, caption=caption, reply_markup=kb)
                            else:
                                await message.reply_text(text=caption, reply_markup=kb)
                            return
                except Exception as exc:
                    logger.warning("Error resolving rel_ deep link: %s", exc)

            # 4. Search query deep-links (getfile-, search-, q_)
            query = raw_param
            for prefix in ("getfile-", "getfile_", "search-", "search_", "q-", "q_"):
                if query.lower().startswith(prefix):
                    query = query[len(prefix):]
                    break
            query = query.replace("-", " ").replace("_", " ").strip()
            if query:
                from .autofilter import process_search_query
                await process_search_query(client, message, query, config, db)
                return

        text = (
            f"👋 **Hello {name}!**\n\n"
            f"Welcome to **iMovies AutoFilter Bot** 🎬\n\n"
            f"🔍 Just send me any Movie or Series name, or add me to your group, "
            f"and I will instantly find all matching files and qualities for you!\n\n"
            f"⚡️ Browse hot releases: /new or /today\n"
            f"📢 **Join our updates:** {config.updates_channel_link}"
        )

        safe_me = (client.me.username or "").strip().lstrip("@")
        buttons = [
            [
                InlineKeyboardButton("➕ Add Me To Your Group ➕", url=f"https://t.me/{safe_me}?startgroup=true"),
            ],
        ]
        row2 = []
        if config.updates_channel_link and config.updates_channel_link.strip().startswith(("http://", "https://")):
            row2.append(InlineKeyboardButton("📢 Updates Channel", url=config.updates_channel_link.strip()))
        if config.movie_group_1_url and config.movie_group_1_url.strip().startswith(("http://", "https://")):
            row2.append(InlineKeyboardButton("👥 Movie Group", url=config.movie_group_1_url.strip()))
        if row2:
            buttons.append(row2)

        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

    @app.on_message(filters.command(["help"]))
    async def help_cmd(_, message: Message) -> None:
        user = message.from_user
        is_admin = user and user.id in config.admin_ids

        text = (
            "📖 **User Guide:**\n\n"
            "• **In PM:** Simply send the name of any movie or web series.\n"
            "• **In Groups:** Send the movie name in group chat and click the quality buttons to download.\n"
            "• `/new` — Browse recent releases with pagination, media & language filters\n"
            "• `/today` — View today's hot OTT and theatrical releases\n"
        )

        if is_admin:
            text += (
                "\n👑 **Admin Commands:**\n"
                "• `/broadcast` — Broadcast message to all bot users with button attachments, live progress & cancel\n"
                "• `/digest` — Send the Day-End Library Summary immediately\n"
                "• `/check_releases` — Check and send today's TMDB releases immediately\n"
                "• `/stats` — Show database file counts, total bot users & system stats"
            )

        await message.reply_text(text)

    @app.on_message(filters.command(["digest"]) & filters.private)
    async def manual_digest_cmd(client: Client, message: Message) -> None:
        if message.from_user.id not in config.admin_ids:
            return await message.reply_text("❌ Admins only.")

        status = await message.reply_text("⏳ Generating today's library digest...")
        count = await send_daily_library_digest(client, config, db)
        if count == 0:
            await status.edit_text("ℹ️ No new files were added to the library today.")
        else:
            await status.edit_text(f"✅ Library digest sent to Admin & Log Channel! (Found {count} files today).")

    @app.on_message(filters.command(["check_releases"]) & filters.private)
    async def manual_check_releases_cmd(client: Client, message: Message) -> None:
        if message.from_user.id not in config.admin_ids:
            return await message.reply_text("❌ Admins only.")

        status = await message.reply_text("⏳ Checking today's TMDB releases...")
        count = await send_daily_tmdb_releases(client, config)
        await status.edit_text(f"✅ Daily TMDB releases report sent to Admin & Log Channel! (Sent {count} release cards).")

    @app.on_message(filters.command(["stats"]) & filters.private)
    async def stats_cmd(_, message: Message) -> None:
        if message.from_user.id not in config.admin_ids:
            return await message.reply_text("❌ Admins only.")

        status = await message.reply_text("⏳ Fetching database stats...")
        try:
            total_files = await db.files.count_documents({})
            user_count = await db.get_user_count()
            channels = await db.get_all_channel_ids()
            text = (
                f"📊 **Bot & Database Statistics:**\n\n"
                f"📂 **Total Indexed Files:** `{total_files:,}`\n"
                f"👥 **Total Registered Users:** `{user_count:,}`\n"
                f"📡 **Dynamic Library Channels:** `{len(channels)}`\n"
                f"🤖 **File Delivery Bot:** `@{config.file_bot_username}`\n"
            )
            await status.edit_text(text)
        except Exception as exc:
            await status.edit_text(f"❌ Error fetching stats: {exc}")
