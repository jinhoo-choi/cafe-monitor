"""Read-only cookie validation; never log credentials, page text or probe URLs."""
import json
from html.parser import HTMLParser
import math
import re
import time
import unicodedata
from urllib.parse import urlparse

CORE = {'NID_AUT', 'NID_SES'}
BODY_SELECTOR = '.se-main-container, .article-viewer, .ContentRenderer, #tbody, .article_body'
CHALLENGES = ('자동입력 방지', '보안문자', '휴대전화 인증', '본인 확인', '2단계 인증', '비정상적인 접근')
LOGIN_HINTS = ('로그인이 필요', '로그인 후 이용', '로그인해 주세요', '로그인해주세요')
MEMBER_HINTS = ('카페 멤버만', '카페 회원만', '가입한 멤버만', '멤버만 볼 수', '카페에 가입된 회원만')
PERMISSION_HINTS = ('등급 이상', '읽기 권한', '접근 권한', '게시글이 삭제', '삭제된 게시글')


def storage_status(state, instant=None):
    """Only login cookies affect expiry; -1 is a session cookie."""
    instant = time.time() if instant is None else instant
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list):
        return 'invalid'
    found = set()
    for cookie in state['cookies']:
        if not isinstance(cookie, dict):
            return 'invalid'
        if cookie.get('name') not in CORE:
            continue
        domain = str(cookie.get('domain', '')).lstrip('.').lower()
        if domain != 'naver.com' and not domain.endswith('.naver.com'):
            continue
        if not isinstance(cookie.get('value'), str) or not cookie['value'].strip():
            continue
        expiry = cookie.get('expires', -1)
        if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or expiry < -1):
            return 'invalid'
        if expiry != -1 and expiry <= instant:
            continue
        found.add(cookie['name'])
    return 'present' if found == CORE else 'invalid'


def probe_urls(raw):
    """Two distinct Naver article URLs only; no query strings or external redirects."""
    try:
        values = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list) or len(values) != 2:
        return []
    for value in values:
        if not isinstance(value, str):
            return []
        parsed = urlparse(value)
        if (parsed.scheme != 'https' or parsed.netloc != 'cafe.naver.com'
                or parsed.query or parsed.fragment
                or not re.fullmatch(r'/(?:f-e/cafes/\d+/articles/\d+|[A-Za-z0-9_-]+/\d+)', parsed.path)):
            return []
    return values if len(set(values)) == 2 else []


def readable(text):
    text = re.sub(r'https?://\S+|\[(?:외부주소|링크|URL|이메일|연락처|계좌|주민번호)\]', '', text)
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    return len(re.findall(r'[가-힣A-Za-z0-9]', text)) >= 30


