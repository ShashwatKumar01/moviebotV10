"""Admin and general commands for iMoviesAutoFilterBot."""

from __future__ import annotations

import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup

from ..config import Config
from ..core.database import Database
from .scheduler import send_daily_library_digest, send_daily_tmdb_releases

logger = logging.getLogger(__name__)


def register_admin_commands(app: Client, config: Config, db: Database) -> None:

    @app.on_message(filters.command(["start"]))
    async def start_cmd(client: Client, message: Message) -> None:
        user = message.from_user
        name = user.first_name if user else "Friend"

        if user:
            await db.save_user(user.id, user.username, user.first_name)

        # Handle deep links like /start getfile-Kappi-Squad-S01 or /start getfile-Akka
        if len(message.command) > 1:
            raw_param = message.command[1].strip()
            query = raw_param
            for prefix in ("getfile-", "getfile_", "search-", "search_"):
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

        buttons = [
            [
                InlineKeyboardButton("➕ Add Me To Your Group ➕", url=f"https://t.me/{client.me.username}?startgroup=true"),
            ],
            [
                InlineKeyboardButton("📢 Updates Channel", url=config.updates_channel_link),
                InlineKeyboardButton("👥 Movie Group", url=config.movie_group_1_url),
            ],
        ]

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
