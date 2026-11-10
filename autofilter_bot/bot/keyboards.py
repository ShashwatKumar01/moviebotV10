"""Inline keyboards for iMoviesAutoFilterBot:
- Two-step AutoFilter (Title selection list -> Quality download links)
- Releases Explorer (/new & /today with media/country/lang tabs)
- Role-based release preview (regular user vs admin)
- 1-Click channel posting (Screenshot 1 & Screenshot 2 templates)
- Broadcast preview and realtime cancellation
"""

from __future__ import annotations

import math
import re
import urllib.parse
from typing import Any
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.tmdb import MediaMeta


def is_valid_url(url: str | None) -> bool:
    """Validate that a URL string is safe for Telegram inline keyboard buttons."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    return u.startswith(("https://", "http://", "tg://")) and " " not in u


def format_bytes(size: int | float | None) -> str:
    if not size or size <= 0:
        return "N/A"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    s = float(size)
    while s >= 1024 and i < len(units) - 1:
        s /= 1024
        i += 1
    return f"{s:.1f} {units[i]}"


_QUALITY_RE = re.compile(r"\b(2160p|4k|1080p|720p|480p|360p)\b", re.IGNORECASE)


def extract_button_label(file_name: str, file_size: int | None) -> str:
    """Create a clean label like '720p • 1.1 GB' or 'Episode 01 • 450 MB'."""
    q_match = _QUALITY_RE.search(file_name)
    quality = q_match.group(1).upper() if q_match else ""

    ep_match = re.search(r"\b(?:E|Episode)[\.\-_ ]*(\d{1,2})\b", file_name, re.IGNORECASE)
    ep = f"EP {int(ep_match.group(1)):02d}" if ep_match else ""

    size_str = format_bytes(file_size)

    parts = [p for p in (quality, ep) if p]
    if parts:
        prefix = " ".join(parts)
        return f"📁 {prefix} ({size_str})"
    else:
        short = file_name[:24].strip()
        return f"📁 {short} ({size_str})"


# ==========================================
# 1. Two-Step AutoFilter Keyboards
# ==========================================

def build_title_selection_keyboard(
    grouped_titles: list[dict[str, Any]],
    query_key: str,
    page: int = 1,
    page_size: int = 8,
    bot_username: str = "",
    query_text: str = "",
) -> InlineKeyboardMarkup:
    """Step 1: Displays clean buttons for distinct movie/series titles (sorted by year desc and season).
    No TMDB info is rendered here.
    """
    total = len(grouped_titles)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    current_items = grouped_titles[start : start + page_size]

    buttons: list[list[InlineKeyboardButton]] = []

    for item in current_items:
        title = item["title"]
        year = item.get("year")
        season = item.get("season")
        is_tv = item.get("is_tv", False)
        group_idx = item["index"]
        file_count = item.get("file_count", 1)

        icon = "📺" if is_tv else "🎬"
        season_str = f" S{season:02d}" if season is not None else ""
        year_str = f" ({year})" if year else ""
        label = f"{icon} {title}{season_str}{year_str}"
        if file_count > 1:
            label += f" [{file_count}]"

        if len(label) > 42:
            label = label[:41] + "…"

        callback = f"sel_t:{query_key}:{group_idx}"
        buttons.append([InlineKeyboardButton(label, callback_data=callback)])

    # Pagination navigation
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_t:{page-1}:{query_key}"))
        else:
            nav_row.append(InlineKeyboardButton("▪️", callback_data="noop"))

        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_t:{page+1}:{query_key}"))
        else:
            nav_row.append(InlineKeyboardButton("▪️", callback_data="noop"))
        buttons.append(nav_row)

    # Share search button
    if bot_username and query_text:
        clean_slug = re.sub(r"[^a-zA-Z0-9]+", "-", query_text).strip("-") or "search"
        safe_bot = bot_username.strip().lstrip("@")
        target_link = f"https://t.me/{safe_bot}?start=q_{clean_slug}"
        share_text = f"Search {query_text} on iMovies"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(target_link, safe='')}&text={urllib.parse.quote(share_text)}"
        if is_valid_url(share_url):
            buttons.append([InlineKeyboardButton("↗️ Share Search", url=share_url)])

    return InlineKeyboardMarkup(buttons)


def build_autofilter_keyboard(
    files: list[dict[str, Any]],
    file_bot_username: str,
    page: int = 1,
    page_size: int = 8,
    query_key: str = "",
    title_page: int = 1,
    trailer_url: str | None = None,
    is_admin: bool = False,
    share_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Step 2: Quality / File buttons with deep links to secondary file delivery bot."""
    total_files = len(files)
    total_pages = max(1, math.ceil(total_files / page_size))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * page_size
    current_files = files[start_idx : start_idx + page_size]

    buttons: list[list[InlineKeyboardButton]] = []

    # File buttons (1 or 2 per row depending on length)
    clean_file_bot = (file_bot_username or "iMoviesMagic_Bot").strip().lstrip("@") or "iMoviesMagic_Bot"
    row: list[InlineKeyboardButton] = []
    for f in current_files:
        file_id = f.get("_id") or f.get("file_id")
        file_name = f.get("file_name", "File")
        file_size = f.get("file_size")

        label = extract_button_label(file_name, file_size)
        clean_fid = str(file_id).strip()
        file_url = f"https://t.me/{clean_file_bot}?start=file_{clean_fid}"
        btn = InlineKeyboardButton(label, url=file_url)

        if len(label) > 20:
            if row:
                buttons.append(row)
                row = []
            buttons.append([btn])
        else:
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    # File pagination row
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_f:{page-1}:{query_key}"))
        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_f:{page+1}:{query_key}"))
        buttons.append(nav_row)

    # Action row: Watch Trailer, Share & Back to Titles
    action_row: list[InlineKeyboardButton] = []
    if is_valid_url(trailer_url):
        action_row.append(InlineKeyboardButton("▶️ Watch Trailer", url=trailer_url.strip()))
    if is_valid_url(share_url):
        action_row.append(InlineKeyboardButton("↗️ Share", url=share_url.strip()))
    action_row.append(InlineKeyboardButton("🔙 Back to Titles", callback_data=f"back_t:{query_key}:{title_page}"))
    buttons.append(action_row)

    # Admin channel posting row (Only visible when requested by Admin)
    if is_admin and query_key:
        buttons.append([
            InlineKeyboardButton("🚀 Post to Updates Channel", callback_data=f"af_post_up:{query_key}"),
            InlineKeyboardButton("📢 Post to Public Channel", callback_data=f"af_post_pub:{query_key}"),
        ])

    return InlineKeyboardMarkup(buttons)


