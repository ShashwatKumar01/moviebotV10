import os
import re
import sys
import unittest
from unittest.mock import MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Mock pyrogram types to avoid sandbox network/socket hangs
mock_pyrogram = MagicMock()
mock_types = MagicMock()

class MockButton:
    def __init__(self, text, url=None, callback_data=None):
        self.text = text
        self.url = url
        self.callback_data = callback_data

class MockMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard

mock_types.InlineKeyboardButton = MockButton
mock_types.InlineKeyboardMarkup = MockMarkup

class MockRPCError(Exception):
    pass

class MockUserNotParticipant(MockRPCError):
    pass

class MockChatAdminRequired(MockRPCError):
    pass

mock_errors = MagicMock()
mock_errors.RPCError = MockRPCError
mock_errors.UserNotParticipant = MockUserNotParticipant
mock_errors.ChatAdminRequired = MockChatAdminRequired

sys.modules["pyrogram"] = mock_pyrogram
sys.modules["pyrogram.types"] = mock_types
sys.modules["pyrogram.errors"] = mock_errors
sys.modules["pyrogram.filters"] = MagicMock()

mock_enums = MagicMock()
mock_enums.ChatMemberStatus = MagicMock()
mock_enums.ChatMemberStatus.LEFT = "left"
mock_enums.ChatMemberStatus.BANNED = "banned"
mock_enums.ChatType = MagicMock()
mock_enums.ChatType.PRIVATE = "private"
mock_enums.ChatType.GROUP = "group"
mock_enums.ChatType.SUPERGROUP = "supergroup"
sys.modules["pyrogram.enums"] = mock_enums

