from pyrogram import Client, filters
from database.ia_filterdb import save_file
from utils import temp

media_filter = filters.document | filters.video | filters.audio


async def _is_library_channel(_, __, message):
    return message.chat and message.chat.id in temp.CHANNELS


library_channel_filter = filters.create(_is_library_channel)


@Client.on_message(library_channel_filter & media_filter)
async def media(bot, message):
    """Auto-indexes every file posted in a registered library channel the
    moment it arrives - see plugins/channels_admin.py for how channels get
    registered into temp.CHANNELS (mongo-backed, no restart needed)."""
    for file_type in ("document", "video", "audio"):
        media = getattr(message, file_type, None)
        if media is not None:
            break
    else:
        return

    media.file_type = file_type
    media.caption = message.caption
    await save_file(media)