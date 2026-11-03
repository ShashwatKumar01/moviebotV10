"""Filename parsing utility for title, year, season, episode, quality, and audio extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedRelease:
    raw_name: str
    title: str
    year: str | None
    is_tv: bool
    season: int | None = None
    episode: int | None = None
    quality: str = ""
    audio: str = ""


_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_SEASON_EPISODE_RE = re.compile(r"\bS(?P<season>\d{1,2})(?:[\.\-_\s]*E(?P<ep>\d{1,2}))?\b", re.IGNORECASE)
_SEASON_WORD_RE = re.compile(r"\bSeason\s*(?P<season>\d{1,2})\b", re.IGNORECASE)
_EPISODE_WORD_RE = re.compile(r"\bE(?:pisode)?\s*(?P<ep>\d{1,3})\b", re.IGNORECASE)

_QUALITY_RE = re.compile(r"\b(2160p|4k|1080p|720p|480p|360p|hdcam|web-?dl|bluray|hdr|dvdrip)\b", re.IGNORECASE)
_AUDIO_RE = re.compile(r"\b(hindi|english|tamil|telugu|malayalam|kannada|dual|multi|clean)\b", re.IGNORECASE)

_NOISE_TOKENS = [
    r"\[.*?\]",
    r"\(.*?\)",
    r"@\w+",
    r"\b(?:x264|x265|hevc|10bit|8bit|aac|ac3|dts|webrip|web-dl|hdrip|bluray|esub|sample)\b",
    r"\b(?:imoviesmagic|cinemagic|moviesverse|filmyzilla|bollyflix|extraquality)\b",
]


def humanize_filename(name: str | None) -> str:
    if not name:
        return ""
    clean = re.sub(r"[\._\+]+", " ", name)
    return re.sub(r"\s+", " ", clean).strip()


def parse_release(name: str | None) -> ParsedRelease:
    raw = name or ""
    clean = raw

    # Extract quality
    q_match = _QUALITY_RE.search(clean)
    quality = q_match.group(1).upper() if q_match else ""

    # Extract audio/languages
    audios = _AUDIO_RE.findall(clean)
    audio_str = " ".join(dict.fromkeys(a.capitalize() for a in audios))

    # Strip extension
    clean = re.sub(r"\.(mkv|mp4|avi|mov|flv|wmv|zip|rar)$", "", clean, flags=re.IGNORECASE)

    # Detect season / episode
    is_tv = False
    season: int | None = None
    episode: int | None = None

    se_match = _SEASON_EPISODE_RE.search(clean)
    if se_match:
        is_tv = True
        season = int(se_match.group("season"))
        if se_match.group("ep"):
            episode = int(se_match.group("ep"))
    else:
        s_word = _SEASON_WORD_RE.search(clean)
        if s_word:
            is_tv = True
            season = int(s_word.group("season"))
        ep_word = _EPISODE_WORD_RE.search(clean)
        if ep_word:
            is_tv = True
            episode = int(ep_word.group("ep"))

    # Extract year
    y_match = _YEAR_RE.search(clean)
    year = y_match.group(1) if y_match else None

    # Clean title
    # Take portion before year, or before S01, or clean tokens
    split_pos = len(clean)
    if se_match and se_match.start() > 2:
        split_pos = min(split_pos, se_match.start())
    if y_match and y_match.start() > 2:
        split_pos = min(split_pos, y_match.start())

    candidate = clean[:split_pos]
    for noise in _NOISE_TOKENS:
        candidate = re.sub(noise, " ", candidate, flags=re.IGNORECASE)

    candidate = re.sub(r"[\._\+\-]+", " ", candidate)
    candidate = re.sub(r"[\(\)\[\]\{\}\:\;]+", " ", candidate)
    title = re.sub(r"\s+", " ", candidate).strip()
    title = re.sub(r"^[\W_]+|[\W_]+$", "", title).strip()

    if not title:
        title = humanize_filename(clean) or "Unknown"

    return ParsedRelease(
        raw_name=raw,
        title=title,
        year=year,
        is_tv=is_tv,
        season=season,
        episode=episode,
        quality=quality,
        audio=audio_str,
    )
