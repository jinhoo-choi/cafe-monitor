"""구독 운영: 기존 수집·수신자 유지, SMTP 영수증→이력 push→private ack."""
from notification_owner import OWNER_EMAIL, ERROR_EMAIL

import base64
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
import hashlib
import json
import os
import re
from pathlib import Path
import smtplib
import sys
import tempfile
import uuid

from negative_queue import BOT_ID, JOB_ID, POLICY, PREFIX, REPOSITORY, Queue, digest, now
import negative_verification as common

CONFIG_PATH = 'negative_subscription_config.json'
LOCKED = {'sending', 'uncertain', 'delivery_complete'}
TERMINAL = {'completed', 'verified', 'cancelled', 'expired'}


def settings(queue=None, live=False):
    raw = queue.source(CONFIG_PATH)[0] if live else Path(CONFIG_PATH).read_bytes()
    value = json.loads(raw)
    if (value.get('engine') not in {'api','subscription','paused'} or value.get('bot_id') != BOT_ID
            or value.get('policy_version') != POLICY or value.get('queue_repository') != REPOSITORY
            or value.get('queue_prefix') != PREFIX or value.get('paid_api_fallback') is not False
            or value.get('max_email_groups') != 30 or value.get('job_ttl_minutes') != 120):
        raise ValueError('구독 운영 설정 불일치')
    if live and (value['engine'] != 'subscription' or value.get('production_enabled') is not True):
        raise RuntimeError('구독 운영 중지 — 유료 API 전환 없음')
    if live and digest(json.loads(queue.source('negative_policy.json')[0])) != common.policy_hash():
        raise RuntimeError('현재 분석 정책 원문 변경 — 재분석 전 발송 보류')
    return value


def change(queue, job_id, edit):
    path = 'jobs/'+job_id+'.json'
    job, sha = queue.get(path)
    if not job:
        raise RuntimeError('구독 운영 회차 없음')
    edit(job)
    queue.put(path, job, sha)
    return job


def outstanding(queue):
    index, _ = queue.get('index.json')
    if index is None:
        return
    if index.get('bot_id') != BOT_ID or not isinstance(index.get('jobs'), list):
        raise ValueError('운영 큐 인덱스 오류')
    for job_id in index['jobs']:
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError('큐 회차 ID 오류')
        job, sha = queue.get('jobs/'+job_id+'.json')
        if not job:
            raise RuntimeError('큐 회차 파일 누락')
        if job.get('mode') != 'production' or job.get('status') in TERMINAL:
            continue
        if (job.get('status') == 'pending' and not job.get('receipts')
                and datetime.fromisoformat(job['expires_at']) <= now()):
            job.update(status='expired',expired_at=now().isoformat(),expiration_reason='unstarted_timeout')
            queue.put('jobs/'+job_id+'.json',job,sha)
            print('미발송 만료 회차 정리; API0/재발송0')
            continue
        raise RuntimeError('미종결 운영 회차 존재 — 새 수집·재발송 금지')


def collect(queue):
    value = settings()
    if value['engine'] == 'paused':
        print('부정여론 구독 일시정지; 쓰기0/발송0/API0')
        return
    if value['engine'] != 'subscription':
        raise RuntimeError('구독 수집은 API 경로를 호출하지 않음')
    if os.environ.get('DRY_RUN') == '1':
        print('DRY_RUN 구독 운영 쓰기0/발송0/API0')
        return
    queue.preflight()
    settings(queue,live=True)
    outstanding(queue)
    common.collect(queue,production=True)


def recipients(negative_count):
    sender = os.environ.get('GMAIL_USER','').strip()
    notify = [a.strip() for a in os.environ.get('NOTIFY_EMAIL','').split(',') if a.strip()]
    cc = [a.strip() for a in os.environ.get('EMAIL_CC','').split(',') if a.strip()]
    if not sender or not notify or not os.environ.get('GMAIL_APP_PW'):
        raise RuntimeError('기존 SMTP 수신자·인증 설정 누락')
    # SMTP 로그인 주소는 수신자가 아니다. 그룹스 회원에게 개인 사본을 중복 발송하지 않는다.
    targets = list(dict.fromkeys([OWNER_EMAIL]+notify+cc)) if negative_count else [ERROR_EMAIL]
    if any('\n' in a or '\r' in a or '@' not in a for a in targets):
        raise ValueError('SMTP 수신자 형식 오류')
    return sender, targets


