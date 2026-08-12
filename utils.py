# Credit @LazyDeveloper.
# Please Don't remove credit.
# Born to make history @LazyDeveloper !
# Thank you LazyDeveloper for helping us in this Journey
# 🥰  Thank you for giving me credit @LazyDeveloperr  🥰
# for any error please contact me -> telegram@LazyDeveloperr or insta @LazyDeveloperr 
# rip paid developers 🤣 - >> No need to buy paid source code while @LazyDeveloperr is here 😍😍
import logging
from pyrogram.errors import InputUserDeactivated, UserNotParticipant, FloodWait, UserIsBlocked, PeerIdInvalid
from info import *
import asyncio
import time as _time
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram import enums
from typing import Union
import re
import os
from datetime import datetime
from typing import List
from database.users_chats_db import db
from bs4 import BeautifulSoup
import requests
import aiohttp
from shortzy import Shortzy

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BTN_URL_REGEX = re.compile(
    r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))"
)

BANNED = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

# temp db for banned 
class temp(object):
    BANNED_USERS = []
    BANNED_CHATS = []
    LAZY_VERIFIED_CHATS = []
    ME = None
    CURRENT=int(os.environ.get("SKIP", 2))
    CANCEL = False
    MELCOW = {}
    GETALL = {}
    SHORT = {}
    IMDB_CAP = {}
    U_NAME = None
    B_NAME = None
    SETTINGS = {}
    CHANNELS = set()  # live-updated library channel ids, no restart needed to add a new one

async def is_subscribed(bot, query):
    if await db.find_join_req(query.from_user.id):
        return True
    try:
        user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
    except UserNotParticipant:
        pass
    except Exception as e:
        logger.exception(e)
    else:
        if user.status != enums.ChatMemberStatus.BANNED:
            return True

    return False

_TMDB_BASE = "https://api.themoviedb.org/3"
_TMDB_IMG = "https://image.tmdb.org/t/p/w780"
_POSTER_CACHE: dict = {}  # key -> (expires_monotonic, value)


class _TmdbCandidate:
    """Lightweight stand-in for the old IMDbPY search-result object.

    Supports both attribute access (`.movieID`, used in callback_data) and
    dict-style `.get('title'|'year')`, matching call sites in misc.py / pm_filter.py.
    """
    __slots__ = ("movieID", "_title", "_year")

    def __init__(self, movieID, title, year):
        self.movieID = movieID
        self._title = title
        self._year = year

    def get(self, key, default=None):
        if key == "title":
            return self._title
        if key == "year":
            return self._year
        return default


def _cache_get(key):
    entry = _POSTER_CACHE.get(key)
    if not entry:
        return None
    expires, value = entry
    if _time.monotonic() > expires:
        _POSTER_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key, value):
    _POSTER_CACHE[key] = (_time.monotonic() + POSTER_CACHE_SECONDS, value)


async def _tmdb_request(session, path, params):
    params = dict(params)
    params["api_key"] = TMDB_API_KEY
    try:
        async with session.get(f"{_TMDB_BASE}{path}", params=params) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"TMDB request failed for {path}: {e}")
        return None


async def _tmdb_search_multi(session, title, year=None):
    data = await _tmdb_request(session, "/search/multi", {"query": title, "include_adult": "false"})
    if not data:
        return []
    results = [r for r in (data.get("results") or []) if r.get("media_type") in ("movie", "tv")]
    if year:
        def _matches_year(r):
            date = r.get("release_date") or r.get("first_air_date") or ""
            return date.startswith(str(year))
        filtered = [r for r in results if _matches_year(r)]
        if filtered:
            results = filtered
    return results


def _crew_by_job(credits, *jobs):
    crew = (credits or {}).get("crew") or []
    names = [c.get("name") for c in crew if c.get("job") in jobs and c.get("name")]
    return list(dict.fromkeys(names))