# ==========================================
# 2. Release Explorer Keyboards (/new & /today)
# ==========================================

def new_releases_keyboard(
    releases: list[MediaMeta],
    page: int,
    total_pages: int,
    media_filter: str = "all",
    country_filter: str = "all",
    lang_filter: str = "all",
) -> InlineKeyboardMarkup:
    """Build inline keyboard for /new releases with pagination, media-type, country,
    and language filter tabs (exact moviemanager UI).
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # 1. Release items (up to 8 per page)
    for item in releases:
        icon = "📺" if item.media_type == "tv" else "🎬"
        year_str = f" ({item.year})" if item.year else ""
        label = f"{icon} {item.title}{year_str}"
        if len(label) > 40:
            label = label[:39] + "…"
        callback = f"prev_rel:{item.media_type}:{item.id}:{page}"
        buttons.append([InlineKeyboardButton(label, callback_data=callback)])

    # 2. Pagination navigation
    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"new_p:{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("▪️", callback_data="noop"))

    nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))

    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"new_p:{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("▪️", callback_data="noop"))
    buttons.append(nav_row)

    # 3. Media Type Filters
    f_all = "• All •" if media_filter == "all" else "All"
    f_movie = "• Movies •" if media_filter == "movie" else "Movies"
    f_tv = "• Series •" if media_filter == "tv" else "Series"
    buttons.append(
        [
            InlineKeyboardButton(f_all, callback_data="new_m:all"),
            InlineKeyboardButton(f_movie, callback_data="new_m:movie"),
            InlineKeyboardButton(f_tv, callback_data="new_m:tv"),
        ]
    )

    # 4. Country / Region Filters
    c_all = "• 🌍 All •" if country_filter == "all" else "🌍 Global"
    c_in = "• 🇮🇳 India •" if country_filter == "in" else "🇮🇳 India"
    c_us = "• 🇺🇸 USA •" if country_filter == "us" else "🇺🇸 USA"
    c_kr = "• 🇰🇷 Korea •" if country_filter == "kr" else "🇰🇷 Korea"
    buttons.append(
        [
            InlineKeyboardButton(c_all, callback_data="new_c:all"),
            InlineKeyboardButton(c_in, callback_data="new_c:in"),
            InlineKeyboardButton(c_us, callback_data="new_c:us"),
            InlineKeyboardButton(c_kr, callback_data="new_c:kr"),
        ]
    )

    # 5. Language Filters
    l_all = "• 🗣 All Lang •" if lang_filter == "all" else "🗣 All Lang"
    l_hi = "• Hindi •" if lang_filter == "hi" else "Hindi"
    l_south = "• South •" if lang_filter == "south" else "South"
    l_en = "• English •" if lang_filter == "en" else "English"
    buttons.append(
        [
            InlineKeyboardButton(l_all, callback_data="new_l:all"),
            InlineKeyboardButton(l_hi, callback_data="new_l:hi"),
            InlineKeyboardButton(l_south, callback_data="new_l:south"),
            InlineKeyboardButton(l_en, callback_data="new_l:en"),
        ]
    )

    # 6. Close
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_new")])

    return InlineKeyboardMarkup(buttons)


def release_preview_keyboard(
    media_type: str,
    media_id: int | str,
    back_page: int = 1,
    is_admin: bool = False,
    is_upd_posted: bool = False,
    is_pub_posted: bool = False,
    trailer_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Preview keyboard with role-based controls:
    - Regular users: Only 'Watch Trailer' and 'Back to Releases'
    - Admins: 'Post to Updates Channel', 'Post to Public Channel', 'Broadcast to Users'
    """
    buttons: list[list[InlineKeyboardButton]] = []

    if is_valid_url(trailer_url):
        buttons.append([InlineKeyboardButton("▶️ Watch Trailer", url=trailer_url.strip())])

    if is_admin:
        # Admin Channel Posting Row 1: Updates Channel
        if is_upd_posted:
            buttons.append([InlineKeyboardButton("✅ Posted to Updates Channel", callback_data="noop")])
        else:
            buttons.append([InlineKeyboardButton("🚀 Post to Updates Channel", callback_data=f"post_rel_upd:{media_type}:{media_id}:{back_page}")])

        # Admin Channel Posting Row 2: Public Channel
        if is_pub_posted:
            buttons.append([InlineKeyboardButton("✅ Posted to Public Channel", callback_data="noop")])
        else:
            buttons.append([InlineKeyboardButton("📢 Post to Public Channel", callback_data=f"post_rel_pub:{media_type}:{media_id}:{back_page}")])

        # Admin Broadcast Row
        buttons.append([InlineKeyboardButton("📣 Broadcast to Users", callback_data=f"bcast_rel:{media_type}:{media_id}")])

    # Always provide 'Back to Releases' button
    buttons.append([InlineKeyboardButton("🔙 Back to Releases", callback_data=f"back_new:{back_page}")])

    return InlineKeyboardMarkup(buttons)