def message(subject, body, sender, address):
    result = MIMEMultipart('alternative')
    result['Subject'] = subject
    result['From'] = formataddr((str(Header('부정여론봇','utf-8')),sender))
    result['To'] = address
    result['Date'] = formatdate(localtime=True)
    result['Message-ID'] = make_msgid(domain='gmail.com')
    result.attach(MIMEText(body,'html','utf-8'))
    return result.as_string()


def mark_uncertain(queue, job_id):
    try:
        def edit(job):
            if job.get('status') == 'sending':
                job.update(status='uncertain',uncertain_at=now().isoformat())
        change(queue,job_id,edit)
    except Exception as error:
        # sending 선기록이 남으면 이후 자동 재발송·만료 정리가 차단된다.
        return error


def failure_detail(error):
    text = str(error)
    for name in ('NEGATIVE_QUEUE_TOKEN', 'GMAIL_APP_PW', 'NAVER_COOKIES_JSON', 'CLAUDE_API_KEY'):
        secret = os.environ.get(name, '')
        if secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)(authorization|cookie|token|password|secret|api[_-]?key)\s*[:=][^\r\n]*', r'\1: [redacted]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+', '[redacted-address]', text)
    detail = {'error_type': type(error).__name__, 'message': text}
    if hasattr(error, 'status') and hasattr(error, 'method'):
        detail.update(http_status=error.status, http_method=error.method,
                      request_id=error.request_id)
    return detail


def record_consume_failure(queue, job_id, stage, error, cleanup_error=None):
    # This is evidence only: never change receipts, unlock, retry SMTP or ACK.
    try:
        job, sha = queue.get('jobs/'+job_id+'.json')
        evidence = {'schema_version': 1, 'job_id': job_id, 'operation': 'consume',
                    'at': now().isoformat(), 'stage': stage, 'error': failure_detail(error),
                    'job_blob_sha': sha, 'job_status': (job or {}).get('status'),
                    'receipts': (job or {}).get('receipts', {}), 'paid_ai_calls': 0}
        if cleanup_error:
            evidence['cleanup_error'] = failure_detail(cleanup_error)
        queue.put('failures/'+job_id+'/'+str(uuid.uuid4())+'.json', evidence)
    except Exception as audit_error:
        print('비공개 오류 기록 저장 실패: '+type(audit_error).__name__, file=sys.stderr)


def history_bytes(state):
    return (json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode()


def retained_history(state):
    cutoff = now()-timedelta(days=30)
    retained = {}
    for identifier,stamp in state.items():
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=common.KST)
        if parsed > cutoff:
            retained[identifier] = stamp
    return retained


def write_history(state):
    path = Path(os.environ.get('NEGATIVE_SEEN_PATH','seen_posts.json'))
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(history_bytes(state))
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)