def _build_poster_result(media_type, data):
    title = data.get("title") or data.get("name") or "N/A"
    date = data.get("release_date") or data.get("first_air_date") or ""
    year = date[:4] if date else (data.get("year") or "N/A")
    credits = data.get("credits") or {}
    cast = [c.get("name") for c in (credits.get("cast") or [])[:10] if c.get("name")]
    director = _crew_by_job(credits, "Director")
    writer = _crew_by_job(credits, "Writer", "Screenplay", "Story")
    producer = _crew_by_job(credits, "Producer", "Executive Producer")
    composer = _crew_by_job(credits, "Original Music Composer")
    cinematographer = _crew_by_job(credits, "Director of Photography")
    music_team = _crew_by_job(credits, "Music", "Music Editor", "Music Supervisor") or composer
    companies = [c.get("name") for c in (data.get("production_companies") or []) if c.get("name")]
    genres = [g.get("name") for g in (data.get("genres") or []) if g.get("name")]
    countries = [c.get("name") for c in (data.get("production_countries") or []) if c.get("name")] \
        or list(data.get("origin_country") or [])
    languages = [l.get("english_name") or l.get("name") for l in (data.get("spoken_languages") or []) if l]
    aka_data = data.get("alternative_titles") or {}
    aka_list = aka_data.get("titles") or aka_data.get("results") or []
    aka = [a.get("title") for a in aka_list if a.get("title")]
    imdb_id = (data.get("external_ids") or {}).get("imdb_id") or data.get("imdb_id")

    certificates = []
    if media_type == "movie":
        for rel in (data.get("release_dates") or {}).get("results") or []:
            if rel.get("iso_3166_1") == "US":
                for r in rel.get("release_dates") or []:
                    if r.get("certification"):
                        certificates.append(r["certification"])
    else:
        for rating in (data.get("content_ratings") or {}).get("results") or []:
            if rating.get("iso_3166_1") == "US" and rating.get("rating"):
                certificates.append(rating["rating"])

    box_office = f"${data['revenue']:,}" if media_type == "movie" and data.get("revenue") else None
    runtime = data.get("runtime")
    if not runtime:
        runtimes = data.get("episode_run_time") or []
        runtime = runtimes[0] if runtimes else None
    poster_path = data.get("poster_path")
    url = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else f"https://www.themoviedb.org/{media_type}/{data.get('id')}"

    return {
        'title': title,
        'votes': data.get('vote_count') or "N/A",
        'aka': list_to_str(aka),
        'seasons': data.get('number_of_seasons') or "N/A",
        'box_office': box_office or "N/A",
        'localized_title': data.get('original_title') or data.get('original_name') or title,
        'kind': 'movie' if media_type == 'movie' else 'tv series',
        'imdb_id': imdb_id or "N/A",
        'cast': list_to_str(cast),
        'runtime': f"{runtime} min" if runtime else "N/A",
        'countries': list_to_str(countries),
        'certificates': list_to_str(list(dict.fromkeys(certificates))),
        'languages': list_to_str(languages),
        'director': list_to_str(director),
        'writer': list_to_str(writer),
        'producer': list_to_str(producer),
        'composer': list_to_str(composer),
        'cinematographer': list_to_str(cinematographer),
        'music_team': list_to_str(music_team),
        'distributors': list_to_str(companies),
        'release_date': date or "N/A",
        'year': year,
        'genres': list_to_str(genres),
        'poster': f"{_TMDB_IMG}{poster_path}" if poster_path else None,
        'plot': ((data.get('overview') or "")[:800] or "N/A"),
        'rating': str(data.get('vote_average') or "N/A"),
        'url': url,
        'movieID': f"{media_type}:{data.get('id')}",
    }


async def _tmdb_details(session, media_type, media_id):
    append = "credits,external_ids,alternative_titles"
    if media_type == "tv":
        append += ",content_ratings"
    else:
        append += ",release_dates"
    data = await _tmdb_request(session, f"/{media_type}/{media_id}", {"append_to_response": append})
    if not data:
        return None
    return _build_poster_result(media_type, data)


async def get_poster(query, bulk=False, id=False, file=None):
    """Movie/TV metadata lookup backed by TMDB. Same signature and return
    dict contract as the old IMDbPY-based version, so callers need no changes."""
    if not TMDB_API_KEY:
        return None

    if id:
        media_type, sep, media_id = str(query).partition(":")
        if not sep:
            media_type, media_id = "movie", query
        cache_key = f"id:{media_type}:{media_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        async with aiohttp.ClientSession() as session:
            result = await _tmdb_details(session, media_type, media_id)
        _cache_set(cache_key, result)
        return result

    query = (query.strip()).lower()
    title = query
    year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
    if year:
        year = list_to_str(year[:1])
        title = (query.replace(year, "")).strip()
    elif file is not None:
        year_in_file = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
        year = list_to_str(year_in_file[:1]) if year_in_file else None
    else:
        year = None

    cache_key = f"search:{title}:{year}:{bulk}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    async with aiohttp.ClientSession() as session:
        results = await _tmdb_search_multi(session, title, year)
        if not results:
            _cache_set(cache_key, None)
            return None

        if bulk:
            candidates = [
                _TmdbCandidate(
                    f"{r.get('media_type')}:{r.get('id')}",
                    r.get('title') or r.get('name') or "N/A",
                    (r.get('release_date') or r.get('first_air_date') or "")[:4] or "N/A",
                )
                for r in results[:10]
            ]
            _cache_set(cache_key, candidates)
            return candidates

        best = results[0]
        result = await _tmdb_details(session, best.get('media_type'), best.get('id'))
        _cache_set(cache_key, result)
        return result

