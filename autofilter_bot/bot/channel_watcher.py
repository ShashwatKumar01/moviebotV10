"""Library Channel Watcher: Detects new uploads, checks TMDB 30-day release date,
and sends 1-click broadcast cards to Admin IDs and Log Channel.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.errors import RPCError

from ..config import Config
from ..core.database import Database
from ..core.release_parse import parse_release
from ..core.tmdb import (
    MediaMeta,
    create_http_session,
    fetch_details_by_id,
    fetch_media_meta,
    is_released_within_days,
)
from .keyboards import build_admin_alert_keyboard
from .channel_poster import post_to_updates_channel, post_to_public_channel

logger = logging.getLogger(__name__)

# Cache of recently alerted TMDB IDs to avoid spamming multiple episodes of same show
ALERTED_TMDB_IDS: set[int] = set()

# State tracking of alert cards: {f"{chat_id}:{message_id}": {"upd": bool, "pub": bool, "trailer": str}}
ALERT_CARD_STATE: dict[str, dict] = {}


def register_channel_watcher(app: Client, config: Config, db: Database) -> None:
    @app.on_message(filters.channel & (filters.document | filters.video))
    async def on_library_upload(client: Client, message: Message) -> None:
        chat_id = message.chat.id
        # Check if chat_id is in config.library_channels or MongoDB dynamic channels
        known_channels = set(config.library_channels)
        try:
            mongo_channels = await db.get_all_channel_ids()
            known_channels.update(mongo_channels)
        except Exception:
            pass

        if known_channels and chat_id not in known_channels:
            return

        media = message.document or message.video
        if not media or not getattr(media, "file_name", None):
            return

        filename = media.file_name
        logger.info("New library file detected in %s: %s", chat_id, filename)

        # Parse title & year
        parsed = parse_release(filename)
        if not parsed.title or len(parsed.title) < 2:
            return

        # TMDB lookup
        meta = await fetch_media_meta(
            config.tmdb_api_key,
            title=parsed.title,
            year=parsed.year,
            is_tv=parsed.is_tv,
        )
        if not meta or not meta.id:
            logger.info("No TMDB match found for %r", parsed.title)
            return

        if meta.id in ALERTED_TMDB_IDS:
            logger.info("TMDB ID %d already alerted recently.", meta.id)
            return

        # Check if released within last 30 days
        if not is_released_within_days(meta, days=30):
            logger.info("Title %r (released %s) is not within 30 days.", meta.title, meta.release_date)
            return

        ALERTED_TMDB_IDS.add(meta.id)
        logger.info("🔥 New 30-day release detected: %s (%s)! Sending admin alerts...", meta.title, meta.release_date)

        # Prepare alert card
        rel_date_str = meta.release_date or "Unknown"
        media_type_label = "TV Series" if meta.media_type == "tv" else "Movie"

        caption = (
            f"🔔 **NEW RELEASE DETECTED IN LIBRARY!** (Within 30 Days)\n\n"
            f"📁 **File:** `{filename}`\n"
            f"🎬 **Title:** **{meta.title}** ({meta.year or 'N/A'})\n"
            f"📺 **Type:** {media_type_label}\n"
            f"📅 **Release Date:** {rel_date_str}\n\n"
            f"Choose where to broadcast this release:"
        )

        keyboard = build_admin_alert_keyboard(
            media_type=meta.media_type,
            tmdb_id=meta.id,
            trailer_url=meta.trailer_url,
            is_upd_posted=False,
            is_pub_posted=False,
        )

        photo = meta.poster_url or config.default_thumbnail_path

        recipients: list[int | str] = []
        if config.log_channel:
            recipients.append(config.log_channel)
        for admin_id in config.admin_ids:
            recipients.append(admin_id)

        for target in recipients:
            try:
                if photo and (photo.startswith("http") or os.path.exists(photo)):
                    sent = await client.send_photo(target, photo=photo, caption=caption, reply_markup=keyboard)
                else:
                    sent = await client.send_message(target, text=caption, reply_markup=keyboard)

                state_key = f"{sent.chat.id}:{sent.id}"
                ALERT_CARD_STATE[state_key] = {
                    "upd": False,
                    "pub": False,
                    "trailer": meta.trailer_url,
                }
                await asyncio.sleep(0.3)
            except RPCError as exc:
                logger.warning("Failed to send alert card to %s: %s", target, exc)

    @app.on_callback_query(filters.regex(r"^post_(upd|pub):"))
    async def on_post_callback(client: Client, query: CallbackQuery) -> None:
        if query.from_user.id not in config.admin_ids:
            return await query.answer("Admins only.", show_alert=True)

        parts = query.data.split(":")
        action = parts[0]  # post_upd or post_pub
        media_type = parts[1]
        tmdb_id = parts[2]

        await query.answer("Fetching full details & posting...")

        async with create_http_session() as session:
            meta = await fetch_details_by_id(session, config.tmdb_api_key, media_type, tmdb_id)

        if not meta:
            return await query.answer("Could not fetch details from TMDB.", show_alert=True)

        msg_key = f"{query.message.chat.id}:{query.message.id}"
        state = ALERT_CARD_STATE.get(msg_key, {"upd": False, "pub": False, "trailer": meta.trailer_url})

        if action == "post_upd":
            success = await post_to_updates_channel(client, config, meta)
            if success:
                state["upd"] = True
                await query.answer("✅ Posted to Updates Channel!", show_alert=True)
            else:
                await query.answer("❌ Failed to post to Updates Channel. Check logs.", show_alert=True)

        elif action == "post_pub":
            success = await post_to_public_channel(client, config, meta)
            if success:
                state["pub"] = True
                await query.answer("✅ Posted to Public Channel!", show_alert=True)
            else:
                await query.answer("❌ Failed to post to Public Channel. Check logs.", show_alert=True)

        # Update button state
        updated_keyboard = build_admin_alert_keyboard(
            media_type=media_type,
            tmdb_id=tmdb_id,
            trailer_url=state.get("trailer"),
            is_upd_posted=state.get("upd", False),
            is_pub_posted=state.get("pub", False),
        )

        try:
            await query.edit_message_reply_markup(reply_markup=updated_keyboard)
        except RPCError:
            pass

