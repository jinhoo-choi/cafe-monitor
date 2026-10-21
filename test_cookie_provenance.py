import unittest
from unittest.mock import MagicMock, patch
import cookie_auth


class ProbeProvenance(unittest.TestCase):
    def test_matching_article_cookie_delivery_reports_names_only(self):
        for header, expected in [('NID_AUT=PRIVATE-A; NID_SES=PRIVATE-B', 'present'), ('tracking=PRIVATE-C', 'missing')]:
            page, response, diagnostic = MagicMock(), MagicMock(), {}
            page.url='https://cafe.naver.com/f-e/cafes/100/articles/101'
            page.frames=[]; page.goto.return_value.status=200
            response.url='https://article.cafe.naver.com/gw/v4/cafes/100/articles/101'
            response.status=401; response.request.resource_type='xhr'
            response.request.all_headers.return_value={'cookie':header}
            page.on.side_effect=lambda event, callback:callback(response)
            self.assertEqual(cookie_auth.page_status(page,page.url,diagnostic),'auth_required')
            self.assertEqual(diagnostic['article_cookie_presence'],[{'NID_AUT':expected,'NID_SES':expected}])
            self.assertNotIn('PRIVATE',str(diagnostic))

    def test_unavailable_cookie_headers_do_not_look_missing(self):
        page, response, diagnostic = MagicMock(), MagicMock(), {}
        page.url='https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.frames=[]; page.goto.return_value.status=200
        response.url='https://article.cafe.naver.com/gw/v4/cafes/100/articles/101'
        response.status=401; response.request.resource_type='xhr'
        response.request.all_headers.side_effect=RuntimeError('PRIVATE-HEADERS')
        page.on.side_effect=lambda event, callback:callback(response)
        self.assertEqual(cookie_auth.page_status(page,page.url,diagnostic),'auth_required')
        self.assertEqual(set(diagnostic['article_cookie_presence'][0].values()),{'unverified'})
        self.assertNotIn('PRIVATE',str(diagnostic))

    def test_collector_does_not_treat_wrapper_comments_as_article_body(self):
        import negative_verification
        module=negative_verification.collector()
        page=MagicMock()
        page.url='https://cafe.naver.com/f-e/cafes/100/articles/101'
        frame=MagicMock()
        frame.url='https://cafe.naver.com/ca-fe/cafes/100/articles/101'
        shell=MagicMock()
        shell.inner_text.return_value='제목 댓글 등록순 클린봇 한국투자증권 불편합니다'
        frame.query_selector.side_effect=lambda selector: shell if selector=='.ArticleContentBox' else None
        page.frames=[frame]
        page.query_selector.return_value=None
        with patch.object(module,'log'):
            self.assertEqual(module.get_post_detail(page,page.url,'offline'),('',''))

    def test_collector_uses_only_requested_article_body_and_ignores_shell(self):
        import negative_verification
        module=negative_verification.collector()
        page=MagicMock()
        page.url='https://cafe.naver.com/f-e/cafes/100/articles/101'
        frame=MagicMock()
        frame.url='https://cafe.naver.com/ca-fe/cafes/100/articles/101'
        body=MagicMock()
        body.inner_text.return_value='실제 한국투자증권 게시글 본문입니다.'
        date=MagicMock()
        date.inner_text.return_value='2026.10.16. 14:47'
        frame.query_selector.side_effect=lambda selector: {'.se-main-container':body,'.article_info .date':date}.get(selector)
        page.frames=[frame]
        details={}
        with patch.object(module,'log'):
            self.assertEqual(module.get_post_detail(page,page.url,'offline',details=details),(body.inner_text.return_value,)*2)
        self.assertEqual(details,{'date_str':'2026.10.16. 14:47'})

    def test_collector_rejects_another_article_frame_and_untrusted_host(self):
        import negative_verification
        module=negative_verification.collector()
        for url in ('https://cafe.naver.com/ca-fe/cafes/100/articles/999',
                    'https://cafe.naver.com.evil.invalid/ca-fe/cafes/100/articles/101'):
            page=MagicMock()
            page.url='https://cafe.naver.com/f-e/cafes/100/articles/101'
            frame=MagicMock()
            frame.url=url
            frame.query_selector.return_value.inner_text.return_value='다른 게시글의 한국투자증권 본문'*10
            page.frames=[frame]
            page.query_selector.return_value=None
            with self.subTest(url=url),patch.object(module,'log'):
                self.assertEqual(module.get_post_detail(page,page.url,'offline'),('',''))

    def test_verified_article_api_body_is_readable_when_dom_is_only_menu(self):
        page = MagicMock()
        page.url = 'https://cafe.naver.com/f-e/cafes/100/articles/101'
        page.frames=[]; page.goto.return_value.status=200
        response=MagicMock()
        response.url='https://article.cafe.naver.com/gw/v4/cafes/100/articles/101'
        response.status=200; response.request.resource_type='xhr'
        response.json.return_value={'result':{'cafeId':100,'articleId':101,'article':{
            'isReadable':True,'contentHtml':'<p>요청한 기사의 실제 본문을 확인했습니다.</p>'*10}}}
        page.on.side_effect=lambda event,callback: callback(response)
        self.assertEqual(cookie_auth.page_status(page,page.url),'body')

    def test_article_json_actual_body_counts_without_client_rendering(self):
        value={'result':{'cafeId':100,'articleId':101,'article':{'isReadable':True,
            'contentHtml':'<p>실제 게시글 본문을 요청한 원문에서 확인했습니다.</p>'*5}}}
        self.assertTrue(cookie_auth.article_api_body(value,'100','101'))
        self.assertFalse(cookie_auth.article_api_body(value,'100','999'))
        value['result']['article']['isReadable']=False
        self.assertFalse(cookie_auth.article_api_body(value,'100','101'))

    def test_article_image_can_verify_access_without_claiming_text_analysis(self):
        value={'result':{'cafeId':100,'articleId':101,'article':{'isReadable':True,
            'contentHtml':'<img src="https://cafefiles.pstatic.net/fake-image.png">'}}}
        self.assertTrue(cookie_auth.article_api_body(value,'100','101'))

    def test_script_error_mask_and_empty_metadata_are_not_article_body(self):
        for content in ['<script>기사 본문처럼 보이는 스크립트입니다</script>'*10,
                        '<style>한국투자증권</style>'*10,'<p>[외부주소]</p>'*100,'']:
            value={'result':{'cafeId':100,'articleId':101,'article':{'isReadable':True,'contentHtml':content}}}
            self.assertFalse(cookie_auth.article_api_body(value,'100','101'))
        self.assertFalse(cookie_auth.article_api_body({'result':{'cafeId':100,'articleId':101}},'100','101'))

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