async def broadcast_messages(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await broadcast_messages(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id}-Removed from Database, since deleted account.")
        return False, "Deleted"
    except UserIsBlocked:
        logging.info(f"{user_id} -Blocked the bot.")
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - PeerIdInvalid")
        return False, "Error"
    except Exception as e:
        return False, "Error"

def _search_gagala_sync(text):
    usr_agent = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/109.0.5414.120 Safari/537.36'
        }
    text = text.replace(" ", '+')
    url = f'https://www.google.com/search?q={text}'
    response = requests.get(url, headers=usr_agent)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    titles = soup.find_all( 'h3' )
    return [title.getText() for title in titles]


async def search_gagala(text):
    # blocking requests call kept off the event loop, so one slow google
    # scrape can't stall every other user's search
    return await asyncio.to_thread(_search_gagala_sync, text)


USERNAME_PATTERN = re.compile(r"@[A-Za-z0-9_]{5,32}")
_PROMO_LINE_PATTERN = re.compile(r"^[^\n]*(sent via|joined via)[^\n]*\n?", re.IGNORECASE | re.MULTILINE)
_BLANK_LINES_PATTERN = re.compile(r"\n{3,}")


def sanitize_caption(caption):
    """Replace any @username mention in a caption (from source channels)
    with our own handle, and drop common promo-bot lines."""
    if not caption:
        return caption
    cleaned = _PROMO_LINE_PATTERN.sub("", caption)
    cleaned = _BLANK_LINES_PATTERN.sub("\n\n", cleaned).strip()
    replacement = f"@{MAIN_CHANNEL_USRNM}"
    if USERNAME_PATTERN.search(cleaned):
        return USERNAME_PATTERN.sub(replacement, cleaned)
    return cleaned

async def get_settings(group_id):
    settings = temp.SETTINGS.get(group_id)
    if not settings:
        settings = await db.get_settings(group_id)
        temp.SETTINGS[group_id] = settings
    return settings
    
async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current[key] = value
    temp.SETTINGS[group_id] = current
    await db.update_settings(group_id, current)
    
def get_size(size):
    """Get size in readable format"""
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])

def split_list(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]  

def get_file_id(msg: Message):
    if msg.media:
        for message_type in (
            "photo",
            "animation",
            "audio",
            "document",
            "video",
            "video_note",
            "voice",
            "sticker"
        ):
            obj = getattr(msg, message_type)
            if obj:
                setattr(obj, "message_type", message_type)
                return obj

def extract_user(message: Message) -> Union[int, str]:
    """extracts the user from a message"""
    # https://github.com/SpEcHiDe/PyroGramBot/blob/f30e2cca12002121bad1982f68cd0ff9814ce027/pyrobot/helper_functions/extract_user.py#L7
    user_id = None
    user_first_name = None
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_first_name = message.reply_to_message.from_user.first_name

    elif len(message.command) > 1:
        if (
            len(message.entities) > 1 and
            message.entities[1].type == enums.MessageEntityType.TEXT_MENTION
        ):
           
            required_entity = message.entities[1]
            user_id = required_entity.user.id
            user_first_name = required_entity.user.first_name
        else:
            user_id = message.command[1]
            # don't want to make a request -_-
            user_first_name = user_id
        try:
            user_id = int(user_id)
        except ValueError:
            pass
    else:
        user_id = message.from_user.id
        user_first_name = message.from_user.first_name
    return (user_id, user_first_name)

def list_to_str(k):
    if not k:
        return "N/A"
    elif len(k) == 1:
        return str(k[0])
    elif MAX_LIST_ELM:
        k = k[:int(MAX_LIST_ELM)]
        return ' '.join(f'{elem}, ' for elem in k)
    else:
        return ' '.join(f'{elem}, ' for elem in k)

def last_online(from_user):
    time = ""
    if from_user.is_bot:
        time += "🤖 Bot :("
    elif from_user.status == enums.UserStatus.RECENTLY:
        time += "Recently"
    elif from_user.status == enums.UserStatus.LAST_WEEK:
        time += "Within the last week"
    elif from_user.status == enums.UserStatus.LAST_MONTH:
        time += "Within the last month"
    elif from_user.status == enums.UserStatus.LONG_AGO:
        time += "A long time ago :("
    elif from_user.status == enums.UserStatus.ONLINE:
        time += "Currently Online"
    elif from_user.status == enums.UserStatus.OFFLINE:
        time += from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    return time


