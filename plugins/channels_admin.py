"""Dynamic library-channel registry.

Replaces the old flow of hardcoding channel ids into the CHANNELS env var
and restarting the bot every time a new source channel is added. An admin
runs /addchannel once with the channel id; the bot asks whether to backfill
the channel's existing files or only index new ones from now on, then
registers it and starts live-indexing - see plugins/channel.py for the
live-indexing side (it watches temp.CHANNELS, which this file keeps in sync
with mongo).

Registration (temp.CHANNELS + mongo) always happens immediately once a mode
is picked, so live-indexing of new posts starts right away regardless of
which button is pressed. Backfill (scanning the channel's whole history) is
opt-in only, since a channel can hold thousands of old files and that scan
is what "responds slowly" for the duration of the run.
"""

import logging

from pyrogram import Client, filters
from pyrogram.errors import ChannelInvalid, UsernameInvalid, UsernameNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery

from info import ADMINS
from database.users_chats_db import db
from utils import temp
from plugins.index import index_files_to_db

logger = logging.getLogger(__name__)


def _parse_chat_ref(raw: str):
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        return raw if raw.startswith("@") else f"@{raw}"


@Client.on_message(filters.command("addchannel") & filters.user(ADMINS))
async def add_channel_cmd(bot, message):
    if len(message.command) < 2:
        return await message.reply(
            "Usage: `/addchannel <channel_id or @username>`\n\n"
            "I must already be an admin in that channel."
        )
    chat_ref = _parse_chat_ref(message.command[1])

    try:
        chat = await bot.get_chat(chat_ref)
    except ChannelInvalid:
        return await message.reply("Can't see that channel - make me an admin there first.")
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("Invalid channel id/username.")
    except Exception as e:
        logger.exception(e)
        return await message.reply(f"Error resolving channel: {e}")

    if await db.is_channel_registered(chat.id):
        temp.CHANNELS.add(chat.id)  # in case it fell out of memory on a restart
        return await message.reply(f"`{chat.title}` is already registered and live-indexing.")

    btn = [
        [InlineKeyboardButton("📥 Backfill existing + future files", callback_data=f"addch#backfill#{chat.id}")],
        [InlineKeyboardButton("🆕 Future files only (skip backfill)", callback_data=f"addch#futureonly#{chat.id}")],
    ]
    await message.reply(
        f"Add `{chat.title}` (`{chat.id}`) as a library channel.\n\n"
        f"Backfill scans its whole history - can take a while and use resources "
        f"if it holds a lot of files. Skip it if you only care about new uploads.",
        reply_markup=InlineKeyboardMarkup(btn),
    )


@Client.on_callback_query(filters.regex(r"^addch#"))
async def add_channel_cb(bot, query: CallbackQuery):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admins only.", show_alert=True)

    _, mode, chat_id = query.data.split("#")
    chat_id = int(chat_id)

    if await db.is_channel_registered(chat_id):
        temp.CHANNELS.add(chat_id)
        return await query.message.edit("Already registered and live-indexing.")

    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title
    except Exception:
        title = str(chat_id)

    await db.add_channel(chat_id, title=title, added_by=query.from_user.id)
    temp.CHANNELS.add(chat_id)  # live-indexing (plugins/channel.py) picks up new posts immediately
    await query.answer()

    if mode == "futureonly":
        await query.message.edit(
            f"Registered `{title}`. No backfill run - only files posted from now on will be indexed."
        )
        return

    status = await query.message.edit(f"Registered `{title}`. Backfilling existing files...")

    last_msg_id = 0
    try:
        async for m in bot.get_chat_history(chat_id, limit=1):
            last_msg_id = m.id
    except Exception as e:
        logger.exception(e)
        return await status.edit(
            f"Registered `{title}` for live-indexing, but backfill couldn't start: {e}\n"
            f"New files posted from now on will still be indexed automatically."
        )

    if last_msg_id:
        await index_files_to_db(last_msg_id, chat_id, status, bot)
    else:
        await status.edit(f"Registered `{title}`. No existing messages to backfill; live-indexing new files now.")


@Client.on_message(filters.command("delchannel") & filters.user(ADMINS))
async def del_channel_cmd(bot, message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/delchannel <channel_id>`")
    chat_ref = _parse_chat_ref(message.command[1])
    try:
        chat_id = int(chat_ref)
    except ValueError:
        return await message.reply("Give the numeric channel id (see /listchannels).")

    removed = await db.remove_channel(chat_id)
    temp.CHANNELS.discard(chat_id)
    if removed:
        await message.reply(f"Stopped indexing `{chat_id}`. Already-saved files are kept.")
    else:
        await message.reply("That channel wasn't registered.")


@Client.on_message(filters.command("listchannels") & filters.user(ADMINS))
async def list_channels_cmd(bot, message):
    channels = await db.get_all_channels()
    if not channels:
        return await message.reply("No library channels registered yet. Use /addchannel.")
    lines = [f"• `{c['_id']}` - {c.get('title') or 'unknown'}" for c in channels]
    await message.reply("**Live-indexed library channels:**\n\n" + "\n".join(lines))
