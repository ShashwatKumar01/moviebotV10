import logging
from pyrogram import Client, filters, enums
from pyrogram.types import ChatJoinRequest
from database.users_chats_db import db
from info import ADMINS
from utils import record_secondary_join_req

logger = logging.getLogger(__name__)

@Client.on_chat_join_request()
async def join_reqs(client, message: ChatJoinRequest):
    try:
        user = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
        if not user or not chat:
            return
        record_secondary_join_req(user.id, chat.id)
        await db.add_join_req(user.id, chat.id)
        logger.info("Recorded secondary join request for user %s in chat %s", user.id, chat.id)
    except Exception as exc:
        logger.warning("Error in secondary join_reqs: %s", exc)

@Client.on_chat_member_updated()
async def member_update_handler(client, update):
    """Automatically purge pending join request when user's membership changes (joins, leaves, or is banned)."""
    try:
        user = getattr(update, "from_user", None)
        new_member = getattr(update, "new_chat_member", None)
        user_id = getattr(getattr(new_member, "user", None), "id", None) or getattr(user, "id", None)
        chat = getattr(update, "chat", None)
        if not user_id or not chat:
            return
        from utils import _SECONDARY_JOIN_REQUESTS
        _SECONDARY_JOIN_REQUESTS.get(int(user_id), set()).discard(int(chat.id))
        await db.req.delete_many({
            "$or": [
                {"id": int(user_id), "chat_id": int(chat.id)},
                {"user_id": int(user_id), "chat_id": int(chat.id)},
            ]
        })
        logger.info("Cleared pending join request for user %s in chat %s due to membership update", user_id, chat.id)
    except Exception as exc:
        logger.warning("Error in member_update_handler: %s", exc)

@Client.on_message(filters.command("delreq") & filters.private & filters.user(ADMINS))
async def del_requests(client, message):
    from utils import _SECONDARY_JOIN_REQUESTS
    _SECONDARY_JOIN_REQUESTS.clear()
    await db.del_join_req()    
    await message.reply("<b>⚙ ꜱᴜᴄᴄᴇꜱꜱғᴜʟʟʏ ᴄʜᴀɴɴᴇʟ ʟᴇғᴛ ᴜꜱᴇʀꜱ ᴅᴇʟᴇᴛᴇᴅ</b>")
