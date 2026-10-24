"""Offline manual backfill preparation. No browser, queue, SMTP, API or seen writes."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from cookie_auth import readable
from negative_queue import BOT_ID, JOB_ID, POLICY, digest, now
import negative_verification as common


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('확인 시각 필요')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('확인 시각 형식 오류') from None
    if parsed.tzinfo is None or parsed > now():
        raise ValueError('확인 시각은 시간대가 있는 과거 시각이어야 함')
    return parsed


def prepare(value, job_id):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or not job_id.startswith('negative-manual-'):
        raise ValueError('수동 검수 회차 ID 오류')
    if not isinstance(value, dict) or set(value) != {'schema_version', 'items'} or value['schema_version'] != 1:
        raise ValueError('수동 입력 형식 오류')
    rows = value['items']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError('수동 입력은 1~200건 필요')
    # Reuse the registered source list; collector() blocks every legacy API/mail/DB writer.
    cafes = common.collector().CAFES
    seen, seen_hash = common.seen_snapshot()
    inputs, identifiers, skipped = [], set(), 0
    fields = {'post_id', 'title', 'url', 'body', 'date_raw', 'observed_at', 'body_verified'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('수동 게시글 필드 오류')
        if any(not isinstance(row[k], str) for k in fields - {'body_verified'}):
            raise ValueError('수동 게시글 문자열 필드 오류')
        if row['body_verified'] is not True or not readable(row['body']) or not row['title'].strip():
            raise ValueError('권한 있는 원문에서 직접 확인한 제목·본문 필요')
        observed = timestamp(row['observed_at'])
        if not common.valid_url(row['url']):
            raise ValueError('게시글 기본 HTTPS 주소 필요')
        parsed = urlparse(row['url'])
        if parsed.query or parsed.fragment:
            raise ValueError('검색·인증 매개변수 없는 주소 필요')
        article_id = parsed.path.rsplit('/', 1)[-1]
        if parsed.hostname == 'cafe.naver.com':
            cafe = next((c for c in cafes if parsed.path.startswith('/f-e/cafes/'+c['num_id']+'/articles/')), None)
            if cafe is None:
                raise ValueError('기존 수집 대상 카페만 수동 보완 가능')
            expected, source = cafe['id']+':'+article_id, 'cafe'
        else:
            expected, source = 'blog:'+article_id, 'blog'
        if row['post_id'] != expected or expected in identifiers:
            raise ValueError('게시글 ID·주소 불일치 또는 중복')
        identifiers.add(expected)
        if expected in seen:
            skipped += 1
            continue
        entry = common.item(expected, row['title'], row['body'], row['url'], source,
                            'historical', row['date_raw'])
        if not common.body_available(entry):
            raise ValueError('개인정보 마스킹 후 읽을 수 있는 본문 필요')
        entry['manual_observed_at'] = observed.isoformat()
        entry['manual_body_verified'] = True
        # Date-only, time-only and unknown publication times remain review-only.
        if not entry['published_at'] or not re.search(r'\d{1,2}:\d{2}', row['date_raw']):
            entry['collection_window_uncertain'] = True
        inputs.append(entry)
    if not inputs:
        raise ValueError('이미 처리된 게시글만 있어 새 검수 입력 없음')
    job = {'schema_version': 1, 'bot_id': BOT_ID, 'mode': 'verification', 'self_test': True,
           'job_id': job_id, 'status': 'pending', 'created_at': now().isoformat(),
           'expires_at': (now()+timedelta(minutes=120)).isoformat(),
           'input': {'policy_version': POLICY, 'policy_hash': common.policy_hash(),
                     'seen_hash': seen_hash, 'items': inputs,
                     'collection': {'coverage': [], 'historical': [], 'manual_supplement': True,
                                    'cookie_loaded': False, 'cookie_auth_status': 'unconfirmed',
                                    'cookie_auth_verified': False}},
           'receipts': {}, 'paid_ai_calls': 0}
    job['input_hash'] = digest(job['input'])
    return job, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    job, skipped = prepare(json.loads(Path(args.input).read_text(encoding='utf-8')), args.job_id)
    # Never overwrite history, input, a previous review or any existing file.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(job, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'items': len(job['input']['items']), 'already_seen_skipped': skipped,
                      'production_enabled': False, 'smtp_calls': 0, 'paid_ai_calls': 0,
                      'queue_writes': 0, 'seen_writes': 0}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError):
        raise SystemExit('수동 보완 준비 실패 — 입력·시각·본문 확인 여부·출력 경로를 확인하세요.') from None