# Mock motor to prevent DNS resolution in sandbox
sys.modules["motor"] = MagicMock()
sys.modules["motor.motor_asyncio"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autofilter_bot.core.release_parse import parse_release
from autofilter_bot.core.tmdb import (
    MediaMeta,
    format_screenshot_1_caption,
    format_screenshot_2_caption,
    format_autofilter_caption,
    format_media_caption,
    is_released_within_days,
)
from autofilter_bot.bot.keyboards import (
    build_autofilter_keyboard,
    build_admin_alert_keyboard,
    build_screenshot_1_keyboard,
    build_screenshot_2_keyboard,
    build_title_selection_keyboard,
    release_preview_keyboard,
    extract_button_label,
)
from autofilter_bot.bot.broadcast import parse_inline_buttons, render_progress_bar
from autofilter_bot.core.cache import TTLCache


class TestAutoFilterCore(unittest.TestCase):
    def test_release_parse_movie(self):
        rel = parse_release("The.Holiday.Spark.2026.720p.WEB-DL.Hindi.English.mkv")
        self.assertEqual(rel.title, "The Holiday Spark")
        self.assertEqual(rel.year, "2026")
        self.assertFalse(rel.is_tv)
        self.assertEqual(rel.quality, "720P")

    def test_release_parse_tv_series(self):
        rel = parse_release("Chaar Yaar S01 E03 2026 1080p WEB-DL.mkv")
        self.assertEqual(rel.title, "Chaar Yaar")
        self.assertEqual(rel.year, "2026")
        self.assertTrue(rel.is_tv)
        self.assertEqual(rel.season, 1)
        self.assertEqual(rel.episode, 3)

    def test_year_sorting_flow(self):
        """Verify that when multiple matching titles exist (e.g. Love Always 2026 and 2025),
        relevance + Year descending places Love Always (2026) first, then Love Always (2025),
        then other matching titles with their actual movie titles.
        """
        raw_items = [
            {"title": "Mothevari Love Story", "year": "2025", "files": [1]},
            {"title": "Love Always", "year": "2025", "files": [2]},
            {"title": "Love Always", "year": "2026", "files": [3]},
            {"title": "Anyway Im Falling in Love", "year": "2024", "files": [4]},
        ]
        clean_q = "love always"

        def get_relevance(title: str) -> int:
            t = title.lower().strip()
            if t == clean_q:
                return 100
            if t.startswith(clean_q):
                return 80
            if clean_q in t:
                return 60
            return 40

        def sort_key(item):
            rel = get_relevance(item["title"])
            raw_y = item.get("year")
            y = int(raw_y) if raw_y and str(raw_y).isdigit() else 0
            return (rel, y)

        sorted_items = sorted(raw_items, key=sort_key, reverse=True)
        self.assertEqual(sorted_items[0]["title"], "Love Always")
        self.assertEqual(sorted_items[0]["year"], "2026")
        self.assertEqual(sorted_items[1]["title"], "Love Always")
        self.assertEqual(sorted_items[1]["year"], "2025")
        self.assertEqual(sorted_items[2]["title"], "Mothevari Love Story")
        self.assertEqual(sorted_items[3]["title"], "Anyway Im Falling in Love")

    def test_title_selection_keyboard(self):
        grouped = [
            {"title": "Love Always", "year": "2026", "is_tv": False, "index": 0, "file_count": 3},
            {"title": "Love Always", "year": "2025", "is_tv": False, "index": 1, "file_count": 1},
        ]
        kb = build_title_selection_keyboard(grouped, query_key="q123", page=1)
        # Should have 2 rows, one for each title
        self.assertEqual(len(kb.inline_keyboard), 2)
        btn1 = kb.inline_keyboard[0][0]
        self.assertIn("Love Always (2026)", btn1.text)
        self.assertEqual(btn1.callback_data, "sel_t:q123:0")

    def test_release_preview_keyboard_role_permission(self):
        # Regular user view
        user_kb = release_preview_keyboard("movie", 12345, back_page=1, is_admin=False)
        user_buttons = [btn.text for row in user_kb.inline_keyboard for btn in row]
        self.assertNotIn("🚀 Post to Updates Channel", user_buttons)
        self.assertNotIn("📢 Post to Public Channel", user_buttons)
        self.assertNotIn("📣 Broadcast to Users", user_buttons)
        self.assertIn("🔙 Back to Releases", user_buttons)

        # Admin view
        admin_kb = release_preview_keyboard("movie", 12345, back_page=1, is_admin=True)
        admin_buttons = [btn.text for row in admin_kb.inline_keyboard for btn in row]
        self.assertIn("🚀 Post to Updates Channel", admin_buttons)
        self.assertIn("📢 Post to Public Channel", admin_buttons)
        self.assertIn("📣 Broadcast to Users", admin_buttons)
        self.assertIn("🔙 Back to Releases", admin_buttons)

    def test_parse_inline_buttons(self):
        text_with_buttons = (
            "Check out this amazing movie!\n\n"
            "[🎬 Watch Now | https://t.me/imoviesmagic_bot?start=123] [📢 Channel | https://t.me/imoviesmagic]\n"
            "Join Discussion - https://t.me/imoviesgroup"
        )
        clean_text, rows = parse_inline_buttons(text_with_buttons)
        self.assertEqual(clean_text, "Check out this amazing movie!")
        self.assertEqual(len(rows), 2)
        # Row 1 has 2 buttons side by side
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "🎬 Watch Now")
        self.assertEqual(rows[0][0].url, "https://t.me/imoviesmagic_bot?start=123")
        self.assertEqual(rows[0][1].text, "📢 Channel")
        # Row 2 has 1 button
        self.assertEqual(len(rows[1]), 1)
        self.assertEqual(rows[1][0].text, "Join Discussion")
        self.assertEqual(rows[1][0].url, "https://t.me/imoviesgroup")

    def test_render_progress_bar(self):
        bar_50 = render_progress_bar(50, 100, length=10)
        self.assertIn("50.0%", bar_50)
        self.assertIn("█████░░░░░", bar_50)

    def test_screenshot_1_caption(self):
        meta = MediaMeta(
            title="The Holiday Spark",
            year="2026",
            overview="Romantic story",
            poster_url=None,
            media_type="movie",
            genres=["Romance"],
            languages=["English"],
            duration="1h 26mins",
        )
        caption = format_screenshot_1_caption(meta)
        self.assertIn("📺 TITLE : The Holiday Spark (2026)", caption)
        self.assertIn("🎬 TYPE : MOVIE", caption)
        self.assertIn("🍿 GENRE: #Romance 💕", caption)
        self.assertIn("🎧 LANGUAGE: #English", caption)
        self.assertIn("⏳ DURATION : 1h 26mins", caption)

    def test_screenshot_2_caption(self):
        meta = MediaMeta(
            title="Chaar Yaar",
            year="2026",
            overview="Four middle-class boys...",
            poster_url=None,
            media_type="tv",
            genres=["Drama"],
            languages=["Hindi"],
            duration="33 - 44mins",
            episode_count=8,
            season_number=1,
            release_date="2026-10-23",
            cast=["Prayrak Mehta", "Saqib Ayub"],
        )
        caption = format_screenshot_2_caption(meta)
        self.assertIn("⚡️ TITIL : Chaar Yaar S01 (𝟸𝟶𝟸𝟼)", caption)
        self.assertIn("🎬 TYPE : TV SERIES ( 8 Epsosides )", caption)
        self.assertIn("🍿 GENRE: #Drama 🎭", caption)
        self.assertIn("🎧 LANGUAGE : #Hindi", caption)
        self.assertIn("⏳ DURATION : 33 - 44mins", caption)
        self.assertIn("📅 RELEASE DATE : 23 October 2026", caption)
        self.assertIn("Cast: Prayrak Mehta, Saqib Ayub", caption)
        self.assertIn("ℹ️ Info via TMDB • No copyright infringement intended.", caption)

    def test_autofilter_caption_with_release_date_and_admin(self):
        meta = MediaMeta(
            title="Resident Evil",
            year="2026",
            overview="Deadly viral outbreak in Raccoon City...",
            poster_url=None,
            rating=7.3,
            genres=["Horror", "Science Fiction", "Adventure"],
            duration="1h 35mins",
            release_date="2026-10-21",
            cast=["Milla Jovovich", "Michelle Rodriguez"],
            languages=["English", "Hindi"],
        )

        # 1. Regular user caption
        user_caption = format_autofilter_caption(meta, file_count=9, is_admin=False)
        self.assertIn("🎬 **Resident Evil (2026)**", user_caption)
        self.assertIn("🌟 Rating: 7.3/10", user_caption)
        self.assertIn("🎭 **Genres:** Horror, Science Fiction, Adventure", user_caption)
        self.assertIn("📅 **Release Date:** 21 October 2026", user_caption)
        self.assertIn("⏳ **Duration:** 1h 35mins", user_caption)
        self.assertIn("📂 **Available Files:** 9 file(s) found in library.", user_caption)
        self.assertNotIn("📖 **Plot:**", user_caption)
        self.assertNotIn("👥 **Cast:**", user_caption)

        # 2. Admin caption: includes Plot, Cast, Languages
        admin_caption = format_autofilter_caption(meta, file_count=9, is_admin=True)
        self.assertIn("📅 **Release Date:** 21 October 2026", admin_caption)
        self.assertIn("📖 **Plot:** Deadly viral outbreak in Raccoon City...", admin_caption)
        self.assertIn("👥 **Cast:** Milla Jovovich, Michelle Rodriguez", admin_caption)

        # 3. Admin keyboard has posting buttons
        files = [{"_id": "f1", "file_name": "Resident.Evil.1080p.mkv", "file_size": 1000000}]
        user_kb = build_autofilter_keyboard(files, "iMoviesMagicBot", query_key="q1", is_admin=False)
        user_btn_texts = [b.text for r in user_kb.inline_keyboard for b in r]
        self.assertNotIn("🚀 Post to Updates Channel", user_btn_texts)
        self.assertNotIn("📢 Post to Public Channel", user_btn_texts)

        admin_kb = build_autofilter_keyboard(files, "iMoviesMagicBot", query_key="q1", is_admin=True)
        admin_btn_texts = [b.text for r in admin_kb.inline_keyboard for b in r]
        self.assertIn("🚀 Post to Updates Channel", admin_btn_texts)
        self.assertIn("📢 Post to Public Channel", admin_btn_texts)

    def test_ordered_phrase_regex_search(self):
        query = "Love always"
        words = [re.escape(w) for w in query.split() if w]
        raw_pattern = r"(\b|[\.\+\-_])" + r".*[\s\.\+\-_]".join(words) + r"(\b|[\.\+\-_])"
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)

        # Matching files
        self.assertTrue(bool(regex.search("Love.Always.2026.Hindi.720p.mkv")))
        self.assertTrue(bool(regex.search("Love Always (2025) 1080p.mkv")))
        self.assertTrue(bool(regex.search("[iMovies] Love Always 2026.mkv")))

        # Completely unrelated files must NOT match
        self.assertFalse(bool(regex.search("Lie to Me S01.mkv")))
        self.assertFalse(bool(regex.search("Anne Shirley S01.mkv")))
        self.assertFalse(bool(regex.search("Mothevari Love Story (2025).mkv")))
        self.assertFalse(bool(regex.search("Recipes For Love and Murder.mkv")))
        self.assertFalse(bool(regex.search("Always a Catch (2020).mkv")))
        self.assertFalse(bool(regex.search("Anyway Im Falling in Love With You.mkv")))

    def test_title_similarity_rejection(self):
        import difflib
        def _title_sim(target, candidate):
            t = re.sub(r"[^\w\s]", "", target.lower()).strip()
            c = re.sub(r"[^\w\s]", "", candidate.lower()).strip()
            if t == c:
                return 1.0
            return difflib.SequenceMatcher(None, t, c).ratio()

        target = "Love Always"
        dolly_parton = "CMA Presents I Will Always Love You: A Celebration of Dolly Parton"
        exact_match = "Love Always"

        self.assertLess(_title_sim(target, dolly_parton), 0.40)
        self.assertEqual(_title_sim(target, exact_match), 1.0)

    def test_channel_post_keyboards(self):
        # Test 4-button keyboard: must omit invalid/empty URLs
        kb1 = build_screenshot_1_keyboard(
            bot_username="iMoviesAutoFilterBot",
            query="Akka",
            updates_channel_link="https://t.me/imoviesmagic",
            group_1_url="https://t.me/moviegroup",
            group_2_url="",  # empty string
            season_number=1,
            trailer_url="https://youtube.com/watch?v=123",
        )
        self.assertEqual(len(kb1.inline_keyboard), 3)
        # Row 1 is getfile link to primary bot
        self.assertEqual(kb1.inline_keyboard[0][0].url, "https://t.me/iMoviesAutoFilterBot?start=getfile-Akka-S01")
        # Row 2 has [📢 Join Channel] and [👥 Join Group]
        self.assertEqual(len(kb1.inline_keyboard[1]), 2)
        self.assertEqual(kb1.inline_keyboard[1][0].text, "📢 Join Channel")
        self.assertEqual(kb1.inline_keyboard[1][0].url, "https://t.me/imoviesmagic")
        self.assertEqual(kb1.inline_keyboard[1][1].text, "👥 Join Group")
        self.assertEqual(kb1.inline_keyboard[1][1].url, "https://t.me/moviegroup")
        # Row 3 is trailer
        self.assertEqual(kb1.inline_keyboard[2][0].text, "▶️ Watch Trailer")

        # Test Screenshot 2 Keyboard for Public Channel:
        # ONLY:
        # Row 1: [🔔 Join Updates Channel] -> https://t.me/+cQxPLyBRxm0wNzM9
        # Row 2: [▶️ Watch Trailer] (if trailer available)
        kb2 = build_screenshot_2_keyboard(
            bot_username="iMoviesAutoFilterBot",
            query="Kappi Squad",
            updates_channel_link="https://t.me/+cQxPLyBRxm0wNzM9",
            group_1_url="https://t.me/moviegroup",
            season_number=1,
            trailer_url="https://youtube.com/watch?v=kappi",
        )
        self.assertEqual(len(kb2.inline_keyboard), 2)
        self.assertEqual(kb2.inline_keyboard[0][0].text, "🔔 Join Updates Channel")
        self.assertEqual(kb2.inline_keyboard[0][0].url, "https://t.me/+cQxPLyBRxm0wNzM9")
        self.assertEqual(kb2.inline_keyboard[1][0].text, "▶️ Watch Trailer")

        # Public Channel with no trailer has only 1 button
        kb2_notrailer = build_screenshot_2_keyboard(
            bot_username="iMoviesAutoFilterBot",
            query="Kappi Squad",
            updates_channel_link="https://t.me/+cQxPLyBRxm0wNzM9",
            trailer_url="",
        )
        self.assertEqual(len(kb2_notrailer.inline_keyboard), 1)
        self.assertEqual(kb2_notrailer.inline_keyboard[0][0].text, "🔔 Join Updates Channel")

    def test_season_grouping_and_share_button(self):
        grouped = [
            {"title": "Spartacus", "year": "2010", "season": 1, "is_tv": True, "file_count": 12, "index": 0},
            {"title": "Spartacus", "year": "2012", "season": 2, "is_tv": True, "file_count": 10, "index": 1},
            {"title": "Spartacus", "year": "1960", "season": None, "is_tv": False, "file_count": 1, "index": 2},
        ]
        kb = build_title_selection_keyboard(
            grouped,
            query_key="k1",
            page=1,
            bot_username="testbot",
            query_text="Spartacus",
        )
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertIn("📺 Spartacus S01 (2010) [12]", labels)
        self.assertIn("📺 Spartacus S02 (2012) [10]", labels)
        self.assertIn("🎬 Spartacus (1960)", labels)
        self.assertIn("↗️ Share Search", labels)

    def test_is_released_within_30_days_strict(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        meta_recent = MediaMeta(
            title="Recent Movie",
            year="2026",
            overview="Sample",
            poster_url=None,
            release_date=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
        )
        self.assertTrue(is_released_within_days(meta_recent, days=30))

        meta_old = MediaMeta(
            title="The Dark Heaven",
            year="2026",
            overview="Sample",
            poster_url=None,
            release_date=(now - timedelta(days=47)).strftime("%Y-%m-%d"),
        )
        self.assertFalse(is_released_within_days(meta_old, days=30))

    def test_ttl_cache(self):
        cache = TTLCache(default_ttl_seconds=10)
        cache.set("foo", "bar")
        self.assertEqual(cache.get("foo"), "bar")
        self.assertIsNone(cache.get("nonexistent"))

    def test_stream_player_template_branding_removed(self):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        req_path = os.path.join(root_dir, "template", "req.html")
        with open(req_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Branding removed
        self.assertNotIn("LazyDeveloper", content)
        self.assertNotIn("LazyPrincess", content)
        self.assertNotIn("Logo-Lazydeveloperr", content)

        # Essential player features present
        self.assertIn("iMovies", content)
        self.assertIn("vlc://", content)
        self.assertIn("com.mxtech.videoplayer", content)
        self.assertIn("copyStreamLink", content)
        self.assertIn("Keyboard Shortcuts", content)
        self.assertIn("Plyr", content)

    def test_dl_template_branding_removed(self):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dl_path = os.path.join(root_dir, "template", "dl.html")
        with open(dl_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Branding removed
        self.assertNotIn("LazyDeveloper", content)
        self.assertNotIn("LazyPrincess", content)
        self.assertNotIn("Logo-Lazydeveloperr", content)

        # Essential download features present
        self.assertIn("iMovies", content)
        self.assertIn("copyDownloadUrl", content)
        self.assertIn("Download File", content)

    def test_force_sub_and_join_requests(self):
        from autofilter_bot.bot.force_sub import (
            get_force_sub_targets,
            record_join_request,
            _join_requests,
            _force_sub_chat_ref_for,
            _force_sub_join_url_for,
        )
        from autofilter_bot.config import Config

        cfg = Config(
            api_id=123,
            api_hash="hash",
            bot_token="token",
            file_bot_username="bot",
            database_uri="uri",
            database_name="db",
            collection_name="col",
            admin_ids=frozenset([849188964]),
            log_channel=-100,
            update_channel=-1002025864946,
            public_channel=-1004277825107,
            library_channels=frozenset([-100]),
            tmdb_api_key="key",
            updates_channel_link="link",
            updates_channel_invite_link="https://t.me/+cQxPLyBRxm0wNzM9",
            movie_group_1_url="grp",
            movie_group_2_url="",
            auth_channels=(-1002025864946, "@imoviesmagic"),
            auth_channel_invite_urls=("https://t.me/+cQxPLyBRxm0wNzM9", ""),
        )

        targets = get_force_sub_targets(cfg)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0]["ref"], -1002025864946)
        self.assertEqual(targets[0]["url"], "https://t.me/+cQxPLyBRxm0wNzM9")
        self.assertEqual(targets[1]["ref"], "@imoviesmagic")

        # Test URL and ref parsing helpers
        self.assertEqual(_force_sub_chat_ref_for("-1002025864946"), -1002025864946)
        self.assertEqual(_force_sub_chat_ref_for("@imoviesmagic"), "@imoviesmagic")
        self.assertEqual(_force_sub_chat_ref_for("https://t.me/imoviesmagic"), "@imoviesmagic")
        self.assertIsNone(_force_sub_chat_ref_for("https://t.me/+cQxPLyBRxm0wNzM9"))
        self.assertEqual(_force_sub_join_url_for("@imoviesmagic"), "https://t.me/imoviesmagic")

        # Test join request recording in fast cache
        record_join_request(user_id=999, chat_id=-1002025864946)
        self.assertIn(-1002025864946, _join_requests.get(999, {}))
        self.assertNotIn(-999999999999, _join_requests.get(999, {}))

    def test_secondary_bot_auth_targets(self):
        from utils import get_auth_targets, record_secondary_join_req, has_secondary_join_req

        targets = get_auth_targets()
        self.assertIsInstance(targets, list)

        # Test join request memory cache for secondary bot
        record_secondary_join_req(777, -1002025864946)
        self.assertTrue(has_secondary_join_req(777, -1002025864946))
        self.assertFalse(has_secondary_join_req(777, -999999999999))

    def test_group_files_by_title(self):
        from autofilter_bot.bot.autofilter import group_files_by_title
        files = [
            {"file_name": "Inception.2010.1080p.mkv", "file_size": 2000000},
            {"file_name": "Inception.2010.720p.mkv", "file_size": 1000000},
            {"file_name": "Interstellar.2014.1080p.mkv", "file_size": 3000000},
        ]
        grouped = group_files_by_title(files, "Inception")
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["title"], "Inception")
        self.assertEqual(grouped[0]["file_count"], 2)



if __name__ == "__main__":
    unittest.main()
