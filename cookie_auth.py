"""Read-only cookie validation; never log credentials, page text or probe URLs."""
import json
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


def page_status(page, url, diagnostic=None):
    """A public homepage or HTTP 200 never proves authenticated access."""
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=12000)
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
        target = re.search(r'/cafes/(\d+)/articles/(\d+)', urlparse(url).path)
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
