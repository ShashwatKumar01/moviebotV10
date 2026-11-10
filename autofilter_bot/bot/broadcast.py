"""Broadcast Engine: Handles admin broadcasting with button attachment support,
interactive preview confirmation, realtime progress updates, and a live [🛑 Cancel Broadcast] button.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from typing import Any
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, RPCError, UserIsBlocked, PeerIdInvalid, InputUserDeactivated

from ..config import Config
from ..core.database import Database
from ..core.tmdb import create_http_session, fetch_details_by_id, format_screenshot_2_caption
from .keyboards import (
    broadcast_preview_keyboard,
    broadcast_progress_keyboard,
    build_screenshot_2_keyboard,
)

logger = logging.getLogger(__name__)

# In-memory storage for broadcast drafts: {draft_id: {...}}
BROADCAST_DRAFTS: dict[str, dict[str, Any]] = {}

# In-memory storage for active broadcast tasks: {task_id: {"cancelled": bool, ...}}
ACTIVE_BROADCASTS: dict[str, dict[str, Any]] = {}


def parse_inline_buttons(text: str) -> tuple[str, list[list[InlineKeyboardButton]]]:
    """Extract inline buttons from broadcast text.
    Supported syntaxes:
    1. [Button Text | https://link]
    2. Button Text - https://link
    Multiple buttons on one line (separated by space or '&&') go into the same row.
    Returns cleaned text and list of button rows.
    """
    lines = text.splitlines()
    clean_lines: list[str] = []
    button_rows: list[list[InlineKeyboardButton]] = []

    # Regex for [Text | URL]
    bracket_re = re.compile(r"\[([^\]\|]+)\|([^\]]+)\]")
    # Regex for Text - http...
    dash_re = re.compile(r"([^|\n\-]+)\s*-\s*(https?://\S+)")

    for line in lines:
        stripped = line.strip()
        bracket_matches = bracket_re.findall(stripped)
        if bracket_matches:
            row: list[InlineKeyboardButton] = []
            for label, url in bracket_matches:
                lbl = label.strip()
                link = url.strip()
                if lbl and link.startswith(("http://", "https://", "tg://")):
                    row.append(InlineKeyboardButton(lbl, url=link))
            if row:
                button_rows.append(row)
            continue

        dash_match = dash_re.match(stripped)
        if dash_match and ("http://" in stripped or "https://" in stripped):
            lbl = dash_match.group(1).strip()
            link = dash_match.group(2).strip()
            if lbl and link.startswith(("http://", "https://", "tg://")):
                button_rows.append([InlineKeyboardButton(lbl, url=link)])
            continue

        clean_lines.append(line)

    clean_text = "\n".join(clean_lines).strip()
    return clean_text, button_rows


def render_progress_bar(current: int, total: int, length: int = 15) -> str:
    """Render a visual ASCII progress bar: [██████░░░░░░░░]"""
    if total <= 0:
        return "[░" * length + "]"
    percent = min(1.0, max(0.0, current / total))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {percent * 100:.1f}%"


def register_broadcast(app: Client, config: Config, db: Database) -> None:

    # -------------------------------------------------------------
    # /broadcast command (Admin only)
    # -------------------------------------------------------------
    @app.on_message(filters.command(["broadcast", "bcast"]) & filters.private)
    async def cmd_broadcast(client: Client, message: Message) -> None:
        if message.from_user.id not in config.admin_ids:
            return await message.reply_text("❌ Admins only.")

        reply = message.reply_to_message
        command_args = (message.text or "").partition(" ")[2].strip()

        if not reply and not command_args:
            return await message.reply_text(
                "📢 **How to use Broadcast:**\n\n"
                "1. **Reply to any message** (Text, Photo, Video, Document) with `/broadcast`\n"
                "2. **Or send text directly:** `/broadcast Your message here`\n\n"
                "🔗 **Attach Buttons Syntax (at the end of text):**\n"
                "`[🎬 Watch Movie | https://t.me/imoviesmagic_bot]`\n"
                "`[📢 Updates | https://t.me/imoviesmagic] [👥 Group | https://t.me/link]`\n"
                "*(Buttons on the same line appear side-by-side)*"
            )

        # Build draft content
        draft_id = str(uuid.uuid4())[:8]
        parsed_buttons: list[list[InlineKeyboardButton]] = []

        if reply:
            source_text = command_args or reply.text or reply.caption or ""
            clean_text, custom_buttons = parse_inline_buttons(source_text)
            if custom_buttons:
                parsed_buttons = custom_buttons
            elif reply.reply_markup and reply.reply_markup.inline_keyboard:
                parsed_buttons = reply.reply_markup.inline_keyboard

            photo = reply.photo.file_id if reply.photo else None
            video = reply.video.file_id if reply.video else None
            document = reply.document.file_id if reply.document else None
        else:
            clean_text, parsed_buttons = parse_inline_buttons(command_args)
            photo = None
            video = None
            document = None

        user_count = await db.get_user_count()

        BROADCAST_DRAFTS[draft_id] = {
            "text": clean_text,
            "buttons": parsed_buttons,
            "photo": photo,
            "video": video,
            "document": document,
            "target_count": user_count,
        }

        # Show Interactive Preview
        preview_markup = broadcast_preview_keyboard(draft_id)
        reply_markup = InlineKeyboardMarkup(parsed_buttons) if parsed_buttons else None

        info_header = (
            f"📢 **BROADCAST PREVIEW**\n"
            f"👥 **Audience:** `{user_count:,}` registered bot users\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )

        try:
            if photo:
                await message.reply_photo(
                    photo=photo,
                    caption=info_header + clean_text,
                    reply_markup=reply_markup,
                )
            elif video:
                await message.reply_video(
                    video=video,
                    caption=info_header + clean_text,
                    reply_markup=reply_markup,
                )
            elif document:
                await message.reply_document(
                    document=document,
                    caption=info_header + clean_text,
                    reply_markup=reply_markup,
                )
            else:
                await message.reply_text(
                    text=info_header + clean_text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )

            # Send confirmation controls
            await message.reply_text(
                "👆 **Check the preview above carefully.**\n"
                "Do you want to broadcast this message to all bot users?",
                reply_markup=preview_markup,
            )

        except RPCError as exc:
            logger.warning("Error showing broadcast preview: %s", exc)
            await message.reply_text(f"❌ Failed to generate preview: {exc}")

    # -------------------------------------------------------------
    # 1-Click Broadcast from Release Card (bcast_rel:{media_type}:{id})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^bcast_rel:"))
    async def cb_bcast_release(client: Client, query: CallbackQuery) -> None:
        if query.from_user.id not in config.admin_ids:
            return await query.answer("Admins only.", show_alert=True)

        parts = query.data.split(":")
        media_type = parts[1]
        media_id = parts[2]

        await query.answer("Preparing broadcast preview...")

        async with create_http_session() as session:
            meta = await fetch_details_by_id(session, config.tmdb_api_key or "", media_type, media_id)

        if not meta:
            return await query.answer("Could not fetch details from TMDB.", show_alert=True)

        draft_id = str(uuid.uuid4())[:8]
        caption = format_screenshot_2_caption(meta)
        bot_info = getattr(client, "me", None)
        if not bot_info or not getattr(bot_info, "username", None):
            try:
                bot_info = await client.get_me()
                client.me = bot_info
            except Exception:
                pass
        bot_username = getattr(bot_info, "username", None) or ""
        markup = build_screenshot_2_keyboard(
            bot_username=bot_username,
            query=meta.title,
            updates_channel_link=config.updates_channel_link,
            group_1_url=config.movie_group_1_url,
            season_number=meta.season_number,
            trailer_url=meta.trailer_url,
        )

        user_count = await db.get_user_count()

        BROADCAST_DRAFTS[draft_id] = {
            "text": caption,
            "buttons": markup.inline_keyboard,
            "photo": meta.poster_url or config.default_thumbnail_path,
            "video": None,
            "document": None,
            "target_count": user_count,
        }

        preview_markup = broadcast_preview_keyboard(draft_id)
        info_header = (
            f"📢 **BROADCAST RELEASE PREVIEW**\n"
            f"👥 **Audience:** `{user_count:,}` registered bot users\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )

        try:
            photo = meta.poster_url or config.default_thumbnail_path
            if photo and (photo.startswith("http") or os.path.exists(photo)):
                await client.send_photo(
                    query.message.chat.id,
                    photo=photo,
                    caption=info_header + caption,
                    reply_markup=markup,
                )
            else:
                await client.send_message(
                    query.message.chat.id,
                    text=info_header + caption,
                    reply_markup=markup,
                )

            await client.send_message(
                query.message.chat.id,
                "👆 **Release Broadcast Preview generated.**\n"
                "Launch broadcast to all bot users now?",
                reply_markup=preview_markup,
            )
        except RPCError as exc:
            logger.warning("Error showing release broadcast preview: %s", exc)

    # -------------------------------------------------------------
    # Confirm & Send Broadcast (send_bcast:{draft_id})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^send_bcast:"))
    async def cb_send_broadcast(client: Client, query: CallbackQuery) -> None:
        if query.from_user.id not in config.admin_ids:
            return await query.answer("Admins only.", show_alert=True)

        draft_id = query.data.split(":")[1]
        draft = BROADCAST_DRAFTS.get(draft_id)
        if not draft:
            return await query.answer("Broadcast draft expired. Please run /broadcast again.", show_alert=True)

        task_id = str(uuid.uuid4())[:8]
        ACTIVE_BROADCASTS[task_id] = {"cancelled": False}

        await query.answer("🚀 Launching broadcast task...")

        # Load all recipient user IDs
        all_users = await db.get_all_user_ids()
        total_users = len(all_users)

        if total_users == 0:
            return await query.edit_message_text("❌ No bot users found in database to broadcast to.")

        status_msg = await query.edit_message_text(
            f"🚀 **Broadcasting in Progress...**\n\n"
            f"{render_progress_bar(0, total_users)}\n"
            f"👥 **Processed:** `0 / {total_users}`\n"
            f"✅ **Delivered:** `0`\n"
            f"❌ **Blocked/Failed:** `0`\n"
            f"⏱ **Elapsed:** `0s`",
            reply_markup=broadcast_progress_keyboard(task_id),
        )

        # Run broadcast worker in background
        asyncio.create_task(
            _run_broadcast_worker(
                client=client,
                task_id=task_id,
                status_msg=status_msg,
                draft=draft,
                user_ids=all_users,
                db=db,
            )
        )

    # -------------------------------------------------------------
    # Cancel Broadcast Draft (cancel_draft:{draft_id})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^cancel_draft:"))
    async def cb_cancel_draft(_: Client, query: CallbackQuery) -> None:
        draft_id = query.data.split(":")[1]
        BROADCAST_DRAFTS.pop(draft_id, None)
        try:
            await query.edit_message_text("❌ **Broadcast cancelled.** No messages were sent.")
        except RPCError:
            pass

    # -------------------------------------------------------------
    # Cancel Active Broadcast (cancel_bcast:{task_id})
    # -------------------------------------------------------------
    @app.on_callback_query(filters.regex(r"^cancel_bcast:"))
    async def cb_cancel_active_broadcast(_: Client, query: CallbackQuery) -> None:
        if query.from_user.id not in config.admin_ids:
            return await query.answer("Admins only.", show_alert=True)

        task_id = query.data.split(":")[1]
        task_state = ACTIVE_BROADCASTS.get(task_id)

        if task_state:
            task_state["cancelled"] = True
            await query.answer("🛑 Cancelling broadcast... Stopping immediately.", show_alert=True)
        else:
            await query.answer("Broadcast is already finished or not active.", show_alert=True)


async def _run_broadcast_worker(
    client: Client,
    task_id: str,
    status_msg: Message,
    draft: dict[str, Any],
    user_ids: list[int],
    db: Database | None = None,
) -> None:
    """Async worker that delivers broadcast messages with rate limiting, error handling,
    live visual progress bar, and cancellation checks.
    """
    total = len(user_ids)
    success = 0
    failed = 0
    blocked = 0
    start_time = time.time()
    last_ui_update = time.time()

    text = draft.get("text", "")
    buttons = draft.get("buttons")
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    photo = draft.get("photo")
    video = draft.get("video")
    document = draft.get("document")

    for idx, uid in enumerate(user_ids, 1):
        # 1. Check for live cancellation
        state = ACTIVE_BROADCASTS.get(task_id)
        if state and state.get("cancelled"):
            elapsed = int(time.time() - start_time)
            try:
                await status_msg.edit_text(
                    f"🛑 **Broadcast Cancelled by Admin!**\n\n"
                    f"{render_progress_bar(idx, total)}\n"
                    f"👥 **Total Processed:** `{idx} / {total}`\n"
                    f"✅ **Delivered:** `{success}`\n"
                    f"❌ **Blocked/Failed:** `{failed + blocked}`\n"
                    f"⏱ **Total Elapsed:** `{elapsed}s`"
                )
            except RPCError:
                pass
            ACTIVE_BROADCASTS.pop(task_id, None)
            return

        # 2. Deliver message & pin in user chat
        try:
            sent_msg = None
            if photo:
                sent_msg = await client.send_photo(uid, photo=photo, caption=text, reply_markup=reply_markup)
            elif video:
                sent_msg = await client.send_video(uid, video=video, caption=text, reply_markup=reply_markup)
            elif document:
                sent_msg = await client.send_document(uid, document=document, caption=text, reply_markup=reply_markup)
            else:
                sent_msg = await client.send_message(uid, text=text, reply_markup=reply_markup, disable_web_page_preview=True)

            if sent_msg:
                try:
                    await client.pin_chat_message(chat_id=uid, message_id=sent_msg.id, both_sides=True)
                except Exception:
                    pass
            success += 1

        except FloodWait as fw:
            logger.warning("FloodWait of %d seconds encountered during broadcast.", fw.value)
            await asyncio.sleep(fw.value + 1)
            # Retry once
            try:
                sent_msg = await client.send_message(uid, text=text, reply_markup=reply_markup)
                if sent_msg:
                    try:
                        await client.pin_chat_message(chat_id=uid, message_id=sent_msg.id, both_sides=True)
                    except Exception:
                        pass
                success += 1
            except Exception:
                failed += 1

        except (UserIsBlocked, InputUserDeactivated):
            blocked += 1
            if db:
                try:
                    await db.delete_user(uid)
                except Exception:
                    pass
        except (PeerIdInvalid, RPCError):
            failed += 1
        except Exception as exc:
            logger.warning("Broadcast send error for user %s: %s", uid, exc)
            failed += 1

        # Safe rate limiting: ~25 msgs/sec
        await asyncio.sleep(0.04)

        # 3. Live UI Progress update every 4 seconds or at completion
        now = time.time()
        if now - last_ui_update >= 4.0 or idx == total:
            last_ui_update = now
            elapsed = int(now - start_time)
            rate = idx / max(1, elapsed)
            remaining_secs = int((total - idx) / max(0.1, rate))

            try:
                await status_msg.edit_text(
                    f"🚀 **Broadcasting in Progress...**\n\n"
                    f"{render_progress_bar(idx, total)}\n"
                    f"👥 **Processed:** `{idx} / {total}`\n"
                    f"✅ **Delivered:** `{success}`\n"
                    f"❌ **Blocked/Failed:** `{blocked + failed}`\n"
                    f"⏱ **Elapsed:** `{elapsed}s` | **Est. Left:** `{remaining_secs}s`",
                    reply_markup=broadcast_progress_keyboard(task_id),
                )
            except RPCError:
                pass

    # 4. Final Completion UI
    total_time = int(time.time() - start_time)
    try:
        await status_msg.edit_text(
            f"✅ **Broadcast Completed Successfully!**\n\n"
            f"👥 **Total Audience:** `{total:,}`\n"
            f"✅ **Successfully Delivered:** `{success:,}`\n"
            f"🚫 **Users Blocked Bot:** `{blocked:,}`\n"
            f"❌ **Failed:** `{failed:,}`\n"
            f"⏱ **Total Execution Time:** `{total_time}s`"
        )
    except RPCError:
        pass

    ACTIVE_BROADCASTS.pop(task_id, None)