def consume(queue, job_id):
    if not JOB_ID.fullmatch(job_id):
        raise ValueError('운영 회차 ID 오류')
    value = settings()
    if value['engine'] == 'paused':
        print('구독 일시정지; 쓰기0/발송0/API0')
        return False
    queue.preflight()
    settings(queue,live=True)
    job, _ = queue.get('jobs/'+job_id+'.json')
    if job and job.get('status') == 'completed':
        print('이미 종결된 운영 회차; 재발송0/API0')
        return False
    if not job or job.get('status') != 'pending' or job.get('receipts'):
        raise RuntimeError('전송 상태 잠금 — 자동 재발송 금지')
    if job.get('mode') != 'production' or job.get('self_test') is not False:
        raise ValueError('검수 회차 운영 발송 금지')
    result, _ = queue.get('results/'+job_id+'.json')
    if not result:
        raise RuntimeError('구독 분석 결과 없음')
    inputs, posts = common.validate(job,result,allow_production=True)
    selected, deferred = common.selected_groups(result)
    sender, targets = recipients(len(selected))
    body = common.render(job,result,production=True)
    executive_body = None
    if selected:
        from negative_mail import render as render_mail
        executive_body = render_mail(job,result,inputs,posts,selected,deferred,
                                     production=True,executive=True)
    state, state_hash = common.seen_snapshot()
    if state_hash != job['input']['seen_hash']:
        raise RuntimeError('분석 입력 이후 운영 이력 변경 — 발송 보류')
    processed = [p['post_id'] for p in posts.values() if p['verdict']=='non_negative']
    processed += [identifier for group in selected for identifier in group['member_post_ids']]
    if any(identifier in state for identifier in processed):
        raise RuntimeError('이미 처리된 입력 포함 — 발송 보류')
    stamp = now().astimezone(common.KST).isoformat()
    expected = {**retained_history(state),**{identifier:stamp for identifier in processed}}
    expected_hash = hashlib.sha256(history_bytes(expected)).hexdigest()
    # SMTP 로그인 실패 후 pending 재소비에서도 기존 파일의 SHA를 사용한다.
    for path, value in (('previews/'+job_id+'.json',{'html':body,'paid_ai_calls':0}),
            ('deferred/'+job_id+'.json',{'groups':deferred,'mode':'production',
             'member_post_ids':[p for g in deferred for p in g['member_post_ids']]}),
            ('reviews/'+job_id+'.json',{'post_ids':[p['post_id'] for p in posts.values() if p['verdict']=='needs_review'],
             'retry_next_collection':True})):
        queue.put(path,value,queue.get(path)[1])
    server = None
    stage = 'smtp_connect'
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20)
        stage = 'smtp_login'
        server.login(sender,os.environ['GMAIL_APP_PW'])
        stage = 'pre_send_settings'
        settings(queue,live=True)
        stage = 'pre_send_job_read'
        latest, sha = queue.get('jobs/'+job_id+'.json')
        if latest.get('status') != 'pending' or latest.get('receipts') or latest['input_hash'] != job['input_hash']:
            raise RuntimeError('발송 직전 회차 상태 변경')
        stage = 'pre_send_validation'
        common.validate(latest,result,allow_production=True)
        if common.seen_snapshot()[1] != state_hash:
            raise RuntimeError('발송 직전 운영 이력 변경')
        keys = [hashlib.sha256(a.encode()).hexdigest() for a in targets]
        latest.update(status='sending',sending_at=now().isoformat(),receipts={key:'planned' for key in keys},
                      recipient_fingerprint=digest(keys),processed_ids=processed,seen_before_hash=state_hash,
                      expected_seen_after_hash=expected_hash,selected_groups=len(selected),deferred_groups=len(deferred))
        stage = 'job_reserve'
        queue.put('jobs/'+job_id+'.json',latest,sha)
        subject = '[부정여론 탐지] '+now().astimezone(common.KST).strftime('%m월 %d일 %H시 기준')
        if not selected:
            subject += ' | 확인필요' if any(p['verdict']=='needs_review' for p in posts.values()) else ' | 탐지 없음'
        for address,key in zip(targets,keys):
            stage = 'recipient_settings'
            settings(queue,live=True)
            stage = 'receipt_sending'
            change(queue,job_id,lambda row:row['receipts'].update({key:'sending'}))
            try:
                stage = 'smtp_send'
                refused = server.sendmail(sender,[address],message(subject,body if not selected or address == OWNER_EMAIL else executive_body,sender,address))
            except smtplib.SMTPRecipientsRefused:
                change(queue,job_id,lambda row:row['receipts'].update({key:'rejected'}))
                raise RuntimeError('운영 수신자 SMTP 거부') from None
            if refused:
                change(queue,job_id,lambda row:row['receipts'].update({key:'rejected'}))
                raise RuntimeError('운영 수신자 SMTP 거부')
            stage = 'receipt_accepted'
            change(queue,job_id,lambda row:row['receipts'].update({key:'accepted'}))
        def delivered(row):
            if not row['receipts'] or any(v!='accepted' for v in row['receipts'].values()):
                raise RuntimeError('수신자 접수 미완료')
            row.update(status='delivery_complete',delivery_at=now().isoformat(),paid_ai_calls=0,
                       smtp_accepted=len(targets),self_test=False)
        stage = 'delivery_complete'
        change(queue,job_id,delivered)
    except Exception as error:
        cleanup_error = mark_uncertain(queue,job_id)
        record_consume_failure(queue,job_id,stage,error,cleanup_error)
        raise
    finally:
        if server:
            try: server.close()
            except Exception: pass
    if common.seen_snapshot()[1] != state_hash:
        raise RuntimeError('발송 후 이력 충돌 — 접수 회차 유지, 재발송 금지')
    write_history(expected)
    if common.seen_snapshot()[1] != expected_hash:
        raise RuntimeError('이력 저장 검증 실패 — 재발송 금지')
    print(json.dumps({'job_id':job_id,'status':'delivery_complete','smtp_accepted':len(targets),
                      'paid_ai_calls':0,'history_ack_pending':True}))
    return True


