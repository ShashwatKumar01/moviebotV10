"""Modern Stream & Download Template Renderer for iMovies."""

from __future__ import annotations

import logging
import urllib.parse
import aiofiles
import aiohttp

from info import URL, LOG_CHANNEL
from lazybot import LazyPrincessBot
from util.human_readable import humanbytes
from util.file_properties import get_file_ids
from server.exceptions import InvalidHash

logger = logging.getLogger(__name__)


async def render_page(id: int | str, secure_hash: str) -> str:
    """Render modern cinema stream player (req.html) or fast download page (dl.html)."""
    file_data = await get_file_ids(LazyPrincessBot, int(LOG_CHANNEL), int(id))
    if not file_data or file_data.unique_id[:6] != secure_hash:
        logger.debug("Invalid hash %s for message ID %s", secure_hash, id)
        raise InvalidHash

    src = urllib.parse.urljoin(URL, f"{secure_hash}{str(id)}")
    mime = getattr(file_data, "mime_type", "") or ""
    tag = mime.split("/")[0].strip() if "/" in mime else "video"
    if tag not in ("video", "audio"):
        tag = "file"

    file_name = getattr(file_data, "file_name", None) or "Media File"
    raw_size = getattr(file_data, "file_size", 0) or 0
    file_size = humanbytes(raw_size) if raw_size > 0 else "Unknown"

    if tag in ("video", "audio"):
        template_path = "template/req.html"
        heading = f"{'Watch' if tag == 'video' else 'Listen'} {file_name}"
    else:
        template_path = "template/dl.html"
        heading = f"Download {file_name}"

    async with aiofiles.open(template_path, mode="r", encoding="utf-8") as r:
        html = await r.read()

    # Fill template placeholders
    html = html.replace("{{HEADING}}", heading)
    html = html.replace("{{FILE_NAME}}", file_name)
    html = html.replace("{{SRC}}", src)
    html = html.replace("{{FILE_SIZE}}", file_size)
    html = html.replace("{{MIME_TYPE}}", mime if mime else ("video/mp4" if tag == "video" else "audio/mp3"))
    html = html.replace("{{TAG}}", tag)
    html = html.replace("<tag", f"<{tag}").replace("</tag>", f"</{tag}>")

    return html
