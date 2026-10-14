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
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.tmp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path=Path(self.tmp)/'seen.json'
        self.path.write_text('{}\n')
        self.stack.enter_context(patch.dict(os.environ,{'NEGATIVE_SEEN_PATH':str(self.path),
            'GMAIL_USER':'owner@example.invalid','GMAIL_APP_PW':'offline',
            'NOTIFY_EMAIL':'ops@example.invalid,owner@example.invalid',
            'EMAIL_CC':'cc@example.invalid,ops@example.invalid','GITHUB_REF':'refs/heads/main'}))
        self.job,self.result=fixture()
        self.job.update(mode='production',self_test=False)
        self.job['input']['items'][0]['dataset']='live'
        self.job['input']['seen_hash']=common.seen_snapshot()[1]
        self.rehash()
        self.queue=MemoryQueue(self.job,self.result)
        self.remote_history=self.path.read_bytes()
        self.queue.source=lambda path:(Path(bot.CONFIG_PATH).read_bytes(),'config') if path==bot.CONFIG_PATH else (self.remote_history,'history-blob')
        self.server=MagicMock()
        self.server.sendmail.return_value={}
        self.smtp=self.stack.enter_context(patch.object(bot.smtplib,'SMTP_SSL',return_value=self.server))

    def rehash(self):
        self.job['input_hash']=digest(self.job['input'])
        self.result['input_hash']=self.job['input_hash']

    def reload_queue(self):
        self.queue.files[self.queue.path]=copy.deepcopy(self.job)
        self.queue.files['results/'+self.job['job_id']+'.json']=copy.deepcopy(self.result)

    def test_recipient_receipts_history_push_and_ack_required_for_completed(self):
        self.assertTrue(bot.consume(self.queue,self.job['job_id']))
        state=self.queue.get(self.queue.path)[0]
        self.assertEqual(state['status'],'delivery_complete')
        self.assertEqual(list(state['receipts'].values()),['accepted']*3)
        self.assertEqual(self.server.sendmail.call_count,3)
        self.assertEqual([c.args[1] for c in self.server.sendmail.call_args_list],
                         [['owner@example.invalid'],['ops@example.invalid'],['cc@example.invalid']])
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

    def test_no_negative_sends_only_owner_and_holds_missing_body(self):
        self.job['input']['items'][0]['body_status']='missing'
        self.result['posts'][0].update(verdict='needs_review',score=None,category='unresolved',urgency='watch',evidence_quotes=[])
        self.result['groups']=[]
        self.rehash();self.reload_queue()
        bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.server.sendmail.call_args.args[1],['owner@example.invalid'])
        self.assertEqual(json.loads(self.path.read_text()),{})
        self.assertEqual(self.queue.get(self.queue.path)[0]['processed_ids'],[])
        self.assertTrue(self.queue.get('reviews/'+self.job['job_id']+'.json')[0]['retry_next_collection'])

    def test_non_negative_is_saved_after_owner_status_mail(self):
        self.result['posts'][0].update(verdict='non_negative',score=0,urgency='watch',category='neutral_other')
        self.result['groups']=[]
        self.reload_queue()
        bot.consume(self.queue,self.job['job_id'])
        self.server.sendmail.assert_called_once()
        self.assertEqual(self.server.sendmail.call_args.args[1],['owner@example.invalid'])
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

    def test_compact_mail_keeps_actions_and_separates_source_from_body_holds(self):
        rendered=common.render(self.job,self.result,production=True)
        self.assertIn('AI 요약',rendered)
        self.assertIn('내부 확인·대응',rendered)
        self.assertIn('검토용 답변 초안',rendered)
        self.assertIn('수집원 오류·접근 보류 0곳',rendered)
        self.assertIn('본문 미수집 0건',rendered)
        self.assertNotIn('비부정·보류 판정 근거',rendered)
        self.assertNotIn('unconfirmed',rendered)
        self.assertNotIn('[TEST]',rendered)
        self.smtp.assert_not_called()

    def test_paid_model_paths_require_explicit_api_setting(self):
        module=common.collector()
        with self.assertRaisesRegex(RuntimeError,'유료 API'):module._require_paid_engine()
        self.assertEqual(module.API_STATS['calls'],0)


if __name__=='__main__':unittest.main()