def ack(queue, job_id):
    if not JOB_ID.fullmatch(job_id):
        raise ValueError('ACK 회차 ID 오류')
    job, _ = queue.get('jobs/'+job_id+'.json')
    if job and job.get('status') == 'completed':
        return
    if (not job or job.get('mode') != 'production' or job.get('status') != 'delivery_complete'
            or not job.get('receipts') or any(v!='accepted' for v in job['receipts'].values())):
        raise RuntimeError('이력 ACK 대상 상태 오류')
    raw, blob = queue.source('seen_posts.json')
    if hashlib.sha256(raw).hexdigest() != job['expected_seen_after_hash']:
        raise RuntimeError('main 처리 이력 불일치 — completed 표시 금지')
    data = json.loads(raw)
    if not all(identifier in data for identifier in job['processed_ids']):
        raise RuntimeError('main 처리 ID 누락')
    def completed(row):
        if row.get('status') != 'delivery_complete' or any(v!='accepted' for v in row['receipts'].values()):
            raise RuntimeError('ACK 직전 상태 변경')
        row.update(status='completed',completed_at=now().isoformat(),history_ack=True,
                   history_blob_sha=blob,history_commit_sha=os.environ.get('NEGATIVE_ACK_COMMIT','unconfirmed'),
                   seen_after_hash=hashlib.sha256(raw).hexdigest(),paid_ai_calls=0)
    change(queue,job_id,completed)
    print(json.dumps({'job_id':job_id,'status':'completed','smtp_accepted':len(job['receipts']),
                      'paid_ai_calls':0,'history_ack':True}))


def request_arguments(value):
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('운영 요청은 main에서만 실행')
    if not isinstance(value,dict) or not set(value) <= {'operation','job_id','self_test','dry_run','request_id'}:
        raise ValueError('운영 요청에는 메타데이터만 허용')
    if value.get('operation') == 'noop':
        return None
    if value.get('operation') != 'consume' or value.get('self_test') is not False or value.get('dry_run') is not False:
        raise ValueError('운영 소비 요청 형식 오류')
    for field in ('job_id','request_id'):
        if not isinstance(value.get(field),str) or not JOB_ID.fullmatch(value[field]):
            raise ValueError('운영 요청 식별자 오류')
    return value['job_id']


def notify_error():
    sender = os.environ.get('GMAIL_USER','').strip()
    if not sender or not os.environ.get('GMAIL_APP_PW'):
        raise RuntimeError('오류 알림 SMTP 설정 없음')
    run = os.environ.get('GITHUB_RUN_ID','')
    url = 'https://github.com/jinhoo-choi/cafe-monitor/actions/runs/'+run
    body = '<p>부정여론봇 구독 경로를 보류했습니다. 유료 API로 전환하지 않았습니다.</p><p>수집·발송·이력 상태는 <a href="'+url+'">Actions 실행 기록</a>에서 확인해 주세요. 접수된 메일을 임의로 재발송하지 마세요.</p>'
    subject = '[부정여론봇] 구독 경로 오류'
    try:
        cookie_status = json.loads(Path('naver_cookie_status.json').read_text()).get('status')
    except (OSError, ValueError, AttributeError):
        cookie_status = None
    if cookie_status and cookie_status != 'healthy':
        subject = '[부정여론봇] 회원 카페 인증 확인 필요'
        body += '<p>클라우드에서 회원 전용 본문 인증을 확인하지 못해 수집을 보류했습니다. 부정 없음으로 판정한 결과가 아닙니다. 노트북을 켜서 쿠키 점검·갱신이 필요합니다. 국내 쿠키가 정상이어도 클라우드 접속 환경의 인증 제한은 별도로 확인해야 합니다.</p>'
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=20) as server:
        server.login(sender,os.environ['GMAIL_APP_PW'])
        refused = server.sendmail(sender,[ERROR_EMAIL],message(subject,body,sender,ERROR_EMAIL))
    if refused:
        raise RuntimeError('본인 오류 알림 거부')
    print('본인 오류 알림 SMTP accepted 1건; API0')


def main():
    operation = sys.argv[1] if len(sys.argv)>1 else 'request'
    if operation == 'notify-error':
        notify_error()
        return
    if operation == 'collect':
        if settings()['engine'] == 'paused':
            print('구독 일시정지; 쓰기0/발송0/API0')
            return
        collect(Queue())
    elif operation == 'ack':
        ack(Queue(),sys.argv[2])
    elif operation == 'request':
        job_id = request_arguments(json.loads(Path('negative_subscription_request.json').read_text()))
        if job_id is None:
            print('운영 소비 요청 없음; 발송0/API0')
            return
        if settings()['engine'] == 'paused':
            print('구독 일시정지; 쓰기0/발송0/API0')
            return
        if consume(Queue(),job_id) and os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as output:
                output.write('ack_needed=true\njob_id='+job_id+'\n')
    else:
        raise ValueError('지원하지 않는 운영 동작')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # 스키마 오류에 포함될 수 있는 비공개 원문·분석을 공개 로그에 출력하지 않는다.
        print('부정여론 구독 경로 보류: '+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)


