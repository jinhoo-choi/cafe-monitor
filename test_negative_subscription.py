"""운영 수신자·전송 잠금·만료·이력 ACK를 실제 SMTP/API 없이 검증한다."""
import copy
from contextlib import ExitStack, redirect_stdout
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from negative_queue import digest, now
import negative_subscription as bot
import negative_verification as common
from test_negative_verification import fixture, MemoryQueue


class ProductionRegression(unittest.TestCase):
    def test_smtp_sender_is_not_implicitly_copied_and_owner_gets_diagnostics(self):
        from email import message_from_string
        self.job['input']['collection']={'coverage':[{'status':'cookie_required'}]}
        self.rehash()
        self.reload_queue()
        with patch.dict(os.environ,{'GMAIL_USER':'sender@example.invalid',
                                   'NOTIFY_EMAIL':'exec@example.invalid','EMAIL_CC':''}):
            bot.consume(self.queue,self.job['job_id'])
        self.assertEqual([call.args[1] for call in self.server.sendmail.call_args_list],
                         [[bot.OWNER_EMAIL],['exec@example.invalid']])
        self.server.login.assert_called_with('sender@example.invalid','offline')
        for call in self.server.sendmail.call_args_list:
            self.assertEqual(call.args[0],'sender@example.invalid')
            mail=message_from_string(call.args[2])
            self.assertEqual(mail['To'],call.args[1][0])
            body=mail.get_payload(0).get_payload(decode=True).decode('utf-8')
            self.assertEqual('NAVER_COOKIES_JSON' in body,call.args[1]==[bot.OWNER_EMAIL])

    def test_group_report_does_not_copy_gmail_sender_and_ack_still_completes(self):
        group='report@example.googlegroups.com'
        with patch.dict(os.environ,{'GMAIL_USER':bot.ERROR_EMAIL,
                                   'NOTIFY_EMAIL':group,'EMAIL_CC':group}):
            bot.consume(self.queue,self.job['job_id'])
        self.assertEqual([call.args[1] for call in self.server.sendmail.call_args_list],
                         [[bot.OWNER_EMAIL],[group]])
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['smtp_accepted'],2)
        self.assertEqual(list(state['receipts'].values()),['accepted']*2)
        self.remote_history=self.path.read_bytes()
        bot.ack(self.queue,self.job['job_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'completed')

    def test_explicit_sender_recipient_is_preserved(self):
        with patch.dict(os.environ,{'GMAIL_USER':bot.ERROR_EMAIL,
                                   'NOTIFY_EMAIL':bot.ERROR_EMAIL,'EMAIL_CC':''}):
            self.assertEqual(bot.recipients(1),
                             (bot.ERROR_EMAIL,[bot.OWNER_EMAIL,bot.ERROR_EMAIL]))

    def test_status_only_never_goes_to_distinct_sender_or_executives(self):
        with patch.dict(os.environ,{'GMAIL_USER':'sender@example.invalid'}):
            self.assertEqual(bot.recipients(0),('sender@example.invalid',[bot.ERROR_EMAIL]))

    def test_error_cookie_notices_and_verification_use_only_gmail(self):
        import refresh_cookies
        from email import message_from_string
        with patch.dict(os.environ,{'GMAIL_USER':'sender@example.invalid',
                                   'NOTIFY_EMAIL':bot.ERROR_EMAIL,'EMAIL_CC':'','TEST_MODE':'1'}):
            self.server.__enter__.return_value=self.server
            bot.notify_error()
            queue=MagicMock()
            queue.get.return_value=(None,None)
            self.assertEqual(refresh_cookies.owner_notice(queue,'cookie_required'),'accepted')
            common.send_owner('owner diagnostic','diagnostic body',common.owner())
        self.assertEqual(self.server.sendmail.call_count,3)
        for call, recipient in zip(self.server.sendmail.call_args_list,
                                   [bot.ERROR_EMAIL, bot.ERROR_EMAIL, bot.ERROR_EMAIL]):
            self.assertEqual(call.args[:2],('sender@example.invalid',[recipient]))
            mail=message_from_string(call.args[2])
            self.assertEqual(mail['To'],recipient)
            self.assertIsNone(mail['Cc'])
            self.assertIsNone(mail['Bcc'])

    def test_cookie_failure_notice_never_claims_no_negative_and_preserves_recipient(self):
        self.server.__enter__.return_value=self.server
        from email import message_from_string
        from email.header import decode_header
        with patch.dict(os.environ,{'GMAIL_USER':'sender@example.invalid'}), patch.object(bot.Path,'read_text',return_value=json.dumps({'status':'invalid','untrusted':'PRIVATE-NOT-TO-LOG'})):
            bot.notify_error()
        call=self.server.sendmail.call_args
        self.assertEqual(call.args[:2],('sender@example.invalid',[bot.ERROR_EMAIL]))
        mail=message_from_string(call.args[2])
        subject=''.join(v.decode(enc or 'utf-8') if isinstance(v,bytes) else v for v,enc in decode_header(mail['Subject']))
        self.assertIn('인증 확인 필요',subject)
        self.assertNotIn('탐지 없음',subject)
        body=mail.get_payload(0).get_payload(decode=True).decode('utf-8')
        self.assertIn('노트북을 켜서',body)
        self.assertIn('부정 없음으로 판정한 결과가 아닙니다',body)
        self.assertNotIn('PRIVATE-NOT-TO-LOG',body)

    def test_legacy_operational_statuses_use_only_gmail(self):
        import ast, html
        from datetime import datetime
        from email import message_from_string
        names={'send_status_email','_notify_send_failure'}
        nodes=[node for node in ast.parse(Path('monitor.py').read_text()).body
               if isinstance(node,ast.FunctionDef) and node.name in names]
        namespace=dict(vars(bot),DRY_RUN=False,datetime=datetime,KST=common.KST,html=html,
                       GMAIL_USER='sender@example.invalid',GMAIL_APP_PW='offline',
                       log=lambda *args:None,_from_header=lambda:'sender@example.invalid',
                       _addr_header=lambda value:value)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'legacy-status','exec'),namespace)
        self.server.__enter__.return_value=self.server
        namespace['send_status_email']('error','offline diagnostic')
        namespace['_notify_send_failure']('offline test',{'exec@example.invalid':(550,b'refused')})
        namespace['send_status_email']('warning','offline cookie warning')
        namespace['send_status_email']('no_result')
        self.assertEqual(self.server.sendmail.call_count,4)
        for call, recipient in zip(self.server.sendmail.call_args_list,
                                   [bot.ERROR_EMAIL] * 4):
            self.assertEqual(call.args[:2],('sender@example.invalid',[recipient]))
            mail=message_from_string(call.args[2])
            self.assertEqual(mail['To'],recipient)
            self.assertIsNone(mail['Cc'])
            self.assertIsNone(mail['Bcc'])

    def test_mixed_diagnostics_only_reach_owner_envelope(self):
        from email import message_from_string
        self.job,self.result=fixture(2)
        self.job.update(mode='production',self_test=False)
        for entry in self.job['input']['items']:entry['dataset']='live'
        self.job['input']['seen_hash']=common.seen_snapshot()[1]
        self.job['input']['collection']={'coverage':[{'status':'cookie_required'}],
                                       'cookie_auth_status':'unverified','cookie_auth_verified':False}
        self.job['input']['items'][1].update(body_status='missing',body_excerpt='')
        self.result['posts'][1].update(verdict='needs_review',score=None,evidence_quotes=[])
        self.result['groups']=self.result['groups'][:1]
        self.rehash()
        self.reload_queue()
        self.assertTrue(bot.consume(self.queue,self.job['job_id']))
        for call in self.server.sendmail.call_args_list:
            mail=message_from_string(call.args[2])
            body=mail.get_payload(0).get_payload(decode=True).decode('utf-8')
            self.assertIn('주문이 계속 실패',body)
            self.assertIn('224000000001',body)
            self.assertNotIn('확인필요',mail['Subject'])
            for diagnostic in ('NAVER_COOKIES_JSON','본문 미수집','회원 전용 본문 인증 미확인','224000000002'):
                if call.args[1]==['111715@koreainvestment.com']:
                    self.assertIn(diagnostic,body)
                else:
                    self.assertNotIn(diagnostic,body)
            self.assertIsNone(mail['Cc'])
            self.assertIsNone(mail['Bcc'])

    def test_executive_renderer_rejects_status_only_and_omits_deferred_notice(self):
        import negative_mail
        inputs,posts=common.validate(self.job,self.result,allow_production=True)
        selected,_=common.selected_groups(self.result)
        body=negative_mail.render(self.job,self.result,inputs,posts,selected,[{}],production=True,executive=True)
        self.assertNotIn('이월 이슈',body)
        with self.assertRaises(ValueError):
            negative_mail.render(self.job,self.result,inputs,posts,[],[],production=True,executive=True)

    def test_legacy_batch_isolates_review_panel_by_envelope(self):
        import ast, html
        from datetime import datetime
        from email import message_from_string
        import negative_mail
        node=next(n for n in ast.parse(Path('monitor.py').read_text()).body
                  if isinstance(n,ast.FunctionDef) and n.name=='send_alert_batch')
        namespace=dict(vars(bot),DRY_RUN=False,API_STATS={},datetime=datetime,KST=common.KST,
                       html=html,build_card=negative_mail.build_card,GMAIL_USER='sender@example.invalid',
                       GMAIL_APP_PW='offline',RECIPIENTS=['ops@example.invalid'],CC_RECIPIENTS=[],
                       log=lambda *args:None,_from_header=lambda:'sender@example.invalid',
                       _addr_header=lambda value:value,_warn_refused=lambda *args:None)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'legacy-batch','exec'),namespace)
        post={'title':'한국투자증권 주문 오류','url':'https://example.invalid/finding',
              'matched_kw':'한국투자증권','score':3,'summary':'주문 오류 신고','reply':'주문 내역 확인',
              'source':'blog','cafe_name':'블로그'}
        unresolved={'title':'검토 대상','url':'https://example.invalid/review'}
        namespace['send_alert_batch']([post],2,2,[unresolved])
        self.assertEqual([call.args[1] for call in self.server.sendmail.call_args_list],
                         [[bot.OWNER_EMAIL],['ops@example.invalid']])
        for call in self.server.sendmail.call_args_list:
            mail=message_from_string(call.args[2])
            body=mail.get_payload(0).get_payload(decode=True).decode('utf-8')
            self.assertIn('주문 오류 신고',body)
            if call.args[1]==['111715@koreainvestment.com']:
                self.assertIn('본문 미수집',body)
                self.assertIn('https://example.invalid/review',body)
            else:
                self.assertNotIn('본문 미수집',body)
                self.assertNotIn('https://example.invalid/review',body)

        self.server.sendmail.reset_mock()
        namespace['send_alert_batch']([],1,1,[unresolved])
        self.server.sendmail.assert_called_once()
        call=self.server.sendmail.call_args
        self.assertEqual(call.args[1],[bot.ERROR_EMAIL])
        mail=message_from_string(call.args[2])
        self.assertEqual(mail['To'],bot.ERROR_EMAIL)
        self.assertIsNone(mail['Cc'])
        self.assertIsNone(mail['Bcc'])
        body=mail.get_payload(0).get_payload(decode=True).decode('utf-8')
        self.assertIn('https://example.invalid/review',body)

    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path=Path(self.tmp)/'seen.json'
        self.path.write_text('{}\n')
        self.stack.enter_context(patch.dict(os.environ,{'NEGATIVE_SEEN_PATH':str(self.path),
            'GMAIL_USER':'111715@koreainvestment.com','GMAIL_APP_PW':'offline',
            'NOTIFY_EMAIL':'ops@example.invalid,111715@koreainvestment.com',
            'EMAIL_CC':'cc@example.invalid,ops@example.invalid','GITHUB_REF':'refs/heads/main'}))
        self.job,self.result=fixture()
        self.job.update(mode='production',self_test=False)
        self.job['input']['items'][0]['dataset']='live'
        self.job['input']['seen_hash']=common.seen_snapshot()[1]
        self.rehash()
        self.queue=MemoryQueue(self.job,self.result)
        self.remote_history=self.path.read_bytes()
        self.queue.source=lambda path:(Path(bot.CONFIG_PATH).read_bytes(),'config') if path==bot.CONFIG_PATH else ((Path('negative_policy.json').read_bytes(),'policy') if path=='negative_policy.json' else (self.remote_history,'history-blob'))
        self.server=MagicMock()
        self.server.sendmail.return_value={}
        self.smtp=self.stack.enter_context(patch.object(bot.smtplib,'SMTP_SSL',return_value=self.server))

    def rehash(self):
        self.job['input_hash']=digest(self.job['input'])
        self.result['input_hash']=self.job['input_hash']

    def reload_queue(self):
        self.queue.files[self.queue.path]=copy.deepcopy(self.job)
        self.queue.files['results/'+self.job['job_id']+'.json']=copy.deepcopy(self.result)

    def test_live_policy_change_blocks_mail_and_keeps_pending_history(self):
        original = self.queue.source
        self.queue.source = lambda path: (b'{"policy_version":"negative-subscription-v1","changed":true}', 'changed') if path == 'negative_policy.json' else original(path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, '정책 원문 변경'):
            bot.consume(self.queue, self.job['job_id'])
        self.server.sendmail.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'], 'pending')

    def test_recipient_receipts_history_push_and_ack_required_for_completed(self):
        self.assertTrue(bot.consume(self.queue,self.job['job_id']))
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'delivery_complete')
        self.assertEqual(list(state['receipts'].values()),['accepted']*3)
        self.assertEqual(self.server.sendmail.call_count,3)
        self.assertEqual([c.args[1] for c in self.server.sendmail.call_args_list],
                         [['111715@koreainvestment.com'],['ops@example.invalid'],['cc@example.invalid']])
        self.assertIn(self.job['input']['items'][0]['post_id'],json.loads(self.path.read_text()))
        with self.assertRaisesRegex(RuntimeError,'이력 불일치'):bot.ack(self.queue,self.job['job_id'])
        self.remote_history=self.path.read_bytes()
        bot.ack(self.queue,self.job['job_id'])
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'completed')
        self.assertTrue(state['history_ack'])
        self.assertEqual(state['paid_ai_calls'],0)
        self.assertFalse(bot.consume(self.queue,self.job['job_id']))
        self.assertEqual(self.server.sendmail.call_count,3)

    def test_smtp_login_failure_is_pending_and_no_history_change(self):
        self.server.login.side_effect=smtplib.SMTPAuthenticationError(535,b'no')
        with self.assertRaises(smtplib.SMTPAuthenticationError):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'pending')
        self.assertEqual(self.queue.get(self.queue.path)[0]['receipts'],{})
        self.assertEqual(self.path.read_bytes(),self.remote_history)
        self.server.sendmail.assert_not_called()

    def test_existing_thirty_day_history_retention_is_preserved(self):
        old={'old':(now()-timedelta(days=31)).isoformat(),'recent':now().isoformat()}
        self.path.write_bytes(bot.history_bytes(old))
        self.remote_history=self.path.read_bytes()
        self.job['input']['seen_hash']=common.seen_snapshot()[1]
        self.rehash();self.reload_queue()
        bot.consume(self.queue,self.job['job_id'])
        saved=json.loads(self.path.read_text())
        self.assertNotIn('old',saved)
        self.assertEqual(saved['recent'],old['recent'])
        self.assertIn(self.result['posts'][0]['post_id'],saved)

    def test_history_write_failure_after_acceptance_never_resends(self):
        with patch.object(bot,'write_history',side_effect=OSError('disk')):
            with self.assertRaises(OSError):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'delivery_complete')
        self.assertEqual(list(self.queue.get(self.queue.path)[0]['receipts'].values()),['accepted']*3)
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.server.sendmail.call_count,3)
        with self.assertRaisesRegex(RuntimeError,'이력 불일치'):bot.ack(self.queue,self.job['job_id'])

    def test_disconnect_after_data_is_uncertain_and_cannot_retry(self):
        self.server.sendmail.side_effect=ConnectionResetError('unknown after DATA')
        with self.assertRaises(ConnectionResetError):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'uncertain')
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.path.read_bytes(),self.remote_history)

    def test_partial_rejection_preserves_accepted_without_completing_or_resending(self):
        self.server.sendmail.side_effect=[{},smtplib.SMTPRecipientsRefused({'ops@example.invalid':(550,b'no')})]
        with self.assertRaisesRegex(RuntimeError,'SMTP 거부'):bot.consume(self.queue,self.job['job_id'])
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'uncertain')
        self.assertEqual(list(state['receipts'].values()),['accepted','rejected','planned'])
        self.assertEqual(self.path.read_bytes(),self.remote_history)
        with self.assertRaises(RuntimeError):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.server.sendmail.call_count,2)

    def test_no_negative_sends_only_gmail_and_holds_missing_body(self):
        self.job['input']['items'][0]['body_status']='missing'
        self.result['posts'][0].update(verdict='needs_review',score=None,category='unresolved',urgency='watch',evidence_quotes=[])
        self.result['groups']=[]
        self.rehash();self.reload_queue()
        bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.server.sendmail.call_args.args[1],[bot.ERROR_EMAIL])
        self.assertEqual(json.loads(self.path.read_text()),{})
        self.assertEqual(self.queue.get(self.queue.path)[0]['processed_ids'],[])
        self.assertTrue(self.queue.get('reviews/'+self.job['job_id']+'.json')[0]['retry_next_collection'])

    def test_non_negative_is_saved_after_gmail_status_mail(self):
        self.result['posts'][0].update(verdict='non_negative',score=0,urgency='watch',category='neutral_other')
        self.result['groups']=[]
        self.reload_queue()
        bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.server.sendmail.call_args.args[1],[bot.ERROR_EMAIL])
        self.assertIn(self.result['posts'][0]['post_id'],json.loads(self.path.read_text()))

    def test_verified_or_historical_inputs_never_use_operating_recipients(self):
        for kind in ('verification','historical'):
            with self.subTest(kind=kind):
                if kind=='verification':self.job.update(mode='verification',self_test=True)
                else:
                    self.job.update(mode='production',self_test=False)
                    self.job['input']['items'][0]['dataset']='historical'
                self.rehash();self.reload_queue()
                with self.assertRaises(ValueError):bot.consume(self.queue,self.job['job_id'])
        self.smtp.assert_not_called()

    def test_changed_seen_refuses_mail(self):
        self.path.write_text('{"already":"2026-10-14T00:00:00+00:00"}')
        with self.assertRaisesRegex(RuntimeError,'이력 변경'):bot.consume(self.queue,self.job['job_id'])
        self.smtp.assert_not_called()

    def test_locked_or_expired_job_cannot_send(self):
        for state in ('sending','uncertain','delivery_complete','expired'):
            self.queue.files[self.queue.path]['status']=state
            with self.subTest(state=state),self.assertRaises(RuntimeError):bot.consume(self.queue,self.job['job_id'])
        self.smtp.assert_not_called()

    def test_unstarted_expired_job_cleans_but_any_receipt_stays_locked(self):
        self.queue.files['index.json']={'bot_id':'negative_monitor','jobs':[self.job['job_id']]}
        self.queue.revisions['index.json']='1'
        self.queue.files[self.queue.path]['expires_at']=(now()-timedelta(seconds=1)).isoformat()
        bot.outstanding(self.queue)
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'expired')
        for status in ('pending','sending','uncertain','delivery_complete'):
            self.queue.files[self.queue.path].update(status=status,receipts={'hashed':'sending'})
            with self.subTest(status=status),self.assertRaisesRegex(RuntimeError,'미종결'):bot.outstanding(self.queue)
        self.smtp.assert_not_called()

    def test_paused_mode_does_not_read_queue_or_send(self):
        paused={**bot.settings(),'engine':'paused'}
        with patch.object(bot,'settings',return_value=paused):
            self.assertFalse(bot.consume(self.queue,self.job['job_id']))
            bot.collect(self.queue)
        self.smtp.assert_not_called()

    def test_pause_between_login_and_state_record_preserves_pending(self):
        active=bot.settings()
        with patch.object(bot,'settings',side_effect=[active,active,RuntimeError('구독 운영 중지')]):
            with self.assertRaisesRegex(RuntimeError,'구독 운영 중지'):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'pending')
        self.server.sendmail.assert_not_called()

    def test_deferred_groups_never_enter_sent_history(self):
        job,result=fixture(35)
        job.update(mode='production',self_test=False)
        for source in job['input']['items']:source['dataset']='live'
        job['input']['seen_hash']=common.seen_snapshot()[1]
        job['input_hash']=digest(job['input']);result['input_hash']=job['input_hash']
        queue=MemoryQueue(job,result);queue.source=self.queue.source
        bot.consume(queue,job['job_id'])
        saved=json.loads(self.path.read_text())
        self.assertEqual(len(saved),30)
        held=queue.get('deferred/'+job['job_id']+'.json')[0]['member_post_ids']
        self.assertEqual(len(held),5)
        self.assertFalse(set(held)&set(saved))

    def test_public_request_requires_main_and_exact_metadata(self):
        valid={'operation':'consume','job_id':'negative-production-123','request_id':'run-123','self_test':False,'dry_run':False}
        self.assertEqual(bot.request_arguments(valid),valid['job_id'])
        for edit in ({'self_test':True},{'dry_run':True},{'job_id':'$(whoami)'},{'body':'private source'}):
            with self.subTest(edit=edit),self.assertRaises(ValueError):bot.request_arguments({**valid,**edit})
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/test'}),self.assertRaises(ValueError):bot.request_arguments(valid)

    def test_original_mail_layout_replaces_compact_review_layout(self):
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('AI 요약',rendered)
        self.assertIn('AI 추천 대응',rendered)
        self.assertIn('background:#c62828',rendered)
        self.assertIn('부정 강도 :',rendered)
        self.assertNotIn('내부 확인·대응',rendered)
        self.assertNotIn('부정여론 모니터링 결과',rendered)
        self.assertNotIn('비부정·보류 판정 근거',rendered)
        self.assertNotIn('unconfirmed',rendered)
        self.assertNotIn('[TEST]',rendered)
        self.smtp.assert_not_called()

    def test_pending_after_login_failure_can_be_consumed_again(self):
        self.server.login.side_effect=smtplib.SMTPAuthenticationError(535,b'no')
        with self.assertRaises(smtplib.SMTPAuthenticationError):bot.consume(self.queue,self.job['job_id'])
        self.server.login.side_effect=None
        self.assertTrue(bot.consume(self.queue,self.job['job_id']))
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'delivery_complete')

    def test_group_member_links_and_cookie_warning_in_mail(self):
        self.job,self.result=fixture(2)
        self.job.update(mode='production',self_test=False)
        for entry in self.job['input']['items']:entry['dataset']='live'
        self.job['input']['collection']={'coverage':[{'status':'cookie_required'}]}
        self.result['groups']=[{'group_id':'g','issue_key':'k','member_post_ids':['blog:offline0','blog:offline1'],
                                'representative_post_id':'blog:offline0','group_reason':'같은 주문 실패'}]
        self.rehash()
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('유사 이슈',rendered)
        self.assertIn('224000000002',rendered)
        self.assertIn('NAVER_COOKIES_JSON 재등록 필요',rendered)
        self.assertLess(rendered.index('NAVER_COOKIES_JSON'),rendered.index('AI 요약'))
        self.job['input']['collection']={'coverage':[], 'cookie_expiring':['NID_AUT']}
        self.rehash()
        self.assertIn('7일 내 만료 예정(NID_AUT)',common.render(self.job,self.result,production=True))
        self.job['input']['collection']={'coverage':[]}
        self.rehash()
        self.assertNotIn('재등록',common.render(self.job,self.result,production=True))

    def test_paid_model_paths_require_explicit_api_setting(self):
        module=common.collector()
        with self.assertRaisesRegex(RuntimeError,'유료 API'):module._require_paid_engine()
        self.assertEqual(module.API_STATS['calls'],0)

    def test_original_card_is_reused_without_a_paid_client(self):
        import ast,html
        import negative_mail
        source=Path('monitor.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='build_card')
        namespace={'html':html,'KST':common.KST}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'original-card','exec'),namespace)
        value={'title':'한국투자증권 주문 문의','url':'https://m.blog.naver.com/offline/224000000001',
               'matched_kw':'한국투자증권','score':2,'summary':'원문 요약','reply':'검토용 답변','source':'blog'}
        self.assertEqual(namespace['build_card'](value,1,1),negative_mail.build_card(value,1,1))
        self.smtp.assert_not_called()

    def test_owner_preview_uses_restored_layout_without_becoming_production(self):
        self.job.update(mode='verification',self_test=True,production_preview=True)
        rendered=common.render(self.job,self.result)
        self.assertIn('background:#c62828',rendered)
        self.assertIn('AI 추천 대응',rendered)
        self.assertIn('[TEST] 본인 전용',rendered)
        self.assertEqual(self.job['mode'],'verification')
        self.smtp.assert_not_called()

    def test_no_negative_status_keeps_original_green_layout(self):
        self.result['posts'][0].update(verdict='non_negative',score=0)
        self.result['groups']=[]
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('background:#2e7d32',rendered)
        self.assertIn('탐지 없음 - 정상 실행',rendered)
        self.assertNotIn('AI 추천 대응',rendered)
        self.job['input']['collection']={'coverage':[{'status':'error','listed':0}]}
        self.rehash()
        degraded=common.render(self.job,self.result,production=True)
        self.assertIn('수집 상태 확인 필요',degraded)
        self.assertNotIn('탐지 없음 - 정상 실행',degraded)

    def test_review_only_keeps_original_review_panel(self):
        self.job['input']['items'][0].update(body_status='missing',body_excerpt='')
        self.result['posts'][0].update(verdict='needs_review',score=None,evidence_quotes=[])
        self.result['groups']=[]
        self.rehash()
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('확인필요',rendered)
        self.assertIn('본문 미수집 1건',rendered)
        self.assertIn('224000000001',rendered)
        self.assertNotIn('탐지 없음 - 정상 실행',rendered)

    def test_original_layout_escapes_untrusted_title_and_date(self):
        self.job['input']['items'][0].update(title='<script>한국투자증권</script>',date_raw='<img src=x>')
        self.rehash()
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('&lt;script&gt;',rendered)
        self.assertIn('&lt;img src=x&gt;',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertNotIn('<img src=x>',rendered)

    def test_restored_body_is_sent_as_html_mime(self):
        from email import message_from_string
        rendered=common.render(self.job,self.result,production=True)
        mail=message_from_string(bot.message('test',rendered,'111715@koreainvestment.com','ops@example.invalid'))
        part=next(p for p in mail.walk() if p.get_content_type()=='text/html')
        self.assertEqual(part.get_payload(decode=True).decode('utf-8'),rendered)
        self.assertIn('background:#c62828',part.get_payload(decode=True).decode('utf-8'))
        self.smtp.assert_not_called()



class QueueWriteConflictRegression(unittest.TestCase):
    def queue(self):
        from negative_queue import Queue
        return Queue.__new__(Queue)

    def test_unrelated_branch_conflict_retries_only_unchanged_target(self):
        from negative_queue import QueueHTTPError
        q=self.queue()
        q.get=MagicMock(return_value=({'receipts':{'r':'planned'}},'old-blob'))
        q.request=MagicMock(side_effect=[QueueHTTPError('PUT',409,'REQ:1'),{'content':{'sha':'new-blob'}}])
        value={'receipts':{'r':'sending'}}
        q.put('jobs/job.json',value,'old-blob')
        self.assertEqual(q.request.call_count,2)
        self.assertEqual(q.request.call_args_list[0],q.request.call_args_list[1])
        q.get.assert_called_once_with('jobs/job.json')

    def test_changed_target_even_identical_value_never_retries(self):
        from negative_queue import QueueHTTPError
        q=self.queue();value={'receipts':{'r':'sending'}}
        q.get=MagicMock(return_value=(value,'other-blob'))
        q.request=MagicMock(side_effect=QueueHTTPError('PUT',409,'REQ:2'))
        with self.assertRaises(QueueHTTPError):q.put('jobs/job.json',value,'old-blob')
        self.assertEqual(q.request.call_count,1)

    def test_second_conflict_and_nonconflict_errors_are_not_retried(self):
        from negative_queue import QueueHTTPError
        for status in [409,422,403]:
            q=self.queue();q.get=MagicMock(return_value=({},'old-blob'))
            q.request=MagicMock(side_effect=QueueHTTPError('PUT',status,'REQ:3'))
            with self.assertRaises(QueueHTTPError):q.put('jobs/job.json',{},'old-blob')
            self.assertEqual(q.request.call_count,2 if status==409 else 1)
        q=self.queue();q.request=MagicMock(side_effect=RuntimeError('network unknown'))
        with self.assertRaises(RuntimeError):q.put('jobs/job.json',{},'old-blob')
        self.assertEqual(q.request.call_count,1)

    def test_conflicting_create_requires_path_still_absent(self):
        from negative_queue import QueueHTTPError
        for current,sha,retries in [(None,None,2),({},'existing',1)]:
            q=self.queue();q.get=MagicMock(return_value=(current,sha))
            q.request=MagicMock(side_effect=[QueueHTTPError('PUT',409,'REQ:4'),{}])
            if retries==2:q.put('checks/new.json',{})
            else:
                with self.assertRaises(QueueHTTPError):q.put('checks/new.json',{})
            self.assertEqual(q.request.call_count,retries)

    def test_http_error_preserves_only_status_method_and_request_id(self):
        import urllib.error
        from negative_queue import QueueHTTPError
        q=self.queue();q.token='private-test-token'
        error=urllib.error.HTTPError('https://api.github.com/private',409,'conflict',
                                    {'X-GitHub-Request-Id':'ABCD:1234'},None)
        with patch('negative_queue.urllib.request.urlopen',side_effect=error):
            with self.assertRaises(QueueHTTPError) as caught:q.request('/contents/test','PUT',{})
        self.assertEqual((caught.exception.method,caught.exception.status,caught.exception.request_id),
                         ('PUT',409,'ABCD:1234'))
        self.assertNotIn('private-test-token',str(caught.exception))


class ConsumeFailureEvidenceRegression(unittest.TestCase):
    # Reuse the existing all-mocked SMTP/private-queue fixture only.
    setUp = ProductionRegression.setUp
    rehash = ProductionRegression.rehash
    reload_queue = ProductionRegression.reload_queue
    def test_receipt_write_failure_keeps_planned_and_private_detail(self):
        from negative_queue import QueueHTTPError
        original=self.queue.put
        def put(path,value,sha=None):
            if path==self.queue.path and 'sending' in value.get('receipts',{}).values():
                raise QueueHTTPError('PUT',409,'REQ:RECEIPT')
            return original(path,value,sha)
        self.queue.put=put
        before=self.path.read_bytes()
        with self.assertRaises(QueueHTTPError):bot.consume(self.queue,self.job['job_id'])
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'uncertain')
        self.assertEqual(list(state['receipts'].values()),['planned']*3)
        self.server.sendmail.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)
        failures=[v for p,v in self.queue.files.items() if p.startswith('failures/')]
        self.assertEqual(len(failures),1)
        event=failures[0]
        self.assertEqual(event['stage'],'receipt_sending')
        self.assertEqual(event['error']['http_status'],409)
        self.assertEqual(event['error']['request_id'],'REQ:RECEIPT')
        self.assertEqual(list(event['receipts'].values()),['planned']*3)
        self.assertNotIn('input',event)
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_not_called()

    def test_smtp_uncertainty_is_not_retried_and_sensitive_error_is_redacted(self):
        self.server.sendmail.side_effect=ConnectionResetError(
            'unknown after DATA secret-value exec@example.invalid Authorization: Bearer hidden')
        with patch.dict(os.environ,{'NEGATIVE_QUEUE_TOKEN':'secret-value'}):
            with self.assertRaises(ConnectionResetError):bot.consume(self.queue,self.job['job_id'])
        failures=[v for p,v in self.queue.files.items() if p.startswith('failures/')]
        text=json.dumps(failures)
        for forbidden in ['secret-value','exec@example.invalid','hidden']:
            self.assertNotIn(forbidden,text)
        self.assertEqual(failures[0]['stage'],'smtp_send')
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'uncertain')
        self.server.sendmail.assert_called_once()

    def test_failure_record_error_preserves_primary_and_lock(self):
        original=self.queue.put
        def put(path,value,sha=None):
            if path.startswith('failures/'):raise RuntimeError('audit unavailable')
            return original(path,value,sha)
        self.queue.put=put;self.server.sendmail.side_effect=ConnectionResetError('after DATA')
        from contextlib import redirect_stderr
        output=StringIO()
        with redirect_stderr(output),self.assertRaises(ConnectionResetError):
            bot.consume(self.queue,self.job['job_id'])
        self.assertIn('비공개 오류 기록 저장 실패',output.getvalue())
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'uncertain')
        self.server.sendmail.assert_called_once()


    def test_unrelated_commit_before_receipt_write_recovers_without_smtp_retry(self):
        import base64,urllib.parse
        from negative_queue import Queue,QueueHTTPError,PREFIX
        original=self.queue.put;adapter=Queue.__new__(Queue);adapter.get=self.queue.get
        conflicts=[]
        def request(suffix,method,data):
            path=urllib.parse.unquote(suffix.split('/contents/'+PREFIX+'/')[1])
            value=json.loads(base64.b64decode(data['content']))
            if path==self.queue.path and 'sending' in value.get('receipts',{}).values() and not conflicts:
                conflicts.append(path);raise QueueHTTPError('PUT',409,'REQ:BRANCH')
            original(path,value,data.get('sha'));return {}
        adapter.request=request
        self.queue.put=lambda path,value,sha=None:Queue.put(adapter,path,value,sha)
        self.assertTrue(bot.consume(self.queue,self.job['job_id']))
        self.assertEqual(len(conflicts),1)
        self.assertEqual(self.server.sendmail.call_count,3)
        self.assertEqual(list(self.queue.get(self.queue.path)[0]['receipts'].values()),['accepted']*3)

    def test_accepted_receipt_write_failure_keeps_ambiguous_lock_and_detail(self):
        original=self.queue.put
        def put(path,value,sha=None):
            if path==self.queue.path and 'accepted' in value.get('receipts',{}).values():
                raise RuntimeError('receipt persistence unavailable')
            return original(path,value,sha)
        self.queue.put=put
        with self.assertRaises(RuntimeError):bot.consume(self.queue,self.job['job_id'])
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'uncertain')
        self.assertIn('sending',state['receipts'].values())
        event=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual(event['stage'],'receipt_accepted')
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.path.read_bytes(),self.remote_history)

    def test_cleanup_error_is_separate_and_never_replaces_original(self):
        original=self.queue.put
        def put(path,value,sha=None):
            if path==self.queue.path and value.get('status')=='uncertain':
                raise RuntimeError('cleanup unavailable')
            return original(path,value,sha)
        self.queue.put=put;self.server.sendmail.side_effect=ConnectionResetError('after DATA')
        with self.assertRaises(ConnectionResetError):bot.consume(self.queue,self.job['job_id'])
        event=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual(event['error']['error_type'],'ConnectionResetError')
        self.assertEqual(event['cleanup_error']['error_type'],'RuntimeError')
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'sending')
        self.server.sendmail.assert_called_once()


