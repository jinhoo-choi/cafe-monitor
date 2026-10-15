import unittest
from unittest.mock import MagicMock
import cookie_auth


class ProbeProvenance(unittest.TestCase):
    def test_article_response_after_dom_ready_is_observed_before_decision(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.frames = []
        page.goto.return_value.status = 200
        response = MagicMock()
        response.url = 'https://article.cafe.naver.com/gw/v4/cafes/100/articles/101'
        response.request.resource_type = 'xhr'
        response.status = 401
        handlers = []
        page.on.side_effect = lambda event, callback: handlers.append(callback)
        page.expect_response.return_value.__exit__.side_effect = lambda *args: handlers[0](response)
        self.assertEqual(cookie_auth.page_status(page,page.url),'auth_required')

    def test_article_api_401_overrides_outer_page_200_and_menu(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.frames = []
        page.goto.return_value.status = 200
        response = MagicMock()
        response.url = 'https://article.cafe.naver.com/gw/v4/cafes/100/articles/101?query=not-logged'
        response.request.resource_type = 'xhr'
        response.status = 401
        page.on.side_effect = lambda event, callback: callback(response)
        self.assertEqual(cookie_auth.page_status(page,page.url),'auth_required')
        page.remove_listener.assert_called_once()

    def test_an_unrelated_article_api_401_does_not_invalidate_current_article(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.frames = []
        page.goto.return_value.status = 200
        response = MagicMock()
        response.url = 'https://article.cafe.naver.com/gw/v4/cafes/100/articles/999'
        response.request.resource_type = 'xhr'
        response.status = 401
        page.on.side_effect = lambda event, callback: callback(response)
        self.assertEqual(cookie_auth.page_status(page,page.url),'unknown')

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
