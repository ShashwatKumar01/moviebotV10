"""Force Sub Engine for Primary AutoFilter Bot.

Supports:
- Multiple Public & Private Auth Channels
- Immediate authorization on Chat Join Request (exact logic from TeraDl/main.py)
- In-memory fast cache backed by persistent MongoDB collection
- Seamless 'Try Again' callback to resume user's query/download
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlparse
from typing import Any
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
    ChatJoinRequest,
)
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    UserNotParticipant,
    ChatAdminRequired,
    RPCError,
)

from ..config import Config
from ..core.database import Database

logger = logging.getLogger(__name__)

# user_id -> {chat_id: timestamp}
_join_requests: dict[int, dict[int, float]] = {}
_force_sub_id_cache: dict[str, int] = {}
_force_sub_title_cache: dict[str, str] = {}
_force_sub_url_cache: dict[str, str] = {}


def _now_ts() -> float:
    return time.time()


def record_join_request(user_id: int, chat_id: int, db: Database | None = None) -> None:
    """Record that a user requested to join a private chat."""
    _join_requests.setdefault(int(user_id), {})[int(chat_id)] = _now_ts()
    if db:
        try:
            asyncio.create_task(db.save_join_request(user_id, chat_id))
        except Exception:
            pass


async def has_join_request(user_id: int, chat_id: int, db: Database | None = None) -> bool:
    """Check if a join request is on record in memory or in DB."""
    uid = int(user_id)
    cid = int(chat_id)
    if cid in _join_requests.get(uid, {}):
        return True
    if db:
        found = await db.has_join_request(uid, cid)
        if found:
            _join_requests.setdefault(uid, {})[cid] = _now_ts()
            return True
    return False


def _force_sub_join_url_for(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("@"):
        return f"https://t.me/{raw.lstrip('@')}"
    if raw.startswith("-100"):
        return ""
    return f"https://t.me/{raw}"


def _force_sub_chat_ref_for(raw: str) -> str | int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("-100") and raw[1:].isdigit():
        return int(raw)
    if raw.startswith("@"):
        return raw
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        path = (parsed.path or "").strip("/")
        if not path or path.startswith("+"):
            return None
        return f"@{path.split('/')[0]}"
    if raw.startswith("+"):
        return None
    return f"@{raw}"


def get_force_sub_targets(config: Config) -> list[dict[str, Any]]:
    """Build structured targets from config.auth_channels and config.auth_channel_invite_urls."""
    targets: list[dict[str, Any]] = []
    invites = list(config.auth_channel_invite_urls)

    for idx, raw_val in enumerate(config.auth_channels):
        raw_str = str(raw_val).strip()
        invite = invites[idx] if idx < len(invites) else ""
        ref = _force_sub_chat_ref_for(raw_str)
        url = invite or _force_sub_join_url_for(raw_str)
        targets.append({
            "index": idx,
            "raw": raw_str,
            "ref": ref,
            "url": url,
        })
    return targets


async def get_chat_id_for_target(client: Client, target: dict[str, Any]) -> int | None:
    ref = target.get("ref")
    if ref is None:
        return None
    if isinstance(ref, int):
        return ref
    raw = target["raw"]
    cached = _force_sub_id_cache.get(raw)
    if cached is not None:
        return cached
    try:
        chat = await client.get_chat(ref)
        cid = int(getattr(chat, "id", 0) or 0)
        if cid:
            _force_sub_id_cache[raw] = cid
            return cid
    except Exception:
        pass
    return None


async def is_subscribed_to_target(
    client: Client,
    user_id: int,
    target: dict[str, Any],
    db: Database | None = None,
) -> bool:
    """Verify membership for a single target channel.
    If the user has submitted a join request to a private channel,
    it immediately counts as VERIFIED!
    """
    chat_ref = target.get("ref")
    if chat_ref is None:
        return True

    try:
        m = await client.get_chat_member(chat_ref, user_id)
        if m.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            return True
        if m.status == ChatMemberStatus.BANNED:
            return False
    except UserNotParticipant:
        pass
    except ChatAdminRequired:
        logger.warning("Bot is not admin in Auth Channel %s", chat_ref)
        return False
    except Exception:
        pass

    # Not joined yet: check if user has a pending join request
    cid = await get_chat_id_for_target(client, target)
    if cid and await has_join_request(user_id, cid, db):
        return True

    return False


async def get_missing_force_sub(
    client: Client,
    user_id: int,
    config: Config,
    db: Database | None = None,
) -> list[dict[str, Any]]:
    """Return all channels the user has NOT joined or sent a request to."""
    targets = get_force_sub_targets(config)
    if not targets:
        return []

    checks = await asyncio.gather(
        *[is_subscribed_to_target(client, user_id, t, db) for t in targets],
        return_exceptions=True,
    )
    return [t for t, ok in zip(targets, checks) if ok is not True]


async def is_user_authorized(
    client: Client,
    user_id: int,
    config: Config,
    db: Database | None = None,
) -> bool:
    """Check if user has satisfied all Auth Channels."""
    if user_id in config.admin_ids:
        return True
    missing = await get_missing_force_sub(client, user_id, config, db)
    return len(missing) == 0


async def get_target_title(client: Client, target: dict[str, Any]) -> str:
    raw = target["raw"]
    cached = _force_sub_title_cache.get(raw)
    if cached:
        return cached

    title = ""
    ref = target.get("ref")
    if ref is not None:
        try:
            chat = await client.get_chat(ref)
            title = (getattr(chat, "title", "") or "").strip()
        except Exception:
            title = ""

    if not title:
        title = raw if raw.startswith("@") else f"Channel {target['index'] + 1}"

    _force_sub_title_cache[raw] = title
    return title


async def get_target_url(client: Client, target: dict[str, Any]) -> str:
    url = (target.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        return url

    raw = target["raw"]
    cached = _force_sub_url_cache.get(raw)
    if cached:
        return cached

    ref = target.get("ref")
    if ref is None:
        return ""

    link = ""
    try:
        chat = await client.get_chat(ref)
        link = (getattr(chat, "invite_link", "") or "").strip()
        if not link and getattr(chat, "username", None):
            link = f"https://t.me/{chat.username}"
    except Exception:
        link = ""

    if not link:
        try:
            link = (await client.export_chat_invite_link(ref) or "").strip()
        except Exception:
            link = ""

    if link:
        _force_sub_url_cache[raw] = link
    return link


async def build_force_sub_keyboard(
    client: Client,
    missing: list[dict[str, Any]],
    retry_payload: str = "",
) -> InlineKeyboardMarkup:
    """Create join buttons for all missing channels + Try Again callback."""
    rows: list[list[InlineKeyboardButton]] = []

    for t in missing:
        url = await get_target_url(client, t)
        if not url:
            continue
        title = await get_target_title(client, t)
        rows.append([InlineKeyboardButton(f"📢 Join {title}", url=url)])

    clean_payload = (retry_payload or "").strip()[:40]
    cb_data = f"fsub_try:{clean_payload}" if clean_payload else "fsub_try:home"
    rows.append([InlineKeyboardButton("🔄 Try Again", callback_data=cb_data)])

    return InlineKeyboardMarkup(rows)


async def send_force_sub_prompt(
    client: Client,
    message: Message,
    config: Config,
    db: Database,
    retry_payload: str = "",
) -> None:
    """Send attractive force-sub alert message when user is not authorized."""
    user = message.from_user
    name = user.first_name if user else "Friend"
    missing = await get_missing_force_sub(client, user.id, config, db)

    keyboard = await build_force_sub_keyboard(client, missing, retry_payload=retry_payload)

    text = (
        f"👋 **Hello {name}!**\n\n"
        f"⚠️ **You must join our updates channel to use this bot!**\n\n"
        f"Please click the button(s) below to join / request to join, "
        f"then click **🔄 Try Again** to continue."
    )

    photo = config.default_thumbnail_path
    try:
        import os
        if photo and os.path.exists(photo):
            await message.reply_photo(photo=photo, caption=text, reply_markup=keyboard)
        else:
            await message.reply_text(text=text, reply_markup=keyboard)
    except RPCError as exc:
        logger.warning("Error sending force sub prompt: %s", exc)


def register_force_sub_handlers(app: Client, config: Config, db: Database) -> None:
    """Register live ChatJoinRequest listener and Try Again callback."""

    @app.on_chat_join_request()
    async def cb_chat_join_request(client: Client, join_request: ChatJoinRequest) -> None:
        """Immediate authentication on chat join request (matches TeraDl/main.py)."""
        try:
            user = getattr(join_request, "from_user", None)
            chat = getattr(join_request, "chat", None)
            if user is None or chat is None:
                return
            record_join_request(user.id, chat.id, db)
            logger.info("Recorded join request for user %s in chat %s", user.id, chat.id)
        except Exception as exc:
            logger.warning("Error in force_sub_join_request: %s", exc)

    @app.on_callback_query(filters.regex(r"^fsub_try:"))
    async def cb_fsub_try(client: Client, query: CallbackQuery) -> None:
        user = query.from_user
        if not user:
            return

        missing = await get_missing_force_sub(client, user.id, config, db)
        if missing:
            names = ", ".join([await get_target_title(client, t) for t in missing])
            await query.answer(f"❌ You have not joined yet! Please join: {names}", show_alert=True)
            try:
                kb = await build_force_sub_keyboard(client, missing, retry_payload=query.data.split(":", 1)[1])
                await query.edit_message_reply_markup(reply_markup=kb)
            except Exception:
                pass
            return

        # User is verified!
        await query.answer("✅ Verified successfully!", show_alert=False)
        payload = query.data.split(":", 1)[1].strip()

        try:
            await query.message.delete()
        except Exception:
            pass

        if payload and payload != "home":
            # Resume search query
            from .autofilter import process_search_query
            await process_search_query(client, query.message, payload, config, db)
        else:
            await client.send_message(
                query.message.chat.id,
                "✅ **You are now verified!**\n\n🔍 Send any Movie or Web Series name to search.",
            )
