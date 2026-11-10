import asyncio
import datetime
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import RPCError

from database.users_chats_db import db
from info import ADMINS
from utils import broadcast_messages

logger = logging.getLogger(__name__)

# State for tracking running broadcasts and live cancellation
BROADCAST_STATE = {
    "is_running": False,
    "cancelled": False,
    "task_id": 0,
}


def render_progress_bar(current: int, total: int, length: int = 12) -> str:
    """Render a visual ASCII progress bar: [██████░░░░░░] 50.0%"""
    if total <= 0:
        return f"[{'░' * length}] 0.0%"
    percent = min(1.0, max(0.0, current / total))
    filled = int(round(length * percent))
    return f"[{'█' * filled}{'░' * (length - filled)}] {percent * 100:.1f}%"


def broadcast_progress_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Broadcast", callback_data="cancel_secondary_broadcast")]
    ])


@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def verupikkals(bot: Client, message: Message):
    if BROADCAST_STATE["is_running"]:
        return await message.reply_text(
            "⚠️ **A broadcast is already in progress!**\nPlease wait or cancel the running broadcast first."
        )

    b_msg = message.reply_to_message
    total_users = await db.total_users_count()
    if total_users == 0:
        return await message.reply_text("❌ No users found in database to broadcast to.")

    BROADCAST_STATE["is_running"] = True
    BROADCAST_STATE["cancelled"] = False
    BROADCAST_STATE["task_id"] += 1
    current_task_id = BROADCAST_STATE["task_id"]

    sts = await message.reply_text(
        f"🚀 **Broadcast Initializing...**\n\n"
        f"{render_progress_bar(0, total_users)}\n"
        f"👥 **Total Audience:** `{total_users:,}`\n"
        f"⚡ **Processed:** `0 / {total_users}`\n"
        f"✅ **Delivered & Pinned:** `0`\n"
        f"🚫 **Blocked (Removed):** `0`\n"
        f"🗑 **Deleted Accts:** `0`\n"
        f"⏱ **Elapsed:** `0s`",
        reply_markup=broadcast_progress_markup(),
    )

    start_time = time.time()
    last_edit_time = time.time()

    done = 0
    blocked = 0
    deleted = 0
    failed = 0
    success = 0

    try:
        users = await db.get_all_users()
        async for user in users:
            # Check for live cancellation
            if BROADCAST_STATE["cancelled"] or BROADCAST_STATE["task_id"] != current_task_id:
                elapsed = int(time.time() - start_time)
                try:
                    await sts.edit(
                        f"🛑 **Broadcast Cancelled by Admin!**\n\n"
                        f"{render_progress_bar(done, total_users)}\n"
                        f"👥 **Total Processed:** `{done} / {total_users}`\n"
                        f"✅ **Delivered & Pinned:** `{success}`\n"
                        f"🚫 **Blocked (Removed):** `{blocked}`\n"
                        f"🗑 **Deleted Accts:** `{deleted}`\n"
                        f"⚠️ **Failed:** `{failed}`\n"
                        f"⏱ **Total Elapsed:** `{elapsed}s`"
                    )
                except RPCError:
                    pass
                return

            uid = int(user['id'])
            # Send message and pin in user's chat
            pti, sh = await broadcast_messages(uid, b_msg, pin=True)
            if pti:
                success += 1
            else:
                if sh == "Blocked":
                    blocked += 1
                elif sh == "Deleted":
                    deleted += 1
                else:
                    failed += 1

            done += 1

            # Safe rate limiting: ~25 msgs/sec
            await asyncio.sleep(0.04)

            # Update status every 3.5 seconds or on final user
            now = time.time()
            if now - last_edit_time >= 3.5 or done == total_users:
                last_edit_time = now
                elapsed = int(now - start_time)
                rate = done / max(1, elapsed)
                remaining_secs = int((total_users - done) / max(0.1, rate))

                try:
                    await sts.edit(
                        f"🚀 **Broadcast in Progress...**\n\n"
                        f"{render_progress_bar(done, total_users)}\n"
                        f"👥 **Total Audience:** `{total_users:,}`\n"
                        f"⚡ **Processed:** `{done:,} / {total_users:,}`\n"
                        f"✅ **Delivered & Pinned:** `{success:,}`\n"
                        f"🚫 **Blocked (Removed):** `{blocked:,}`\n"
                        f"🗑 **Deleted Accts:** `{deleted:,}`\n"
                        f"⚠️ **Failed:** `{failed:,}`\n\n"
                        f"⏱ **Elapsed:** `{elapsed}s` | **Est. Left:** `{remaining_secs}s`",
                        reply_markup=broadcast_progress_markup(),
                    )
                except RPCError:
                    pass

        total_time = str(datetime.timedelta(seconds=int(time.time() - start_time)))
        try:
            await sts.edit(
                f"✅ **Broadcast Completed Successfully!**\n\n"
                f"👥 **Total Audience:** `{total_users:,}`\n"
                f"✅ **Successfully Delivered & Pinned:** `{success:,}`\n"
                f"🚫 **Blocked Users (Removed from DB):** `{blocked:,}`\n"
                f"🗑 **Deleted Accounts (Removed):** `{deleted:,}`\n"
                f"⚠️ **Failed Deliveries:** `{failed:,}`\n"
                f"⏱ **Completed in:** `{total_time}`"
            )
        except RPCError:
            pass

    finally:
        BROADCAST_STATE["is_running"] = False
        BROADCAST_STATE["cancelled"] = False


@Client.on_callback_query(filters.regex(r"^cancel_secondary_broadcast$"))
async def cancel_secondary_broadcast_cb(bot: Client, query: CallbackQuery):
    if query.from_user.id not in ADMINS:
        return await query.answer("❌ Admins only.", show_alert=True)

    if not BROADCAST_STATE["is_running"]:
        return await query.answer("Broadcast is not currently running.", show_alert=True)

    BROADCAST_STATE["cancelled"] = True
    await query.answer("🛑 Cancelling broadcast... Stopping immediately.", show_alert=True)