def daily_release_card_keyboard(
    media_type: str,
    media_id: int | str,
    is_upd_posted: bool = False,
    is_pub_posted: bool = False,
    trailer_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Card sent to Admin & Log Channel during daily automated releases check."""
    buttons: list[list[InlineKeyboardButton]] = []

    if is_valid_url(trailer_url):
        buttons.append([InlineKeyboardButton("▶️ Watch Trailer", url=trailer_url.strip())])

    # Row 1: Updates Channel (Screenshot 1 format)
    if is_upd_posted:
        buttons.append([InlineKeyboardButton("✅ Updates Channel: Posted", callback_data="noop")])
    else:
        buttons.append([InlineKeyboardButton("📤 Post to Updates Channel", callback_data=f"post_upd:{media_type}:{tmdb_id_clean(media_id)}")])

    # Row 2: Public Channel (Screenshot 2 format)
    if is_pub_posted:
        buttons.append([InlineKeyboardButton("✅ Public Channel: Posted", callback_data="noop")])
    else:
        buttons.append([InlineKeyboardButton("📢 Post to Public Channel", callback_data=f"post_pub:{media_type}:{tmdb_id_clean(media_id)}")])

    # Row 3: Broadcast to Users
    buttons.append([InlineKeyboardButton("📣 Broadcast to Users", callback_data=f"bcast_rel:{media_type}:{tmdb_id_clean(media_id)}")])

    return InlineKeyboardMarkup(buttons)


def tmdb_id_clean(val: Any) -> str:
    return str(val).strip()


def build_admin_alert_keyboard(
    media_type: str,
    tmdb_id: int | str,
    trailer_url: str | None = None,
    is_upd_posted: bool = False,
    is_pub_posted: bool = False,
) -> InlineKeyboardMarkup:
    """Card sent to Admin & Log Channel when a 30-day release is detected in library."""
    buttons: list[list[InlineKeyboardButton]] = []

    # Row 1: Updates Channel (Screenshot 1)
    if is_upd_posted:
        buttons.append([InlineKeyboardButton("✅ Updates Channel: Posted", callback_data="noop")])
    else:
        buttons.append([InlineKeyboardButton("📤 Post to Updates Channel", callback_data=f"post_upd:{media_type}:{tmdb_id}")])

    # Row 2: Public Channel (Screenshot 2)
    if is_pub_posted:
        buttons.append([InlineKeyboardButton("✅ Public Channel: Posted", callback_data="noop")])
    else:
        buttons.append([InlineKeyboardButton("📢 Post to Public Channel", callback_data=f"post_pub:{media_type}:{tmdb_id}")])

    # Row 3: Broadcast & Trailer
    row: list[InlineKeyboardButton] = [
        InlineKeyboardButton("📣 Broadcast", callback_data=f"bcast_rel:{media_type}:{tmdb_id}")
    ]
    if is_valid_url(trailer_url):
        row.append(InlineKeyboardButton("▶️ Trailer", url=trailer_url.strip()))
    buttons.append(row)

    return InlineKeyboardMarkup(buttons)


# ==========================================
# 3. Channel Post Keyboards (Screenshot 1 & 2)
# ==========================================

def generate_getfile_slug(title: str, season_number: int | None = None) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "-", title.strip()).strip("-") or "movie"
    if season_number:
        clean = f"{clean}-S{season_number:02d}"
    return clean


def build_screenshot_1_keyboard(
    bot_username: str,
    query: str,
    updates_channel_link: str | None = None,
    group_1_url: str | None = None,
    group_2_url: str | None = None,
    season_number: int | None = None,
    trailer_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Exact 4-button keyboard for Updates Channel:
    Row 1: [📩 Click to get Files 📩] -> deep link to Primary Bot
    Row 2: [📢 Join Channel] [👥 Join Group]
    Row 3: [▶️ Watch Trailer]
    """
    clean_bot = (bot_username or "").strip().lstrip("@")
    slug = generate_getfile_slug(query, season_number)
    get_files_url = f"https://t.me/{clean_bot}?start=getfile-{slug}"

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📩 Click to get Files 📩", url=get_files_url)]
    ]

    row2: list[InlineKeyboardButton] = []
    channel_url = updates_channel_link or "https://t.me/imoviesmagic"
    if is_valid_url(channel_url):
        row2.append(InlineKeyboardButton("📢 Join Channel", url=channel_url.strip()))
    if is_valid_url(group_1_url):
        row2.append(InlineKeyboardButton("👥 Join Group", url=group_1_url.strip()))
    elif is_valid_url(group_2_url):
        row2.append(InlineKeyboardButton("👥 Join Group", url=group_2_url.strip()))
    if row2:
        rows.append(row2)

    if is_valid_url(trailer_url):
        rows.append([InlineKeyboardButton("▶️ Watch Trailer", url=trailer_url.strip())])

    return InlineKeyboardMarkup(rows)