class RecoveryEvidenceRegression(unittest.TestCase):
    setUp = ProductionRegression.setUp
    rehash = ProductionRegression.rehash
    reload_queue = ProductionRegression.reload_queue

    def test_expired_uncertain_collect_keeps_lock_and_records_exact_blocker(self):
        self.job.update(status='uncertain',expires_at=(now()-timedelta(hours=1)).isoformat(),
                        receipts={'recipient':'planned'})
        self.reload_queue()
        self.queue.files['index.json']={'bot_id':bot.BOT_ID,'jobs':[self.job['job_id']]}
        with patch.object(common,'collect') as collect,self.assertRaisesRegex(RuntimeError,'미종결'):
            bot.collect(self.queue)
        collect.assert_not_called();self.smtp.assert_not_called()
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'uncertain')
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual((e['operation'],e['stage']),('collect','outstanding'))
        self.assertEqual(e['blocking_job']['job_id'],self.job['job_id'])
        self.assertEqual(e['blocking_job']['status'],'uncertain')
        self.assertIn('미종결',e['error']['message']);self.assertTrue(e['stack'])
        self.assertNotIn('input',e)

    def test_collection_source_failure_is_recorded_without_an_empty_success(self):
        with patch.object(bot,'outstanding'),patch.object(common,'collect',side_effect=ValueError('source fetch failed')):
            with self.assertRaisesRegex(ValueError,'source fetch failed'):bot.collect(self.queue)
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual((e['operation'],e['stage']),('collect','source_collection'))
        self.smtp.assert_not_called()

    def test_message_ids_are_persisted_before_smtp_and_match_exact_payload(self):
        from email import message_from_string
        mids=[]
        def send(sender,targets,payload):
            key=__import__('hashlib').sha256(targets[0].encode()).hexdigest()
            row=self.queue.get(self.queue.path)[0]
            self.assertEqual(row['receipts'][key],'sending')
            mid=message_from_string(payload)['Message-ID'];mids.append(mid)
            self.assertEqual(row['delivery_evidence'][key]['message_id'],mid)
            self.assertEqual(row['delivery_evidence'][key]['submission'],'may_have_started')
            return {}
        self.server.sendmail.side_effect=send
        bot.consume(self.queue,self.job['job_id'])
        row=self.queue.get(self.queue.path)[0]
        self.assertEqual(len(set(mids)),3)
        self.assertTrue(all(x['submission']=='accepted' for x in row['delivery_evidence'].values()))

    def test_smtp_returned_accepted_is_preserved_when_receipt_put_fails(self):
        original=self.queue.put
        def put(path,value,sha=None):
            if path==self.queue.path and 'accepted' in value.get('receipts',{}).values():
                raise RuntimeError('receipt persist failed')
            return original(path,value,sha)
        self.queue.put=put
        with self.assertRaisesRegex(RuntimeError,'receipt persist failed'):bot.consume(self.queue,self.job['job_id'])
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        observed=list(e['smtp_observations'].values())
        self.assertEqual(len(observed),1);self.assertEqual(observed[0]['submission'],'accepted')
        self.assertTrue(observed[0]['message_id'])
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'uncertain')
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()

    def test_history_failure_keeps_accepted_receipts_and_never_resends(self):
        with patch.object(bot,'write_history',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):bot.consume(self.queue,self.job['job_id'])
        row=self.queue.get(self.queue.path)[0]
        self.assertEqual(row['status'],'delivery_complete')
        self.assertEqual(list(row['receipts'].values()),['accepted']*3)
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual(e['stage'],'history_write')
        self.assertEqual(len(e['smtp_observations']),3)
        with self.assertRaisesRegex(RuntimeError,'잠금'):bot.consume(self.queue,self.job['job_id'])
        self.assertEqual(self.server.sendmail.call_count,3)

    def test_ack_failure_is_recorded_without_downgrading_delivery(self):
        bot.consume(self.queue,self.job['job_id'])
        with self.assertRaisesRegex(RuntimeError,'main 처리 이력 불일치'):bot.ack(self.queue,self.job['job_id'])
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual((e['operation'],e['stage']),('ack','history_ack'))
        self.assertEqual(self.queue.get(self.queue.path)[0]['status'],'delivery_complete')
        self.assertEqual(self.server.sendmail.call_count,3)

    def test_refusal_is_not_mislabeled_as_accepted_or_retried(self):
        self.server.sendmail.side_effect=smtplib.SMTPRecipientsRefused({'hidden@example.invalid':(550,b'blocked')})
        with self.assertRaisesRegex(RuntimeError,'SMTP 거부'):bot.consume(self.queue,self.job['job_id'])
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        o=next(iter(e['smtp_observations'].values()))
        self.assertEqual((o['submission'],o['smtp_codes']),('rejected',[550]))
        self.assertNotIn('hidden@example.invalid',json.dumps(e))
        self.server.sendmail.assert_called_once()

    def test_expired_pending_never_reaches_smtp(self):
        self.job['expires_at']=(now()-timedelta(seconds=1)).isoformat();self.reload_queue()
        with self.assertRaises(ValueError):bot.consume(self.queue,self.job['job_id'])
        self.smtp.assert_not_called()
        e=next(v for p,v in self.queue.files.items() if p.startswith('failures/'))
        self.assertEqual(e['stage'],'preflight_or_render')

if __name__=='__main__':unittest.main()