def split_quotes(text: str) -> List:
    if not any(text.startswith(char) for char in START_CHAR):
        return text.split(None, 1)
    counter = 1  # ignore first char -> is some kind of quote
    while counter < len(text):
        if text[counter] == "\\":
            counter += 1
        elif text[counter] == text[0] or (text[0] == SMART_OPEN and text[counter] == SMART_CLOSE):
            break
        counter += 1
    else:
        return text.split(None, 1)

    # 1 to avoid starting quote, and counter is exclusive so avoids ending
    key = remove_escapes(text[1:counter].strip())
    # index will be in range, or `else` would have been executed and returned
    rest = text[counter + 1:].strip()
    if not key:
        key = text[0] + text[0]
    return list(filter(None, [key, rest]))

def parser(text, keyword):
    if "buttonalert" in text:
        text = (text.replace("\n", "\\n").replace("\t", "\\t"))
    buttons = []
    note_data = ""
    prev = 0
    i = 0
    alerts = []
    for match in BTN_URL_REGEX.finditer(text):
        # Check if btnurl is escaped
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1

        # if even, not escaped -> create button
        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            if match.group(3) == "buttonalert":
                # create a thruple with button label, url, and newline status
                if bool(match.group(5)) and buttons:
                    buttons[-1].append(InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"alertmessage:{i}:{keyword}"
                    ))
                else:
                    buttons.append([InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"alertmessage:{i}:{keyword}"
                    )])
                i += 1
                alerts.append(match.group(4))
            elif bool(match.group(5)) and buttons:
                buttons[-1].append(InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                ))
            else:
                buttons.append([InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                )])

        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    else:
        note_data += text[prev:]

    try:
        return note_data, buttons, alerts
    except:
        return note_data, buttons, None

def remove_escapes(text: str) -> str:
    res = ""
    is_escaped = False
    for counter in range(len(text)):
        if is_escaped:
            res += text[counter]
            is_escaped = False
        elif text[counter] == "\\":
            is_escaped = True
        else:
            res += text[counter]
    return res

async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""

        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1

        unit = ts[index:].lstrip()

        if value:
            value = int(value)

        return value, unit

    value, unit = extract_value_and_unit(time_string)

    if unit == 's':
        return value
    elif unit == 'min':
        return value * 60
    elif unit == 'hour':
        return value * 3600
    elif unit == 'day':
        return value * 86400
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0

def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'
    
async def get_shortlink(chat_id, link):
    settings = await get_settings(chat_id) #fetching settings for group
    if 'shortlink' in settings.keys():
        URL = settings['shortlink']
        API = settings['shortlink_api']
    else:
        URL = URL_SHORTENR_WEBSITE
        API = URL_SHORTNER_WEBSITE_API
    if URL.startswith("shorturllink") or URL.startswith("terabox.in") or URL.startswith("urlshorten.in"):
        URL = URL_SHORTENR_WEBSITE
        API = URL_SHORTNER_WEBSITE_API
    if URL == "api.shareus.io":
        url = f'https://{URL}/easy_api'
        params = {
            "key": API,
            "link": link,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                    data = await response.text()
                    return data
        except Exception as e:
            logger.error(e)
            return link
    else:
        shortzy = Shortzy(api_key=API, base_site=URL)
        link = await shortzy.convert(link)
        return link
   
def get_readable_time(seconds: int) -> str:
    count = 0
    readable_time = ""
    time_list = []
    time_suffix_list = ["s", "m", "h", " days"]
    while count < 4:
        count += 1
        if count < 3:
            remainder, result = divmod(seconds, 60)
        else:
            remainder, result = divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        readable_time += time_list.pop() + ", "
    time_list.reverse()
    readable_time += ": ".join(time_list)
    return readable_time 

async def get_tutorial(chat_id):
    settings = await get_settings(chat_id) #fetching settings for group
    if 'tutorial' in settings.keys():
        if settings['is_tutorial']:
            TUTORIAL_URL = settings['tutorial']
        else:
            TUTORIAL_URL = TUTORIAL
    else:
        TUTORIAL_URL = TUTORIAL
    return TUTORIAL_URL
  


# Credit @LazyDeveloper.
# Please Don't remove credit.
# Born to make history @LazyDeveloper !
# Thank you LazyDeveloper for helping us in this Journey
# 🥰  Thank you for giving me credit @LazyDeveloperr  🥰
# for any error please contact me -> telegram@LazyDeveloperr or insta @LazyDeveloperr 
# rip paid developers 🤣 - >> No need to buy paid source code while @LazyDeveloperr is here 😍😍
