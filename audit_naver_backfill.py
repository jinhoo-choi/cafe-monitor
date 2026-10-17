"""Owner-requested recovery audit. Writes only a separate private audit; never sends mail or updates history."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from cookie_auth import BODY_SELECTOR, CHALLENGES, readable
from negative_queue import Queue, digest
from negative_verification import ALIASES, collector, item, mask
from playwright.sync_api import sync_playwright

START = datetime(2026, 10, 13, tzinfo=timezone(timedelta(hours=9)))
MAX_PAGES = 10
MAX_DETAILS = 300

def run():
    report = json.loads(Path('naver_cookie_status.json').read_text())
    if report.get('status') != 'healthy':
        raise RuntimeError('Healthy cookie preflight required; login is disabled')
    queue = Queue()
    queue.preflight()
    raw_seen, _ = queue.source('seen_posts.json')
    seen = json.loads(raw_seen)
    seen_hash = digest(seen)
    index, _ = queue.get('index.json')
    prior, prior_processed = {}, set()
    for job_id in (index or {}).get('jobs', []):
        job, _ = queue.get('jobs/' + job_id + '.json')
        if not job or job.get('mode') != 'production':
            continue
        if job.get('status') == 'completed':
            prior_processed.update(job.get('processed_ids', []))
        for entry in job.get('input', {}).get('items', []):
            prior.setdefault(entry['post_id'], []).append({
                'job_id': job_id, 'status': job.get('status'),
                'body_status': entry.get('body_status'),
                'receipt_count': len(job.get('receipts', {}))})
    module = collector()
    module.TIME_WINDOW = int((datetime.now(START.tzinfo) - START).total_seconds()/3600) + 25
    module.log = lambda *args, **kwargs: None
    result = {'schema_version': 1, 'bot_id': 'negative_monitor',
              'operation': 'owner_backfill_audit', 'created_at': datetime.now(timezone.utc).isoformat(),
              'since': START.isoformat(), 'seen_hash': seen_hash,
              'paid_ai_calls': 0, 'smtp_calls': 0, 'history_writes': 0,
              'coverage': [], 'items': []}
    details = 0
    human_required = False
    with redirect_stdout(StringIO()), sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state='naver_cookies.json')
        page = context.new_page()
        for cafe in [x for x in module.CAFES if x.get('login_required')]:
            name = '배당 투자자 모임' if cafe['id'] == 'divclub' else cafe['name']
            coverage = {'source': cafe['id'], 'name': name, 'queries': [],
                        'listed_unique': 0, 'previously_processed': 0, 'recovered': 0,
                        'body_missing': 0, 'collection_errors': 0}
            result['coverage'].append(coverage)
            found = {}
            for keyword in ALIASES:
                query = {'keyword': keyword, 'pages': 0, 'stop': 'page_limit', 'complete': False}
                coverage['queries'].append(query)
                previous = set()
                for number in range(1, MAX_PAGES + 1):
                    try:
                        posts = module.search_keyword(page, cafe['id'], cafe['num_id'], keyword, page_number=number)
                        query['pages'] = number
                        visible = page.inner_text('body')
                        if any(x in visible for x in CHALLENGES):
                            query['stop'] = 'human_required'
                            human_required = True
                            break
                        rows = []
                        for selector in ('table.article-table tbody tr', '.article-board tbody tr', '.ArticleItem'):
                            rows = [x for x in page.query_selector_all(selector)
                                    if 'board-notice' not in (x.get_attribute('class') or '')]
                            if rows:
                                break
                        dates = []
                        for row in rows:
                            for cell in row.query_selector_all('td'):
                                value = cell.inner_text().strip().split('\n')[0].strip().rstrip('.')
                                if re.fullmatch(r'(?:\d{1,2}:\d{2}|\d{4}\.\d{2}\.\d{2}|\d+분 전|\d+시간 전|방금|어제)', value):
                                    dates.append(module.parse_date(value))
                                    break
                        ids = {x['post_id'] for x in posts}
                        for post in posts:
                            if post.get('date_str') and post['post_time'] < START:
                                continue
                            found.setdefault(post['post_id'], post)
                        if ids and ids <= previous:
                            query['stop'] = 'repeated_page'
                            break
                        previous.update(ids)
                        if dates and max(dates) < START:
                            query.update(stop='older_than_start', complete=True)
                            break
                        if not rows and any(x in visible for x in ('검색 결과가 없습니다', '검색결과가 없습니다', '검색된 게시글이 없습니다', '게시글이 없습니다')):
                            query.update(stop='no_results', complete=True)
                            break
                        if not posts:
                            query['stop'] = 'no_parsed_window_items'
                            break
                    except Exception as error:
                        query.update(stop='collection_error', error_type=type(error).__name__)
                        coverage['collection_errors'] += 1
                        break
                if human_required:
                    break
            coverage['listed_unique'] = len(found)
            if cafe['id'] == 'stocktraining':
                coverage['membership'] = 'manager_approval_pending'
            for post in found.values():
                identifier = cafe['id'] + ':' + post['post_id']
                if identifier in seen or identifier in prior_processed:
                    coverage['previously_processed'] += 1
                    continue
                entry = item(identifier, post['title'], '', post['url'], 'cafe', 'historical', post.get('date_str', ''))
                entry['prior_collection'] = prior.get(identifier, [])
                entry['recovery_kind'] = 'previously_collected_unresolved' if identifier in prior else 'not_in_prior_collection'
                if cafe['id'] == 'stocktraining':
                    entry['access_status'] = 'manager_approval_pending'
                elif details >= MAX_DETAILS:
                    entry['access_status'] = 'detail_limit'
                else:
                    details += 1
                    try:
                        module.get_post_detail(page, post['url'], cafe['id'])
                        body = ''
                        for frame in page.frames:
                            parsed = urlparse(frame.url)
                            if parsed.hostname not in {'cafe.naver.com', 'ca-fe.naver.com'} or not re.search(
                                    r'/cafes/' + cafe['num_id'] + r'/articles/' + post['post_id'] + r'(?:[/?#]|$)', parsed.path):
                                continue
                            for element in frame.query_selector_all(BODY_SELECTOR):
                                candidate = element.inner_text().strip()
                                if readable(candidate):
                                    body = candidate
                                    break
                            if body:
                                break
                        if body:
                            recovered = item(identifier, post['title'], body, post['url'], 'cafe', 'historical', post.get('date_str', ''))
                            entry.update(recovered)
                            entry['access_status'] = 'body'
                            coverage['recovered'] += 1
                        else:
                            visible = page.inner_text('body')
                            if any(x in visible for x in CHALLENGES):
                                entry['access_status'] = 'human_required'
                                human_required = True
                            else:
                                entry['access_status'] = 'body_unavailable'
                    except Exception as error:
                        entry['access_status'] = 'collection_error'
                        entry['error_type'] = type(error).__name__
                        coverage['collection_errors'] += 1
                if entry['body_status'] != 'collected':
                    coverage['body_missing'] += 1
                result['items'].append(entry)
                if human_required:
                    break
            print_line = {'source': cafe['id'], 'listed': coverage['listed_unique'],
                          'recovered': coverage['recovered'], 'body_missing': coverage['body_missing']}
            # This is buffered by redirect_stdout; no article titles or bodies enter public logs.
            print(json.dumps(print_line), flush=True)
            if human_required:
                break
        context.close()
        browser.close()
    latest_seen, _ = queue.source('seen_posts.json')
    result['seen_unchanged'] = digest(json.loads(latest_seen)) == seen_hash
    result['human_required'] = human_required
    result['counts'] = {'listed': sum(x['listed_unique'] for x in result['coverage']),
                        'previously_processed': sum(x['previously_processed'] for x in result['coverage']),
                        'recovered': sum(x['recovered'] for x in result['coverage']),
                        'body_missing': sum(x['body_missing'] for x in result['coverage'])}
    path = 'audits/naver-backfill-' + os.environ['GITHUB_RUN_ID'] + '.json'
    if queue.get(path)[0] is not None:
        raise RuntimeError('Existing audit must not be overwritten')
    queue.put(path, result)
    saved, _ = queue.get(path)
    if digest(saved) != digest(result):
        raise RuntimeError('Private audit readback mismatch')
    print('[과거글점검] ' + json.dumps({'status': 'human_required' if human_required else 'completed',
          'counts': result['counts'], 'seen_unchanged': result['seen_unchanged'],
          'paid_ai_calls': 0, 'smtp_calls': 0, 'history_writes': 0}, ensure_ascii=False))

if __name__ == '__main__':
    run()
