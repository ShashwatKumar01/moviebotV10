"""Channel posting engine for Screenshot 1 & Screenshot 2 templates."""

from __future__ import annotations

import logging
import os
from pyrogram import Client
from pyrogram.errors import RPCError

from ..config import Config
from ..core.tmdb import MediaMeta, format_screenshot_1_caption, format_screenshot_2_caption
from .keyboards import build_screenshot_1_keyboard, build_screenshot_2_keyboard

logger = logging.getLogger(__name__)


async def post_to_updates_channel(client: Client, config: Config, meta: MediaMeta) -> bool:
    """Post to Updates Channel using Screenshot 1 Template:

    Photo: Poster
    Caption: Screenshot 1 Template
    Buttons: [📩 Click to get Files 📩] / [Movie Grp 1] [Movie Grp 2] / [Watch Trailer]
    """
    if not config.update_channel:
        logger.warning("UPDATE_CHANNEL is not configured.")
        return False

    bot_info = getattr(client, "me", None)
    if not bot_info or not getattr(bot_info, "username", None):
        try:
            bot_info = await client.get_me()
            client.me = bot_info
        except Exception:
            pass
    bot_username = getattr(bot_info, "username", None) or ""
    caption = format_screenshot_1_caption(meta)
    keyboard = build_screenshot_1_keyboard(
        bot_username=bot_username,
        query=meta.title,
        updates_channel_link=config.updates_channel_link,
        group_1_url=config.movie_group_1_url,
        group_2_url=config.movie_group_2_url,
        season_number=meta.season_number,
        trailer_url=meta.trailer_url,
    )

    photo = meta.poster_url or config.default_thumbnail_path
    target = config.update_channel

    try:
        if photo and (photo.startswith("http") or os.path.exists(photo)):
            await client.send_photo(target, photo=photo, caption=caption, reply_markup=keyboard)
        else:
            await client.send_message(target, text=caption, reply_markup=keyboard)
        logger.info("Successfully posted %r to Updates Channel (%s)", meta.title, target)
        return True
    except RPCError as exc:
        logger.error("Failed to post to Updates Channel (%s): %s", target, exc)
        return False


async def post_to_public_channel(client: Client, config: Config, meta: MediaMeta) -> bool:
    """Post to Public Channel using Screenshot 2 Template:

    Photo: Poster
    Caption: Screenshot 2 Template (Full storyline, cast, monospace year, disclaimer)
    Buttons: [📩 Click to get Files 📩] / [Movie Group] [Watch Trailer] / [🔔 Join Updates Channel]
    """
    target = config.public_channel or config.update_channel
    if not target:
        logger.warning("PUBLIC_CHANNEL is not configured.")
        return False

    bot_info = getattr(client, "me", None)
    if not bot_info or not getattr(bot_info, "username", None):
        try:
            bot_info = await client.get_me()
            client.me = bot_info
        except Exception:
            pass
    bot_username = getattr(bot_info, "username", None) or ""
    caption = format_screenshot_2_caption(meta)
    keyboard = build_screenshot_2_keyboard(
        bot_username=bot_username,
        query=meta.title,
        updates_channel_link=getattr(config, "updates_channel_invite_link", None) or "https://t.me/+cQxPLyBRxm0wNzM9",
        group_1_url=config.movie_group_1_url,
        group_2_url=config.movie_group_2_url,
        season_number=meta.season_number,
        trailer_url=meta.trailer_url,
    )

    photo = meta.poster_url or config.default_thumbnail_path

    try:
        if photo and (photo.startswith("http") or os.path.exists(photo)):
            await client.send_photo(target, photo=photo, caption=caption, reply_markup=keyboard)
        else:
            await client.send_message(target, text=caption, reply_markup=keyboard)
        logger.info("Successfully posted %r to Public Channel (%s)", meta.title, target)
        return True
    except RPCError as exc:
        logger.error("Failed to post to Public Channel (%s): %s", target, exc)
        return False