def build_screenshot_2_keyboard(
    bot_username: str,
    query: str,
    updates_channel_link: str | None = None,
    group_1_url: str | None = None,
    group_2_url: str | None = None,
    season_number: int | None = None,
    trailer_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Exact keyboard for Public Channel:
    ONLY:
    Row 1: [🔔 Join Updates Channel] -> https://t.me/+cQxPLyBRxm0wNzM9
    Row 2: [▶️ Watch Trailer] (if trailer available)
    NO file links and NO movie group in public channel!
    """
    rows: list[list[InlineKeyboardButton]] = []

    channel_invite = updates_channel_link or "https://t.me/+cQxPLyBRxm0wNzM9"
    if is_valid_url(channel_invite):
        rows.append([InlineKeyboardButton("🔔 Join Updates Channel", url=channel_invite.strip())])

    if is_valid_url(trailer_url):
        rows.append([InlineKeyboardButton("▶️ Watch Trailer", url=trailer_url.strip())])

    return InlineKeyboardMarkup(rows)


# ==========================================
# 4. Broadcast Keyboards
# ==========================================

def broadcast_preview_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    """Confirmation keyboard shown before sending broadcast."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Confirm & Send Broadcast", callback_data=f"send_bcast:{draft_id}")],
        [InlineKeyboardButton("❌ Cancel Broadcast", callback_data=f"cancel_draft:{draft_id}")],
    ])


def broadcast_progress_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Realtime keyboard during active broadcast execution."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Broadcast", callback_data=f"cancel_bcast:{task_id}")],
    ])
