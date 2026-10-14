import unittest
from unittest.mock import MagicMock
import cookie_auth


class ProbeProvenance(unittest.TestCase):
    def test_other_article_or_ad_body_cannot_prove_requested_post_access(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.goto.return_value.status = 200
        frame = MagicMock()
        frame.url = 'https://ca-fe.naver.com/ca-fe/cafes/100/articles/999'
        frame.query_selector_all.return_value = [MagicMock()]
        frame.query_selector_all.return_value[0].inner_text.return_value = '다른 게시글의 실제 본문입니다' * 10
        frame.inner_text.return_value = '메뉴'
        page.frames = [frame]
        self.assertEqual(cookie_auth.page_status(page,page.url),'unknown')

    def test_requested_article_body_can_prove_access(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.goto.return_value.status = 200
        frame = MagicMock()
        frame.url = 'https://ca-fe.naver.com/ca-fe/cafes/100/articles/101'
        frame.query_selector_all.return_value = [MagicMock()]
        frame.query_selector_all.return_value[0].inner_text.return_value = '요청한 게시글의 실제 본문입니다' * 10
        page.frames = [frame]
        self.assertEqual(cookie_auth.page_status(page,page.url),'body')
