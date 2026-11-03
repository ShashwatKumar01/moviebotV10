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

sys.modules["pyrogram"] = mock_pyrogram
sys.modules["pyrogram.types"] = mock_types
sys.modules["pyrogram.errors"] = MagicMock()
sys.modules["pyrogram.filters"] = MagicMock()

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

    def test_autofilter_keyboard_deep_link(self):
        files = [
            {"_id": "file_123", "file_name": "Deadpool.2024.720p.mkv", "file_size": 1200000000},
            {"_id": "file_456", "file_name": "Deadpool.2024.1080p.mkv", "file_size": 2500000000},
        ]
        kb = build_autofilter_keyboard(files, file_bot_username="iMoviesMagicBot")
        
        first_btn = kb.inline_keyboard[0][0]
        self.assertIn("iMoviesMagicBot", first_btn.url)
        self.assertEqual(first_btn.url, "https://t.me/iMoviesMagicBot?start=file_file_123")
        self.assertIn("720P", first_btn.text)

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

        # Test Screenshot 2 Keyboard delegates to exact same 4-button layout
        kb2 = build_screenshot_2_keyboard(
            bot_username="iMoviesAutoFilterBot",
            query="Kappi Squad",
            updates_channel_link="https://t.me/imoviesmagic",
            group_1_url="https://t.me/moviegroup",
            season_number=1,
            trailer_url="",
        )
        self.assertEqual(kb2.inline_keyboard[0][0].url, "https://t.me/iMoviesAutoFilterBot?start=getfile-Kappi-Squad-S01")
        self.assertEqual(len(kb2.inline_keyboard[1]), 2)
        self.assertEqual(kb2.inline_keyboard[1][0].text, "📢 Join Channel")
        self.assertEqual(kb2.inline_keyboard[1][1].text, "👥 Join Group")

    def test_ttl_cache(self):
        cache = TTLCache(default_ttl_seconds=10)
        cache.set("foo", "bar")
        self.assertEqual(cache.get("foo"), "bar")
        self.assertIsNone(cache.get("nonexistent"))


if __name__ == "__main__":
    unittest.main()