def article_api_body(obj, cafe_id, article_id):
    """Only the requested, readable article's actual content counts; never menus/error HTML."""
    result = obj.get('result', {}) if isinstance(obj, dict) else {}
    if not isinstance(result, dict) or str(result.get('cafeId')) != cafe_id or str(result.get('articleId')) != article_id:
        return False
    article = result.get('article', {})
    if not isinstance(article, dict) or article.get('isReadable') is not True or article.get('isBlind') is True:
        return False
    content = article.get('contentHtml', '')
    if not isinstance(content, str) or len(content) > 2_000_000:
        return False
    class Content(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text, self.media, self.hidden = [], False, 0
        def handle_starttag(self, tag, attrs):
            if tag in {'script','style'}:
                self.hidden += 1
            if not self.hidden and tag in {'img','video','audio'} and dict(attrs).get('src'):
                self.media = True
        def handle_endtag(self, tag):
            if tag in {'script','style'} and self.hidden:
                self.hidden -= 1
        def handle_data(self, text):
            if not self.hidden:
                self.text.append(text)
    parsed = Content()
    parsed.feed(content)
    return readable(' '.join(parsed.text)) or parsed.media


def page_status(page, url, diagnostic=None):
    """A public homepage or HTTP 200 never proves authenticated access."""
    target = re.search(r'/cafes/(\d+)/articles/(\d+)', urlparse(url).path)
    article_responses = []
    api_bodies = []
    def matching(response):
        parsed = urlparse(response.url)
        return bool(target and parsed.hostname in {'article.cafe.naver.com', 'apis.naver.com', 'cafe.naver.com'}
            and re.search(r'/cafes/' + target[1] + '/articles/' + target[2] + r'/?$', parsed.path)
            and response.request.resource_type in {'fetch', 'xhr'})
    def capture(response):
        try:
            if matching(response):
                article_responses.append(response.status)
                if response.status == 200:
                    api_bodies.append(article_api_body(response.json(), target[1], target[2]))
        except Exception:
            pass
    page.on('response', capture)
    try:
        response = None
        try:
            # Article XHR can start after the sidebar has rendered. Register BEFORE navigation.
            with page.expect_response(matching, timeout=16000):
                response = page.goto(url, wait_until='domcontentloaded', timeout=12000)
        except Exception:
            if response is None:
                raise
        if diagnostic is not None:
            diagnostic['http_status'] = response.status if response is not None else None
            diagnostic['final_host'] = urlparse(page.url).hostname
        if response is None or response.status >= 400:
            return 'unknown'
        try:
            page.wait_for_selector(BODY_SELECTOR + ', iframe#cafe_main', timeout=4000)
        except Exception:
            pass
        frames = [f for f in page.frames if urlparse(f.url).hostname in {
            'cafe.naver.com', 'ca-fe.naver.com', 'nid.naver.com'}]
        if diagnostic is not None:
            diagnostic['frame_hosts'] = sorted({urlparse(f.url).hostname for f in page.frames if urlparse(f.url).hostname})
        for frame in frames:
            if target and not re.search(r'/cafes/' + target[1] + r'/articles/' + target[2] + r'(?:[/?#]|$)', frame.url):
                continue
            if not target and urlparse(frame.url).path != urlparse(url).path:
                continue
            try:
                frame.wait_for_selector(BODY_SELECTOR, timeout=4000)
            except Exception:
                pass
            for element in frame.query_selector_all(BODY_SELECTOR):
                if readable(element.inner_text()):
                    return 'body'
        if diagnostic is not None:
            diagnostic['article_http_statuses'] = sorted(set(article_responses))
            diagnostic['article_body_available'] = any(api_bodies)
        if any(api_bodies):
            return 'body'
        # The SPA can show only the cafe menu after its article API returns 401.
        # Do not mistake the outer page's HTTP 200 for successful article access.
        if 401 in article_responses:
            return 'auth_required'
        if 403 in article_responses:
            return 'permission_denied'
        # Only inspect access walls when no article body exists.
        if 'nidlogin' in urlparse(page.url).path:
            return 'auth_required'
        texts = [frame.inner_text('body')[:12000] for frame in frames]
        if diagnostic is not None:
            diagnostic['wall_markers'] = [label for label in ('로그인', '가입', '권한', '삭제', '오류', '접근', '멤버', '인증')
                                          if any(label in text for text in texts)]
        if any(any(h in text for h in CHALLENGES) for text in texts):
            return 'human_required'
        if any(any(h in text for h in PERMISSION_HINTS) for text in texts):
            return 'permission_denied'
        if any(any(h in text for h in LOGIN_HINTS) for text in texts):
            return 'auth_required'
        if any(any(h in text for h in MEMBER_HINTS) for text in texts):
            return 'member_required'
    except Exception as error:
        if diagnostic is not None:
            diagnostic['error_type'] = type(error).__name__
    finally:
        page.remove_listener('response', capture)
    return 'unknown'


def access_status(browser, state, urls, checks=None):
    if len(urls) != 2:
        return 'not_configured'
    anonymous = browser.new_context()
    try:
        page = anonymous.new_page()
        details = [{}, {}]
        gates = [page_status(page, url, diagnostic=detail) for url, detail in zip(urls, details)]
        if checks is not None:
            checks['anonymous'] = gates
            checks['anonymous_details'] = details
    finally:
        anonymous.close()
    # Each probe must independently fail without a cookie; public/removed/unknown posts are insufficient.
    if any(value not in {'auth_required', 'member_required'} for value in gates):
        # A protected probe can still diagnose current access when another configured probe is public.
        # It cannot turn the pair into healthy or authorize a login.
        if checks is not None and storage_status(state) == 'present' and any(
                gate in {'auth_required', 'member_required'} for gate in gates):
            context = browser.new_context(storage_state=state)
            try:
                page = context.new_page()
                checks['partial_authenticated'] = {
                    str(i+1): page_status(page, url) for i, (url, gate) in enumerate(zip(urls, gates))
                    if gate in {'auth_required', 'member_required'}}
            finally:
                context.close()
        return 'probe_unconfirmed'
    if storage_status(state) != 'present':
        return 'invalid'
    authenticated = browser.new_context(storage_state=state)
    try:
        page = authenticated.new_page()
        details = [{}, {}]
        results = [page_status(page, url, diagnostic=detail) for url, detail in zip(urls, details)]
        if checks is not None:
            checks['authenticated'] = results
            checks['authenticated_details'] = details
    finally:
        authenticated.close()
    if results == ['body', 'body']:
        return 'healthy'
    if 'human_required' in results:
        return 'human_required'
    if all(value == 'auth_required' for value in results):
        return 'invalid'
    if any(value in {'body', 'permission_denied', 'member_required'} for value in results):
        return 'permission_denied'
    return 'unknown'


def collector_auth(state, report=None):
    """Unknown access preserves existing collection attempts, without claiming verified login."""
    present = storage_status(state) == 'present'
    status = (report or {}).get('status', 'unconfirmed')
    if status in {'invalid', 'human_required'} or (report or {}).get('access_status') == 'invalid':
        present = False
    return present, status, present and status in {'healthy', 'renewed'}
