"""TMDB API Integration for iMoviesAutoFilterBot.

Supports:
- Screenshot 1 Template (Updates Channel)
- Screenshot 2 Template (Public Channel)
- AutoFilter Search Template (Groups & PM)
- 30-Day New Release Detection
- Official YouTube Trailer Extraction
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .cache import TMDB_CACHE

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"
_POSTER_BASE_URL = "https://image.tmdb.org/t/p/w780"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=12)
_CAST_LIMIT = 5
_CAPTION_LIMIT = 1024


def create_http_session(timeout: aiohttp.ClientTimeout | None = None) -> aiohttp.ClientSession:
    """Use ThreadedResolver to bypass Windows/Koyeb DNS issues with aiodns."""
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    return aiohttp.ClientSession(connector=connector, timeout=timeout or _REQUEST_TIMEOUT)


_MONOSPACE_DIGITS = {
    "0": "𝟶", "1": "𝟷", "2": "𝟸", "3": "𝟹", "4": "𝟺",
    "5": "𝟻", "6": "𝟼", "7": "𝟽", "8": "𝟾", "9": "𝟿",
}


def to_monospace_digits(text: str | int | None) -> str:
    if text is None:
        return ""
    return "".join(_MONOSPACE_DIGITS.get(ch, ch) for ch in str(text))


def format_release_date(raw_date: str | None) -> str:
    if not raw_date:
        return ""
    try:
        dt = datetime.strptime(raw_date.strip()[:10], "%Y-%m-%d")
        return dt.strftime("%d %B %Y")
    except Exception:
        return raw_date.strip()


GENRE_EMOJIS = {
    "romance": "💕",
    "survival": "😱",
    "drama": "🎭",
    "fantasy": "🧚♀",
    "horror": "👻",
    "mystery": "👀",
    "action": "💥",
    "adventure": "🧗",
    "animation": "🎨",
    "comedy": "😂",
    "crime": "🕵️",
    "documentary": "📜",
    "family": "👨‍👩‍👧‍👦",
    "history": "🏛",
    "music": "🎵",
    "science fiction": "🚀",
    "sci-fi": "🚀",
    "thriller": "😱",
    "war": "⚔️",
    "western": "🤠",
}

ISO_LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "kn": "Kannada",
    "bn": "Bengali",
    "mr": "Marathi",
    "ko": "Korean",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
}

COUNTRY_CODE_MAP = {
    "in": "IN",
    "india": "IN",
    "us": "US",
    "usa": "US",
    "kr": "KR",
    "korea": "KR",
    "jp": "JP",
    "japan": "JP",
    "uk": "GB",
    "gb": "GB",
}

LANGUAGE_CODE_MAP = {
    "hi": "hi",
    "hindi": "hi",
    "south": "ta|te|ml|kn",
    "ta": "ta",
    "tamil": "ta",
    "te": "te",
    "telugu": "te",
    "ml": "ml",
    "malayalam": "ml",
    "kn": "kn",
    "kannada": "kn",
    "en": "en",
    "english": "en",
    "ko": "ko",
    "korean": "ko",
    "ja": "ja",
    "japanese": "ja",
}


@dataclass
class MediaMeta:
    title: str
    year: str | None
    overview: str
    poster_url: str | None
    cast: list[str] = field(default_factory=list)
    media_type: str = "movie"  # "movie" or "tv"
    genres: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    duration: str = ""
    episode_count: int | None = None
    season_number: int | None = None
    id: int | None = None
    popularity: float = 0.0
    release_date: str = ""
    rating: float = 0.0
    trailer_url: str | None = None


DISCLAIMER_TEXT = "ℹ️ Info via TMDB • No copyright infringement intended."


def extract_trailer_url(data: dict) -> str | None:
    videos = data.get("videos", {}).get("results") or []
    if not isinstance(videos, list) or not videos:
        return None

    # Priority 1: Official Trailer on YouTube
    for v in videos:
        if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("official") and v.get("key"):
            return f"https://www.youtube.com/watch?v={v['key']}"

    # Priority 2: Any Trailer on YouTube
    for v in videos:
        if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("key"):
            return f"https://www.youtube.com/watch?v={v['key']}"

    # Priority 3: Teaser on YouTube
    for v in videos:
        if v.get("site") == "YouTube" and v.get("type") == "Teaser" and v.get("key"):
            return f"https://www.youtube.com/watch?v={v['key']}"

    # Priority 4: Any video
    for v in videos:
        if v.get("site") == "YouTube" and v.get("key"):
            return f"https://www.youtube.com/watch?v={v['key']}"

    return None


def is_released_within_days(meta: MediaMeta, days: int = 30) -> bool:
    """Check if the media was released within the past N days (strictly default 30 days)."""
    now = datetime.now(timezone.utc)
    if not meta.release_date:
        return False
    try:
        rel_dt = datetime.strptime(meta.release_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        earliest = now - timedelta(days=days)
        return earliest <= rel_dt <= (now + timedelta(days=14))
    except Exception:
        return False


def format_screenshot_1_caption(meta: MediaMeta) -> str:
    """Template for Screenshot 1 (NEW LATEST OTt RELEASE / Updates Channel):

    📺 TITLE : The Holiday Spark (2026)
    🎬 TYPE : MOVIE
    🍿 GENRE: #Romance 💕
    🎧 LANGUAGE: #English
    ⏳ DURATION : 1h 26mins
    """
    title_str = meta.title.strip()
    year_str = f"({meta.year})" if meta.year else ""
    line1 = f"📺 TITLE : {title_str} {year_str}".strip()

    if meta.media_type == "tv":
        ep_count = f"( {meta.episode_count} Episodes )" if meta.episode_count else ""
        line2 = f"🎬 TYPE : TV SERIES {ep_count}".strip()
    else:
        line2 = "🎬 TYPE : MOVIE"

    genres_str = " ".join(f"#{re.sub(r'[^A-Za-z0-9]', '', g)} {GENRE_EMOJIS.get(g.lower(), '🎬')}" for g in meta.genres[:3])
    line3 = f"🍿 GENRE: {genres_str}" if genres_str else "🍿 GENRE: #Entertainment 🎬"

    langs_str = " ".join(f"#{re.sub(r'[^A-Za-z0-9]', '', l)}" for l in meta.languages[:3])
    line4 = f"🎧 LANGUAGE: {langs_str}" if langs_str else "🎧 LANGUAGE: #English"

    dur_str = meta.duration if meta.duration else "N/A"
    line5 = f"⏳ DURATION : {dur_str}"

    return f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}"


def format_screenshot_2_caption(meta: MediaMeta) -> str:
    """Template for Screenshot 2 (iMoviesMagic Channel / Public Channel):

    ⚡️ TITIL : Chaar Yaar S01 (2026)
    🎬 TYPE : TV SERIES ( 8 Epsosides )
    🍿 GENRE: #Drama 🎭
    🎧 LANGUAGE : #Hindi
    ⏳ DURATION : 33 - 44mins
    📅 RELEASE DATE : 23 October 2026

    {Plot / Overview}

    Cast: {Cast}

    ℹ️ Info via TMDB • No copyright infringement intended.
    """
    title_display = meta.title.strip()
    is_tv = meta.media_type == "tv"
    if is_tv and not re.search(r"S\d+", title_display, re.IGNORECASE):
        season_num = meta.season_number or 1
        title_display = f"{title_display} S{season_num:02d}"

    year_str = to_monospace_digits(meta.year) if meta.year else ""
    title_line = f"⚡️ TITIL : {title_display} ({year_str})" if year_str else f"⚡️ TITIL : {title_display}"

    if is_tv:
        type_line = f"🎬 TYPE : TV SERIES ( {meta.episode_count} Epsosides )" if meta.episode_count else "🎬 TYPE : TV SERIES"
    else:
        type_line = "🎬 TYPE : MOVIE"

    genres_list = [f"#{re.sub(r'[^A-Za-z0-9]', '', g)} {GENRE_EMOJIS.get(g.lower(), '🎬')}" for g in meta.genres]
    genre_line = "🍿 GENRE: " + " ".join(genres_list) if genres_list else "🍿 GENRE: #Entertainment 🎬"

    langs_list = [f"#{re.sub(r'[^A-Za-z0-9]', '', l)}" for l in meta.languages]
    lang_line = "🎧 LANGUAGE : " + " ".join(langs_list) if langs_list else "🎧 LANGUAGE : #English"

    dur_str = meta.duration if meta.duration else "N/A"
    duration_line = f"⏳ DURATION : {dur_str}"

    header_lines = [title_line, type_line, genre_line, lang_line, duration_line]
    rel_date = format_release_date(meta.release_date)
    if rel_date:
        header_lines.append(f"📅 RELEASE DATE : {rel_date}")

    header = "\n".join(header_lines)
    cast_str = "Cast: " + ", ".join(meta.cast[:_CAST_LIMIT]) if meta.cast else "Cast: N/A"
    overview_str = meta.overview.strip() or "No description available."

    full_caption = f"{header}\n\n{overview_str}\n\n{cast_str}\n\n{DISCLAIMER_TEXT}"
    if len(full_caption) > _CAPTION_LIMIT:
        fixed_len = len(header) + len(cast_str) + len(DISCLAIMER_TEXT) + 6
        max_overview = _CAPTION_LIMIT - fixed_len - 3
        if max_overview > 30:
            overview_str = overview_str[:max_overview].rstrip() + "…"
            full_caption = f"{header}\n\n{overview_str}\n\n{cast_str}\n\n{DISCLAIMER_TEXT}"
        else:
            full_caption = full_caption[: _CAPTION_LIMIT - 1].rstrip() + "…"

    return full_caption


# Alias for compatibility with moviemanager and release preview
format_media_caption = format_screenshot_2_caption


def format_autofilter_caption(meta: MediaMeta, file_count: int = 1, is_admin: bool = False) -> str:
    """Rich card sent in Group/PM when user searches a movie/series.
    Includes Release Date above Duration, and for Admins includes Plot and Cast details.
    """
    title_str = meta.title.strip()
    year_str = f"({meta.year})" if meta.year else ""
    rating_str = f"🌟 Rating: {meta.rating:.1f}/10" if meta.rating else ""
    genres_str = ", ".join(meta.genres[:3]) if meta.genres else "Entertainment"

    lines = [
        f"🎬 **{title_str} {year_str}**",
    ]
    if rating_str:
        lines.append(rating_str)
    lines.append(f"🎭 **Genres:** {genres_str}")

    # Release date above duration
    rel_date = format_release_date(meta.release_date)
    if rel_date:
        lines.append(f"📅 **Release Date:** {rel_date}")

    if meta.duration:
        lines.append(f"⏳ **Duration:** {meta.duration}")

    # Extended details for Admin requests
    if is_admin:
        if meta.overview:
            plot_text = meta.overview.strip()
            if len(plot_text) > 300:
                plot_text = plot_text[:300].rstrip() + "…"
            lines.append(f"\n📖 **Plot:** {plot_text}")
        if meta.cast:
            lines.append(f"👥 **Cast:** {', '.join(meta.cast[:5])}")
        if meta.languages:
            lines.append(f"🎧 **Languages:** {', '.join(meta.languages)}")

    lines.append(f"\n📂 **Available Files:** {file_count} file(s) found in library.")
    lines.append("\n👇 **Select your preferred quality below to get file:**")

    return "\n".join(lines)


def _extract_media_meta(data: dict, media_type: str) -> MediaMeta:
    is_tv = media_type == "tv"
    title = (data.get("name") if is_tv else data.get("title")) or data.get("original_title") or "Unknown"
    rel_date = (data.get("first_air_date") if is_tv else data.get("release_date")) or ""
    year = rel_date[:4] if rel_date else None
    overview = data.get("overview") or ""
    poster_path = data.get("poster_path")
    poster_url = f"{_POSTER_BASE_URL}{poster_path}" if poster_path else None

    # Cast
    credits_data = data.get("credits") or {}
    cast_list = [m["name"] for m in credits_data.get("cast", [])[:_CAST_LIMIT] if m.get("name")]

    # Genres
    genres = [g["name"] for g in data.get("genres", []) if g.get("name")]

    # Languages
    spoken = [lang.get("english_name") or lang.get("name") for lang in data.get("spoken_languages", []) if lang.get("english_name") or lang.get("name")]
    if not spoken:
        orig = data.get("original_language", "")
        if orig:
            spoken = [ISO_LANG_NAMES.get(orig, orig.title())]
    if not spoken:
        spoken = ["English"]

    # Duration & Episode Count
    duration_str = ""
    episode_count = None
    season_number = None

    if is_tv:
        runtimes = data.get("episode_run_time") or []
        if len(runtimes) >= 2:
            duration_str = f"{min(runtimes)} - {max(runtimes)}mins"
        elif len(runtimes) == 1:
            duration_str = f"{runtimes[0]} mins"
        else:
            duration_str = "33 - 44mins"

        episode_count = data.get("number_of_episodes")
        seasons = data.get("seasons") or []
        for s in seasons:
            if s.get("season_number") == 1 and s.get("episode_count"):
                episode_count = s["episode_count"]
                season_number = 1
                break
        if not season_number:
            season_number = data.get("number_of_seasons") or 1
    else:
        rt = data.get("runtime")
        if rt and rt > 0:
            h = rt // 60
            m = rt % 60
            duration_str = f"{h}h {m:02d}mins" if h > 0 else f"{m} mins"
        else:
            duration_str = "N/A"

    rating = float(data.get("vote_average", 0.0))
    popularity = float(data.get("popularity", 0.0))
    trailer_url = extract_trailer_url(data)

    return MediaMeta(
        id=data.get("id"),
        title=title,
        year=year,
        overview=overview,
        poster_url=poster_url,
        cast=cast_list,
        media_type=media_type,
        genres=genres,
        languages=spoken,
        duration=duration_str,
        episode_count=episode_count,
        season_number=season_number,
        popularity=popularity,
        release_date=rel_date,
        rating=rating,
        trailer_url=trailer_url,
    )


async def fetch_details_by_id(session: aiohttp.ClientSession, api_key: str, media_type: str, media_id: int | str) -> MediaMeta | None:
    cache_key = f"tmdb_id:{media_type}:{media_id}"
    cached = TMDB_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        url = f"{_BASE_URL}/{media_type}/{media_id}"
        params = {"api_key": api_key, "append_to_response": "credits,videos"}
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                meta = _extract_media_meta(data, media_type)
                TMDB_CACHE.set(cache_key, meta, ttl_seconds=1800)
                return meta
    except Exception as exc:
        logger.warning("Error fetching TMDB details for %s/%s: %s", media_type, media_id, exc)
    return None


def _title_similarity(target: str, candidate: str) -> float:
    t = re.sub(r"[^\w\s]", "", target.lower()).strip()
    c = re.sub(r"[^\w\s]", "", candidate.lower()).strip()
    if not t or not c:
        return 0.0
    if t == c:
        return 1.0
    # Exact word phrase inside candidate (e.g. "love always" inside "Love Always S01")
    if re.search(r"\b" + re.escape(t) + r"\b", c):
        return 0.90
    return difflib.SequenceMatcher(None, t, c).ratio()


async def fetch_media_meta(api_key: str, title: str, year: str | None = None, is_tv: bool = False) -> MediaMeta | None:
    """Fetch accurate movie or TV series metadata from TMDB using multi-search
    and title similarity scoring to prevent fetching unrelated titles.
    """
    clean_title = title.strip()
    if not clean_title:
        return None

    cache_key = f"tmdb_search:{clean_title.lower()}:{year or ''}:{is_tv}"
    cached = TMDB_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        async with create_http_session() as session:
            raw_candidates: list[dict[str, Any]] = []

            # 1. Search multi (searches both movies and TV shows simultaneously)
            async with session.get(
                f"{_BASE_URL}/search/multi",
                params={"api_key": api_key, "query": clean_title, "include_adult": "false"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw_candidates.extend(
                        [r for r in (data.get("results") or []) if r.get("media_type") in ("movie", "tv")]
                    )

            # If multi search returned empty, query tv and movie endpoints separately
            if not raw_candidates:
                for m_type in (("tv", "movie") if is_tv else ("movie", "tv")):
                    async with session.get(
                        f"{_BASE_URL}/search/{m_type}",
                        params={"api_key": api_key, "query": clean_title},
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data.get("results") or []:
                                item["media_type"] = m_type
                                raw_candidates.append(item)

            if not raw_candidates:
                return None

            # 2. Score candidates by title similarity + year match + language + media type match
            best_candidate: dict[str, Any] | None = None
            best_score = 0.0

            for cand in raw_candidates:
                cand_title = cand.get("title") or cand.get("name") or ""
                cand_orig = cand.get("original_title") or cand.get("original_name") or ""
                sim = max(_title_similarity(clean_title, cand_title), _title_similarity(clean_title, cand_orig))

                # Discard candidates with low similarity (< 0.50) to prevent completely wrong cards
                if sim < 0.50:
                    continue

                score = sim * 100.0

                # Release year match bonus
                cand_date = cand.get("release_date") or cand.get("first_air_date") or ""
                cand_year = cand_date[:4] if cand_date else None
                if year and cand_year:
                    if cand_year == str(year):
                        score += 30.0
                    elif str(cand_year).isdigit() and str(year).isdigit() and abs(int(cand_year) - int(year)) <= 1:
                        score += 15.0

                # Indian language / Hindi bonus
                orig_lang = cand.get("original_language", "")
                if orig_lang in ("hi", "te", "ta", "ml", "kn"):
                    score += 15.0

                # Media type bonus
                cand_type = cand.get("media_type", "movie")
                if is_tv and cand_type == "tv":
                    score += 15.0
                elif not is_tv and cand_type == "movie":
                    score += 5.0

                # Popularity tie-breaker
                score += min(10.0, float(cand.get("popularity", 0.0)) / 10.0)

                if score > best_score:
                    best_score = score
                    best_candidate = cand

            if best_candidate and best_candidate.get("id"):
                matched_type = best_candidate.get("media_type", "movie" if not is_tv else "tv")
                meta = await fetch_details_by_id(session, api_key, matched_type, best_candidate["id"])
                if meta:
                    TMDB_CACHE.set(cache_key, meta, ttl_seconds=1800)
                    return meta

    except Exception as exc:
        logger.warning("Error fetching TMDB meta for %r: %s", clean_title, exc)

    return None


async def fetch_recent_releases(
    api_key: str,
    days: int = 15,
    media_filter: str = "all",
    country: str = "all",
    lang: str = "all",
) -> list[MediaMeta]:
    """Fetch releases from the last N days (default 15 days), optionally filtered
    by media type ('all', 'movie', 'tv'), country ('IN', 'US', etc.), and language.
    """
    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    country_code = (
        COUNTRY_CODE_MAP.get(country.lower(), country.upper())
        if country and country.lower() != "all"
        else None
    )
    lang_code = (
        LANGUAGE_CODE_MAP.get(lang.lower(), lang.lower())
        if lang and lang.lower() != "all"
        else None
    )

    raw_items: list[tuple[str, dict]] = []

    try:
        async with create_http_session() as session:
            # 1. Fetch Movies (OTT Digital & Theatrical releases, excluding TV broadcast)
            if media_filter in ("all", "movie"):
                movie_params = {
                    "api_key": api_key,
                    "primary_release_date.gte": start_date,
                    "primary_release_date.lte": end_date,
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "page": 1,
                }
                if country_code:
                    movie_params["with_origin_country"] = country_code
                if lang_code:
                    movie_params["with_original_language"] = lang_code

                async with session.get(f"{_BASE_URL}/discover/movie", params=movie_params) as resp:
                    if resp.status == 200:
                        m_data = await resp.json()
                        for item in m_data.get("results", []):
                            raw_items.append(("movie", item))

            # 2. Fetch TV Series (OTT Web Series only - excluding Soap, Talk, News, Reality)
            if media_filter in ("all", "tv"):
                tv_params = {
                    "api_key": api_key,
                    "first_air_date.gte": start_date,
                    "first_air_date.lte": end_date,
                    "sort_by": "popularity.desc",
                    "include_adult": "false",
                    "without_genres": "10766,10767,10763,10764",
                    "page": 1,
                }
                if country_code:
                    tv_params["with_origin_country"] = country_code
                if lang_code:
                    tv_params["with_original_language"] = lang_code

                async with session.get(f"{_BASE_URL}/discover/tv", params=tv_params) as resp:
                    if resp.status == 200:
                        t_data = await resp.json()
                        for item in t_data.get("results", []):
                            raw_items.append(("tv", item))
            # Fallback to broader search if specific country returned empty
            if not raw_items and country_code:
                if media_filter in ("all", "movie"):
                    fb_m = {
                        "api_key": api_key,
                        "primary_release_date.gte": start_date,
                        "primary_release_date.lte": end_date,
                        "sort_by": "popularity.desc",
                        "include_adult": "false",
                        "page": 1,
                    }
                    if lang_code:
                        fb_m["with_original_language"] = lang_code
                    async with session.get(f"{_BASE_URL}/discover/movie", params=fb_m) as resp:
                        if resp.status == 200:
                            m_data = await resp.json()
                            for item in m_data.get("results", []):
                                raw_items.append(("movie", item))
                if media_filter in ("all", "tv"):
                    fb_t = {
                        "api_key": api_key,
                        "first_air_date.gte": start_date,
                        "first_air_date.lte": end_date,
                        "sort_by": "popularity.desc",
                        "include_adult": "false",
                        "without_genres": "10766,10767,10763,10764",
                        "page": 1,
                    }
                    if lang_code:
                        fb_t["with_original_language"] = lang_code
                    async with session.get(f"{_BASE_URL}/discover/tv", params=fb_t) as resp:
                        if resp.status == 200:
                            t_data = await resp.json()
                            for item in t_data.get("results", []):
                                raw_items.append(("tv", item))
    except Exception as exc:
        logger.warning("Error fetching recent releases from TMDB: %s", exc)

    raw_items.sort(key=lambda pair: float(pair[1].get("popularity", 0.0)), reverse=True)

    releases: list[MediaMeta] = []
    excluded_genre_ids = {10766, 10767, 10763, 10764}

    for m_type, item in raw_items:
        genre_ids = set(item.get("genre_ids") or [])
        if genre_ids.intersection(excluded_genre_ids):
            continue

        title = (item.get("name") if m_type == "tv" else item.get("title")) or "Unknown"
        rel_date = (item.get("first_air_date") if m_type == "tv" else item.get("release_date")) or ""

        if rel_date and not (start_date <= rel_date <= end_date):
            continue

        year = rel_date[:4] if rel_date else None
        poster_path = item.get("poster_path")
        poster_url = f"{_POSTER_BASE_URL}{poster_path}" if poster_path else None

        releases.append(
            MediaMeta(
                id=item.get("id"),
                title=title,
                year=year,
                overview=item.get("overview") or "",
                poster_url=poster_url,
                media_type=m_type,
                popularity=float(item.get("popularity", 0.0)),
                release_date=rel_date,
            )
        )

    return releases


async def fetch_daily_releases(
    api_key: str,
    target_date: str | None = None,
    country: str = "all",
    lang: str = "all",
) -> list[MediaMeta]:
    """Fetch releases for today (or target_date) and retrieve full details."""
    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    country_code = (
        COUNTRY_CODE_MAP.get(country.lower(), country.upper())
        if country and country.lower() != "all"
        else None
    )
    lang_code = (
        LANGUAGE_CODE_MAP.get(lang.lower(), lang.lower())
        if lang and lang.lower() != "all"
        else None
    )

    candidate_ids: list[tuple[str, int]] = []

    try:
        async with create_http_session() as session:
            movie_params = {
                "api_key": api_key,
                "primary_release_date.gte": target_date,
                "primary_release_date.lte": target_date,
                "sort_by": "popularity.desc",
                "include_adult": "false",
            }
            if country_code:
                movie_params["with_origin_country"] = country_code
            if lang_code:
                movie_params["with_original_language"] = lang_code

            async with session.get(f"{_BASE_URL}/discover/movie", params=movie_params) as resp:
                if resp.status == 200:
                    m_data = await resp.json()
                    for m in m_data.get("results", [])[:5]:
                        if m.get("id"):
                            candidate_ids.append(("movie", m["id"]))

            tv_params = {
                "api_key": api_key,
                "first_air_date.gte": target_date,
                "first_air_date.lte": target_date,
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "without_genres": "10766,10767,10763,10764",
            }
            if country_code:
                tv_params["with_origin_country"] = country_code
            if lang_code:
                tv_params["with_original_language"] = lang_code

            async with session.get(f"{_BASE_URL}/discover/tv", params=tv_params) as resp:
                if resp.status == 200:
                    t_data = await resp.json()
                    for t in t_data.get("results", [])[:5]:
                        if t.get("id"):
                            candidate_ids.append(("tv", t["id"]))

            detailed_releases: list[MediaMeta] = []
            current_year = datetime.now(timezone.utc).year
            for m_type, m_id in candidate_ids:
                meta = await fetch_details_by_id(session, api_key, m_type, m_id)
                if meta:
                    if meta.year and meta.year.isdigit() and int(meta.year) < current_year:
                        continue
                    lower_genres = {g.lower().strip() for g in meta.genres}
                    if lower_genres.intersection({"soap", "talk", "news", "reality"}):
                        continue
                    detailed_releases.append(meta)

            detailed_releases.sort(key=lambda r: r.popularity, reverse=True)
            return detailed_releases
    except Exception as exc:
        logger.warning("Error fetching daily releases from TMDB: %s", exc)
        return []


async def search_tmdb(api_key: str, query: str) -> list[MediaMeta]:
    """Search TMDB for movies and TV series by keyword."""
    results: list[MediaMeta] = []
    try:
        async with create_http_session() as session:
            params = {"api_key": api_key, "query": query, "include_adult": "false"}
            async with session.get(f"{_BASE_URL}/search/multi", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("results", [])[:10]:
                        m_type = item.get("media_type")
                        if m_type not in ("movie", "tv"):
                            continue
                        title = (item.get("name") if m_type == "tv" else item.get("title")) or "Unknown"
                        rel_date = (item.get("first_air_date") if m_type == "tv" else item.get("release_date")) or ""
                        year = rel_date[:4] if rel_date else None
                        poster_path = item.get("poster_path")
                        poster_url = f"{_POSTER_BASE_URL}{poster_path}" if poster_path else None
                        results.append(
                            MediaMeta(
                                id=item.get("id"),
                                title=title,
                                year=year,
                                overview=item.get("overview") or "",
                                poster_url=poster_url,
                                media_type=m_type,
                                popularity=float(item.get("popularity", 0.0)),
                                release_date=rel_date,
                            )
                        )
    except Exception as exc:
        logger.warning("Error searching TMDB: %s", exc)
    return results

