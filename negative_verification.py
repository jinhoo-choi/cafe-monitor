"""실제 수집→구독 분석 결과 검증→본인 SMTP 검수. 운영 main·DB·API는 실행하지 않는다."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
import hashlib
import html
import importlib
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import smtplib
from urllib.parse import urlparse

import jsonschema

from negative_queue import BOT_ID, JOB_ID, POLICY, PREFIX, REPOSITORY, digest, now

KST = timezone(timedelta(hours=9))
MASKS = ('[이메일]', '[연락처]', '[계좌]', '[주민번호]', '[외부주소]')
ALIASES = ('한국투자증권', '한투', '뱅키스', 'BanKIS')


def config():
    value = json.loads(Path('negative_verification_config.json').read_text())
    if (value.get('engine') != 'verification_only' or value.get('production_enabled') is not False
            or value.get('bot_id') != BOT_ID or value.get('policy_version') != POLICY
            or value.get('queue_repository') != REPOSITORY or value.get('queue_prefix') != PREFIX):
        raise RuntimeError('본인 검수 설정 불일치 — 운영 발송 금지')
    return value


def seen_snapshot():
    path = Path(os.environ.get('NEGATIVE_SEEN_PATH', 'seen_posts.json'))
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('운영 DB 형식 오류 — 빈 이력으로 대체하지 않음')
    for identifier, timestamp in data.items():
        if not isinstance(identifier, str) or not isinstance(timestamp, str):
            raise ValueError('운영 DB ID·시각 형식 오류')
        datetime.fromisoformat(timestamp)
    return data, hashlib.sha256(raw).hexdigest()


def blocked(*args, **kwargs):
    raise RuntimeError('검수 경로에서 기존 API·발송·DB 쓰기 금지')


def collector():
    # 기존 수집 함수만 재사용한다. 원래 운영 진입점·3개 모델 경로·발송·DB 함수는 차단한다.
    os.environ['CLAUDE_API_KEY'] = ''
    for name in ('GMAIL_USER', 'GMAIL_APP_PW', 'NOTIFY_EMAIL'):
        os.environ.setdefault(name, 'verification-only')
    module = importlib.import_module('monitor')
    for name in ('main', 'analyze_sentiment', 'group_similar_alerts', 'regenerate_widespread_reply',
                 'send_alert_batch', 'send_status_email', '_notify_send_failure',
                 'init_db', 'cleanup_db', 'mark_seen', '_save_db'):
        setattr(module, name, blocked)
    module.requests.post = blocked
    module.KEYWORDS = list(ALIASES)
    return module


def mask(text):
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[이메일]', text or '')
    text = re.sub(r'\b0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}\b', '[연락처]', text)
    text = re.sub(r'\b\d{6}[- ]?[1-4]\d{6}\b', '[주민번호]', text)
    text = re.sub(r'\b\d{3}[- ]\d{2,6}[- ]\d{4,8}\b', '[계좌]', text)
    return re.sub(r'https?://[^\s<>]+', '[외부주소]', text)


def excerpt(body, limit=4000):
    text = mask(body)
    if len(text) <= limit:
        return text
    # 첫 문단만 자르지 않고 대상 회사/불만 근처 문단과 끝부분을 보존한다.
    spans = [(0, 1000), (max(0, len(text)-400), len(text))]
    hints = (*ALIASES, '주문', '출금', '수수료', '불만', '오류', '사기', '보상', 'MTS', 'HTS')
    folded = text.casefold()
    for hint in hints:
        start = folded.find(hint.casefold())
        if start >= 0:
            spans.append((max(0, start-220), min(len(text), start+420)))
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return '\n[중간 생략]\n'.join(text[lo:hi] for lo, hi in merged)[:limit]


def valid_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port:
        return False
    if parsed.netloc == 'cafe.naver.com':
        return bool(re.fullmatch(r'/f-e/cafes/\d+/articles/\d+', parsed.path))
    if parsed.netloc in {'blog.naver.com', 'm.blog.naver.com'}:
        return bool(re.fullmatch(r'/[A-Za-z0-9_-]+/\d{6,}', parsed.path))
    return False


def published(date_str):
    match = re.search(r'(20\d{2})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})', date_str or '')
    if match:
        try:
            time_match = re.search(r'(\d{1,2}):(\d{2})', (date_str or '')[match.end():])
            hour, minute = map(int, time_match.groups()) if time_match else (0, 0)
            return datetime(*map(int, match.groups()), hour, minute, tzinfo=KST).isoformat()
        except ValueError:
            return None
    return None


def item(post_id, title, body, url, source, dataset, date_str='', previous_score=None, reference_run=None):
    if not valid_url(url):
        raise ValueError('실제 게시글 URL 형식 오류')
    text = excerpt(body)
    return {'post_id': post_id, 'title': mask(title), 'body_excerpt': text,
            'body_status': 'collected' if body.strip() else 'missing', 'url': url,
            'source': source, 'dataset': dataset, 'published_at': published(date_str),
            'date_raw': date_str, 'body_original_chars': len(body), 'body_excerpt_chars': len(text),
            'body_truncated': len(mask(body)) > len(text),
            'target_present': any(a.casefold() in (title+' '+body).casefold() for a in ALIASES),
            'previous_score': previous_score, 'reference_run': reference_run}


def collect(queue, fixture_id=None):
    settings = config()
    queue.preflight()
    seen, state_hash = seen_snapshot()
    run_id = os.environ.get('GITHUB_RUN_ID', now().strftime('%Y%m%dT%H%M%S'))
    job_id = 'negative-verification-' + run_id
    if not JOB_ID.fullmatch(job_id) or queue.get('jobs/'+job_id+'.json')[0] is not None:
        raise ValueError('검수 회차 ID 오류 또는 중복')
    fixtures = None
    if fixture_id:
        if not JOB_ID.fullmatch(fixture_id):
            raise ValueError('과거 자료 식별자 오류')
        fixtures, _ = queue.get('fixtures/'+fixture_id+'.json')
        if not fixtures or fixtures.get('bot_id') != BOT_ID:
            raise ValueError('비공개 과거 검수 자료 없음')
    module = collector()
    from playwright.sync_api import sync_playwright
    inputs, coverage, historical, log_capture = [], [], [], StringIO()
    with redirect_stdout(log_capture), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        cookie_path = Path('naver_cookies.json')
        cookie_loaded = cookie_path.exists()
        context = browser.new_context(storage_state=str(cookie_path)) if cookie_loaded else browser.new_context()
        page = context.new_page()
        if cookie_loaded:
            try:
                page.goto('https://www.naver.com', wait_until='domcontentloaded', timeout=20000)
                if 'nid.naver.com' in page.url:
                    cookie_loaded = False
                    context.close()
                    context = browser.new_context()
                    page = context.new_page()
            except Exception:
                cookie_loaded = False
        for cafe in module.CAFES:
            record = {'source': cafe['id'], 'name': cafe['name'], 'status': 'ok', 'listed': 0, 'unseen': 0, 'selected': 0}
            coverage.append(record)
            if not cookie_loaded and cafe.get('login_required', True):
                record['status'] = 'cookie_required'
                continue
            try:
                posts = module.get_post_list(page, cafe['id'], cafe['num_id'])
                record['listed'] = len(posts)
                for post in posts:
                    identifier = cafe['id']+':'+post['post_id']
                    if identifier in seen:
                        continue
                    record['unseen'] += 1
                    _, full_body = module.get_post_detail(page, post['url'], cafe['id'])
                    inputs.append(item(identifier, post['title'], full_body, post['url'], 'cafe', 'live', post.get('date_str', '')))
                    record['selected'] += 1
            except Exception as error:
                record['status'] = 'collection_error'
                record['error_type'] = type(error).__name__
        record = {'source': 'naver_blog', 'status': 'ok', 'listed': 0, 'unseen': 0, 'selected': 0, 'invalid_urls': 0}
        coverage.append(record)
        try:
            posts = module.get_blog_post_list(page, list(ALIASES))
            record['listed'] = len(posts)
            cutoff = now()-timedelta(hours=24)
            for post in posts:
                if not valid_url(post['url']):
                    record['invalid_urls'] += 1
                    continue
                identifier = 'blog:'+post['post_id']
                if identifier in seen:
                    continue
                record['unseen'] += 1
                detail = module.get_blog_detail(page, post['url'])
                known_date = published(detail['date_str'])
                if known_date:
                    dated = datetime.fromisoformat(known_date)
                    # 시각이 없는 전일 글은 날짜만으로 24시간 초과라고 단정하지 않는다.
                    if not re.search(r'\d{1,2}:\d{2}', detail['date_str']):
                        dated += timedelta(days=1)
                    if dated < cutoff:
                        continue
                inputs.append(item(identifier, detail['title'] or '제목 미수집', detail.get('body_full', ''), post['url'], 'blog', 'live', detail['date_str']))
                record['selected'] += 1
        except Exception as error:
            record['status'] = 'collection_error'
            record['error_type'] = type(error).__name__
        for spec in (fixtures or {}).get('sources', []):
            record = {'reference_run': spec.get('reference_run'), 'status': 'ok'}
            historical.append(record)
            try:
                if spec['source'] == 'blog':
                    if not valid_url(spec['url']):
                        raise ValueError('과거 블로그 URL 오류')
                    detail = module.get_blog_detail(page, spec['url'])
                    identifier = 'blog:'+urlparse(spec['url']).path.rsplit('/',1)[-1]
                    record['post_id'] = identifier
                    inputs.append(item(identifier, detail['title'] or '제목 미수집', detail.get('body_full',''), spec['url'], 'blog', 'historical', detail['date_str'], spec.get('previous_score'), spec.get('reference_run')))
                elif spec['source'] == 'cafe':
                    cafe = next(c for c in module.CAFES if c['id'] == spec['cafe_id'])
                    if spec.get('url'):
                        if not valid_url(spec['url']) or not spec['url'].startswith(
                                'https://cafe.naver.com/f-e/cafes/'+cafe['num_id']+'/articles/'):
                            raise ValueError('과거 카페 URL·수집 대상 불일치')
                        target = {'post_id':urlparse(spec['url']).path.rsplit('/',1)[-1],
                                  'title':spec['title_query'],'url':spec['url'],'date_str':spec.get('date_raw','')}
                        _, body = module.get_post_detail(page,target['url'],cafe['id'])
                        identifier = cafe['id']+':'+target['post_id']
                        record['post_id'] = identifier
                        inputs.append(item(identifier,target['title'],body,target['url'],'cafe','historical',
                                           target['date_str'],spec.get('previous_score'),spec.get('reference_run')))
                        continue
                    original_window = module.TIME_WINDOW
                    try:
                        module.TIME_WINDOW = 24*60
                        found = module.search_keyword(page, cafe['id'], cafe['num_id'], spec['title_query'])
                    finally:
                        module.TIME_WINDOW = original_window
                    target = next((p for p in found if p['title'].strip() == spec['title_query'].strip()), None)
                    if not target:
                        record['status'] = 'source_not_found'
                        continue
                    _, body = module.get_post_detail(page, target['url'], cafe['id'])
                    identifier = cafe['id']+':'+target['post_id']
                    record['post_id'] = identifier
                    inputs.append(item(identifier, target['title'], body, target['url'], 'cafe', 'historical', target.get('date_str',''), spec.get('previous_score'), spec.get('reference_run')))
                else:
                    raise ValueError('과거 자료 소스 오류')
            except Exception as error:
                record['status'] = 'collection_error'
                record['error_type'] = type(error).__name__
        context.close()
        browser.close()
    by_id = {}
    for entry in inputs:
        if entry['post_id'] not in by_id or entry['dataset'] == 'historical':
            by_id[entry['post_id']] = entry
    _, after_hash = seen_snapshot()
    if after_hash != state_hash:
        raise RuntimeError('검수 수집 중 운영 이력 변경 감지')
    job = {'schema_version': 1, 'bot_id': BOT_ID, 'mode': 'verification', 'self_test': True,
           'job_id': job_id, 'status': 'pending', 'created_at': now().isoformat(),
           'expires_at': (now()+timedelta(minutes=settings['job_ttl_minutes'])).isoformat(),
           'input': {'policy_version': POLICY, 'seen_hash': state_hash, 'items': list(by_id.values()),
                     'collection': {'coverage': coverage, 'historical': historical, 'cookie_loaded': cookie_loaded}},
           'receipts': {}, 'paid_ai_calls': 0}
    job['input_hash'] = digest(job['input'])
    queue.put('jobs/'+job_id+'.json', job)
    index, sha = queue.get('index.json')
    index = index or {'bot_id': BOT_ID, 'jobs': []}
    if index.get('bot_id') != BOT_ID:
        raise ValueError('다른 봇 큐 인덱스')
    index['jobs'] = (index['jobs']+[job_id])[-60:]
    queue.put('index.json', index, sha)
    queue.put('collection-logs/'+job_id+'.json', {'log': mask(log_capture.getvalue())[-10000:], 'paid_ai_calls': 0})
    print(json.dumps({'job_id': job_id, 'items': len(by_id), 'sources': len(coverage),
                      'historical_sources': len(historical), 'paid_ai_calls': 0, 'smtp_calls': 0}))


def validate(job, result):
    config()
    if (job.get('schema_version') != 1 or job.get('bot_id') != BOT_ID
            or job.get('mode') != 'verification' or job.get('self_test') is not True):
        raise ValueError('다른 봇 또는 운영 회차는 본인 검수 불가')
    if datetime.fromisoformat(job['expires_at']) <= now():
        raise ValueError('검수 입력 만료')
    if job['input'].get('policy_version') != POLICY or job['input_hash'] != digest(job['input']):
        raise ValueError('입력 해시 또는 정책 불일치')
    schema = json.loads(Path('negative_result_schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(result)
    if result['job_id'] != job['job_id'] or result['input_hash'] != job['input_hash']:
        raise ValueError('결과 회차·입력 해시 불일치')
    inputs = {p['post_id']: p for p in job['input']['items']}
    results = {p['post_id']: p for p in result['posts']}
    if len(inputs) != len(job['input']['items']) or len(results) != len(result['posts']) or set(inputs) != set(results):
        raise ValueError('전체 입력 ID 누락·중복·추가')
    for identifier, post in results.items():
        source = inputs[identifier]
        if not valid_url(source['url']):
            raise ValueError('검수 원문 URL 오류')
        if source['body_status'] != 'collected' and post['verdict'] != 'needs_review':
            raise ValueError('본문 미확보는 확인필요로 보류')
        score = post['score']
        if score is not None and (type(score) not in {int,float} or not math.isfinite(score)):
            raise ValueError('유한한 점수만 허용')
        if ((score is not None and score >= 7) or post['urgency'] == 'high') and post['reviewed'] is not True:
            raise ValueError('높은 강도·긴급 사안 검토 미완료')
        if post['verdict'] == 'negative' and not source.get('target_present'):
            raise ValueError('한국투자증권 실제 언급 없음')
        for evidence in post['evidence_quotes']:
            if evidence['quote'] not in source[evidence['field']] or any(m in evidence['quote'] for m in MASKS):
                raise ValueError('실제 연속 인용 또는 개인정보 마스킹 검증 실패')
        numbers = lambda text: set(re.findall(r'\d+(?:\.\d+)?', text.replace(',','')))
        source_numbers = numbers(source['title']+' '+source['body_excerpt'])
        if not numbers(post['summary']) <= source_numbers:
            raise ValueError('요약 원문 미확인 수치')
        if not (numbers(post['internal_action']) | numbers(post['reply_draft'])) <= source_numbers | {'1544','5000'}:
            raise ValueError('대응 문구 원문 미확인 수치')
        if re.search(r'저는|저도|제가|이미 해결됐|정상입니다', post['reply_draft']):
            raise ValueError('미확인 개인 경험 또는 해결 단정 금지')
    negative = {identifier for identifier,p in results.items() if p['verdict']=='negative'}
    grouped, group_ids = [], []
    for group in result['groups']:
        members = group['member_post_ids']
        if not set(members) <= negative or group['representative_post_id'] not in members:
            raise ValueError('그룹에 비부정·확인필요 또는 외부 대표 ID')
        if len({results[m]['category'] for m in members}) != 1 or len({inputs[m]['dataset'] for m in members}) != 1:
            raise ValueError('다른 증상·현재/과거 회차의 과도한 묶음')
        grouped += members
        group_ids.append(group['group_id'])
    if set(grouped) != negative or len(grouped) != len(set(grouped)) or len(group_ids) != len(set(group_ids)):
        raise ValueError('부정 ID 그룹 누락·중복')
    return inputs, results


def selected_groups(result):
    posts = {p['post_id']:p for p in result['posts']}
    ordered = sorted(result['groups'], key=lambda g: (posts[g['representative_post_id']]['urgency']!='high',
                                                    -posts[g['representative_post_id']]['score']))
    bound = config()['max_email_groups']
    return ordered[:bound], ordered[bound:]


def render(job, result):
    inputs, posts = validate(job, result)
    selected, deferred = selected_groups(result)
    counts = {verdict: sum(p['verdict']==verdict for p in posts.values()) for verdict in ('negative','non_negative','needs_review')}
    esc = html.escape
    categories = {'app_access':'접속·로그인','order_execution':'주문·체결','withdrawal_settlement':'출금·결제',
                  'fees':'수수료','customer_support':'고객응대','security':'보안 확인',
                  'company_reputation':'회사 평판','neutral_other':'일반·비부정','unresolved':'원문 확인'}
    priorities = {'high':'우선 확인','normal':'일반 점검','watch':'경과 관찰'}
    verdicts = {'negative':'부정','non_negative':'비부정','needs_review':'확인 필요'}
    cards = []
    for group in selected:
        identifier = group['representative_post_id']
        source, post = inputs[identifier], posts[identifier]
        label = '실제 과거 게시글 재검수' if source['dataset']=='historical' else '실제 최근 수집 게시글'
        previous = f" · 당시 로그 {source['previous_score']}점" if source.get('previous_score') is not None else ''
        quotes = '<br>'.join('“'+esc(q['quote'])+'”' for q in post['evidence_quotes'])
        cards.append(f'''<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dce3eb;margin-bottom:18px;border-radius:8px;"><tr><td style="padding:18px;">
<p style="font-size:12px;color:#697586;margin:0 0 8px;">{label}{esc(previous)} · 게시글 {len(group['member_post_ids'])}건(피해자 수 아님)</p>
<h3 style="margin:0 0 10px;font-size:17px;"><a href="{esc(source['url'],quote=True)}" style="color:#24344b;">{esc(source['title'])}</a></h3>
<p><b>부정 강도 {post['score']}/10</b> · {esc(categories[post['category']])} · {esc(priorities[post['urgency']])}</p>
<p><b>AI 요약</b><br>{esc(post['summary'])}</p>
<p><b>내부 확인·대응</b><br>{esc(post['internal_action'])}</p>
<p style="background:#f1f6fb;padding:12px;"><b>검토용 답변 초안 — 자동 게시 안 함</b><br>{esc(post['reply_draft'])}</p>
<p style="font-size:12px;color:#667085;"><b>원문 근거</b><br>{quotes}</p></td></tr></table>''')
    unresolved = ''.join(f"<li>{esc(inputs[p['post_id']]['title'])}<br>{esc(p['reason'])}<br>{esc(p['internal_action'])}</li>" for p in posts.values() if p['verdict']=='needs_review')
    audit = ''.join(f"<tr><td style='padding:7px;border-bottom:1px solid #eee;'>{esc(inputs[p['post_id']]['title'])}</td><td>{esc(verdicts[p['verdict']])}</td><td>{esc(p['reason'])}</td></tr>" for p in posts.values() if p['verdict']!='negative')
    coverage = job['input'].get('collection',{}).get('coverage',[])
    failures = sum(s['status']!='ok' for s in coverage)
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f3f5f8;font-family:Arial,sans-serif;color:#24344b;line-height:1.7;"><table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center"><table width="640" style="max-width:640px;width:100%;background:white;" cellpadding="22"><tr><td>
<p style="font-size:12px;color:#356b50;">[TEST] 본인 전용 · 유료 AI API 0 · 운영 발송 0 · 운영 DB 쓰기 0</p>
<h1 style="font-size:24px;">부정여론봇 개선안 실제 환경 검수</h1>
<p>실제 최근 수집과 실제 과거 게시글을 구분한 검수입니다. 작성자의 주장과 사실 확인 여부를 구별하며 게시글 수를 피해자 수로 표현하지 않습니다.</p>
<p><b>전체 {len(posts)}건 · 부정 {counts['negative']}건 · 비부정 {counts['non_negative']}건 · 확인 필요 {counts['needs_review']}건</b><br>메일 선정 이슈 {len(selected)}건 · 이월 {len(deferred)}건 · 수집 상태 확인 필요 {failures}곳</p>
{''.join(cards)}<h2 style="font-size:18px;">본문·귀속 확인 필요</h2><ul>{unresolved or '<li>해당 없음</li>'}</ul>
<h2 style="font-size:18px;">비부정·보류 판정 근거</h2><table width="100%" style="font-size:12px;"><tr><th>원문 제목</th><th>판정</th><th>근거</th></tr>{audit}</table>
<p style="font-size:12px;color:#667085;">회차 {esc(job['job_id'])} · 구독 분석 모델 {esc(result['model_used'])}<br>보류는 정상으로 간주하지 않습니다. 운영 전환과 게시글 자동 답변은 이번 검수 범위에 포함되지 않습니다.</p>
</td></tr></table></td></tr></table></body></html>'''


def owner():
    address = os.environ.get('GMAIL_USER','').strip()
    if (os.environ.get('TEST_MODE')!='1' or not address or not os.environ.get('GMAIL_APP_PW')
            or os.environ.get('NOTIFY_EMAIL','').strip()!=address or os.environ.get('EMAIL_CC','').strip()):
        raise RuntimeError('본인 검수 수신자 격리 설정 실패')
    return address


def send_owner(subject, body, address):
    if address != owner():
        raise RuntimeError('본인 이외 envelope 수신자 금지')
    message = MIMEMultipart('alternative')
    message['Subject'] = subject
    message['From'] = formataddr((str(Header('부정여론봇','utf-8')), address))
    message['To'] = address
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain='gmail.com')
    message.attach(MIMEText(body,'html','utf-8'))
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(address,os.environ['GMAIL_APP_PW'])
        refused = server.sendmail(address,[address],message.as_string())
    if refused:
        raise RuntimeError('본인 테스트메일 SMTP 거부')


def consume(queue, job_id):
    config()
    address = owner()
    if not JOB_ID.fullmatch(job_id):
        raise ValueError('검수 회차 ID 오류')
    queue.preflight()
    path = 'jobs/'+job_id+'.json'
    job, sha = queue.get(path)
    if job and job.get('status')=='verified':
        print('이미 검수 완료; 재발송0/API0')
        return
    if not job or job.get('status')!='pending' or job.get('receipts'):
        raise RuntimeError('검수 미종결 전송 상태 — 재발송 금지')
    result, _ = queue.get('results/'+job_id+'.json')
    if result is None:
        raise RuntimeError('구독 결과 미확인')
    validate(job,result)
    _, state_hash = seen_snapshot()
    if state_hash != job['input']['seen_hash']:
        raise RuntimeError('검수 입력 이후 운영 이력 변경 — 본인 발송 보류')
    body = render(job,result)
    selected, deferred = selected_groups(result)
    queue.put('previews/'+job_id+'.json',{'html':body,'paid_ai_calls':0})
    queue.put('deferred/'+job_id+'.json',{'groups':deferred,'mode':'verification','production_seen_writes':0})
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(address,os.environ['GMAIL_APP_PW'])
    latest, latest_sha = queue.get(path)
    if latest.get('status')!='pending' or latest.get('receipts') or latest['input_hash']!=job['input_hash']:
        raise RuntimeError('검수 발송 직전 상태 변경')
    validate(latest,result)
    key = hashlib.sha256(address.encode()).hexdigest()
    latest.update(status='sending',sending_at=now().isoformat(),receipts={key:'sending'})
    queue.put(path,latest,latest_sha)
    subject = '[TEST] [부정여론] 구독 개선안 실제·과거 게시글 검수 | ' + now().astimezone(KST).strftime('%m월 %d일 %H:%M')
    try:
        send_owner(subject,body,address)
        latest, sha = queue.get(path)
        latest['receipts'][key] = 'accepted'
        queue.put(path,latest,sha)
        _, after_hash = seen_snapshot()
        if after_hash != state_hash:
            raise RuntimeError('본인 검수 중 운영 이력 변경')
        latest, sha = queue.get(path)
        latest.update(status='verified',verified_at=now().isoformat(),self_test=True,paid_ai_calls=0,
                      production_recipients=0,production_seen_writes=0,seen_after_hash=after_hash,
                      test_subject=subject,selected_groups=len(selected),deferred_groups=len(deferred))
        queue.put(path,latest,sha)
    except Exception:
        latest, sha = queue.get(path)
        latest['status'] = 'uncertain'
        queue.put(path,latest,sha)
        raise
    print(json.dumps({'job_id':job_id,'status':'verified','self_test':True,'smtp_accepted':1,
                      'production_recipients':0,'production_seen_writes':0,'paid_ai_calls':0}))
