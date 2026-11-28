import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import broker_voice_collect as collector


NOW = datetime(2026, 11, 28, 6, 15, tzinfo=timezone(timedelta(hours=9)))
MOBILE = "https://m.blog.naver.com/publictest/123456789"
DESKTOP = "https://blog.naver.com/publictest/123456789"
CAFE = "https://cafe.naver.com/f-e/cafes/15112066/articles/987654"


class Browser:
    def __init__(self):
        self.contexts = []

    def new_context(self, storage_state=None):
        context = SimpleNamespace(new_page=lambda: Page(bool(storage_state)))
        self.contexts.append(context)
        return context


class Page:
    def __init__(self, logged_in):
        self.logged_in = logged_in
        self.url = ""

    def get_by_text(self, *args):
        return SimpleNamespace(count=lambda: 1, is_visible=lambda: True)


class Sources:
    CAFES = [
        {"id": "ustock", "num_id": "15112066", "login_required": False},
        {"id": "stocktraining", "num_id": "29798500", "login_required": True},
    ]

    def __init__(self, mobile_only=False, inaccessible=False):
        self.mobile_only = mobile_only
        self.inaccessible = inaccessible
        self.detail_urls = []
        self.board_queries = set()

    def search_naver_blog(self, page, term):
        if term in ("한국투자증권", "뱅키스", "삼성증권"):
            return [{"url": MOBILE}]
        return []

    def get_post_list(self, page, board, num_id, terms, **kwargs):
        self.board_queries.add(board)
        if board == "ustock" and "삼성증권" in terms:
            return [{"url": CAFE, "title": "삼성증권 앱 후기"}]
        return []

    def get_post_detail(self, page, url, board, details):
        assert not page.logged_in, "public evidence must be read signed out"
        page.url = url
        self.detail_urls.append(url)
        details["date_str"] = "2026.11.27. 22:03"
        return "", "삼성증권 앱에서 공모주 신청함"

    def get_blog_detail(self, page, url):
        assert not page.logged_in, "public evidence must be read signed out"
        page.url = url
        self.detail_urls.append(url)
        if self.inaccessible:
            page.url = "https://nid.naver.com/login/"
        ok = not self.mobile_only or url == MOBILE
        return {"title": "삼성증권에서 뱅키스로 옮긴 이유" if ok else "",
                "body_full": "삼성증권에서 한국투자증권으로 계좌를 옮겼습니다." if ok else "",
                "date_str": "2026. 11. 27. 22:30"}


class CollectorTests(unittest.TestCase):
    def collect(self, source, state=None):
        return collector.candidates(Browser(), source, {} if state is None else state, NOW, {"cookies": []})

    def test_canonical_key_and_dates(self):
        self.assertEqual(collector.identity(MOBILE), collector.identity(DESKTOP+"?from=search"))
        self.assertIsNone(collector.identity("https://blog.naver.com.example.org/publictest/123456789"))
        self.assertEqual(collector.published("8시간 전",NOW), date(2026,11,27))
        self.assertIsNone(collector.published("2026.11.31",NOW))

    def test_desktop_and_mobile_use_same_public_mobile_detail(self):
        posts, _ = self.collect(Sources(mobile_only=True))
        self.assertIn("blog:publictest:123456789",posts)

    def test_company_counts_after_dedup_and_second_slot(self):
        sources, state = Sources(), {}
        records, counts = self.collect(sources,state)
        self.assertEqual(len(counts),16)
        self.assertEqual(len(sources.detail_urls),2)
        self.assertEqual(set(records["blog:publictest:123456789"]["companies"]),{"KIS","SAM"})
        for key in ("KIS/blog","SAM/blog","SAM/cafe"):
            self.assertEqual(counts[key]["read"],1,key)
            self.assertEqual(counts[key]["listed"],1,key)
        sources.detail_urls.clear()
        next_records, counts = self.collect(sources,state)
        self.assertFalse(next_records)
        self.assertFalse(sources.detail_urls)
        for key in ("KIS/blog","SAM/blog","SAM/cafe"):
            self.assertEqual(counts[key]["cached"],1,key)

    def test_closed_or_redirected_blog_not_forwarded(self):
        records, counts = self.collect(Sources(inaccessible=True))
        self.assertEqual(len(records),1)
        self.assertEqual(counts["KIS/blog"]["failed"],1)
        self.assertEqual(counts["SAM/blog"]["failed"],1)

    def test_public_boards_only(self):
        sources = Sources()
        self.collect(sources)
        self.assertNotIn("stocktraining",sources.board_queries)


if __name__ == "__main__":
    unittest.main()
