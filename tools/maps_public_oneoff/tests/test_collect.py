import base64
import csv
import importlib.util
import io
import json
import socket
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class PureFunctionTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch.object(socket, "create_connection", side_effect=AssertionError("Network disabled"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.now = datetime(2026, 10, 6, 10, 0, tzinfo=collector.KST)

    def test_canonical_urls_drop_access_tokens(self):
        source, url = collector.canonical_url("https://cafe.naver.com/example/12345?art=DO_NOT_KEEP&tc=naver_search")
        self.assertEqual((source, url), ("cafe", "https://cafe.naver.com/example/12345"))
        self.assertEqual(collector.canonical_url("https://m.blog.naver.com/example/224400000001?tracking=1")[1],
                         "https://blog.naver.com/example/224400000001")

    def test_profiles_and_malicious_destinations_rejected(self):
        for url in ("https://blog.naver.com/example", "https://blog.naver.com.evil.test/a/123456",
                    "https://user:pass@blog.naver.com/a/123456", "javascript:alert(1)",
                    "file:///etc/passwd", "https://evil.test/?blog.naver.com/a/123456"):
            self.assertIsNone(collector.canonical_url(url))

    def test_legacy_urls(self):
        self.assertEqual(collector.canonical_url("https://blog.naver.com/PostView.naver?blogId=example&logNo=224400000001"),
                         ("blog", "https://blog.naver.com/example/224400000001"))
        self.assertEqual(collector.canonical_url("https://cafe.naver.com/ArticleRead.nhn?clubid=123&articleid=456&art=token"),
                         ("cafe", "https://cafe.naver.com/f-e/cafes/123/articles/456"))

    def test_dates_never_fallback_to_now(self):
        self.assertEqual(collector.parse_date("2026. 10. 1.", self.now), ("2026-10-01", "day"))
        self.assertEqual(collector.parse_date("5일 전", self.now), ("2026-10-01", "relative_estimate"))
        for raw in ("", "unknown", "2026.13.40", "2026. 10. 1. 오전 9:00"):
            self.assertEqual(collector.parse_date(raw, self.now), ("", "unknown"))

    def test_sensitive_excerpt_redaction(self):
        text, changed = collector.clean_text("연락 test@example.com 010-1234-5678 보유액 500만원", 500)
        self.assertTrue(changed)
        for secret in ("test@example.com", "010-1234-5678", "500만원"):
            self.assertNotIn(secret, text)
        text, changed = collector.clean_text("초등학생 자녀 계좌 안내", 500)
        self.assertTrue(changed)
        self.assertNotIn("초등학생", text)

    def test_records_do_not_claim_verified_customer(self):
        row = collector.record_from_card({"href": "https://blog.naver.com/example/224400000001",
                                         "texts": ["MAPS 후기", "새 화면이 낯설어요"], "date": "5일 전"}, "MAPS", self.now)
        self.assertEqual(row["access_status"], "public_search_snippet_only")
        self.assertEqual(row["evidence_type"], "unverified_candidate")
        self.assertEqual(row["period_status"], "within_window_estimated")
        self.assertEqual(set(row), set(collector.FIELDS))

    def test_same_url_merges_queries(self):
        row = collector.record_from_card({"href": "https://blog.naver.com/example/224400000001",
                                         "texts": ["MAPS 후기"], "date": ""}, "MAPS", self.now)
        new = dict(row, matched_queries=["맵스"], excerpt="추가 문맥")
        collector.merge_record(row, new)
        self.assertEqual(row["matched_queries"], ["MAPS", "맵스"])
        self.assertEqual(row["excerpt"], "추가 문맥")

    def test_csv_formula_injection_neutralized(self):
        row = collector.record_from_card({"href": "https://blog.naver.com/example/224400000001",
                                         "texts": ['=HYPERLINK("https://evil.test")']}, "MAPS", self.now)
        archive = collector.archive_bytes([row], {"complete": True})
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            self.assertEqual(set(z.namelist()), {"candidates.json", "candidates.csv", "audit.json"})
            csv_rows = list(csv.DictReader(io.StringIO(z.read("candidates.csv").decode("utf-8-sig"))))
            self.assertTrue(csv_rows[0]["title"].startswith("'="))

    def test_search_urls_fixed_destination_and_date(self):
        url = collector.search_url("blog", '"미래에셋" "MAPS"')
        self.assertTrue(url.startswith("https://search.naver.com/search.naver?"))
        self.assertIn("20261001to20261006", url)
        with self.assertRaises(ValueError):
            collector.search_url("https://evil.test", "MAPS")


class EncryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.public_pem = cls.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                         serialization.PublicFormat.SubjectPublicKeyInfo)

    def decrypt(self, envelope):
        raw = lambda field: base64.b64decode(envelope[field])
        aes_key = self.key.decrypt(raw("wrapped_key"), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=collector.AAD))
        return AESGCM(aes_key).decrypt(raw("nonce"), raw("ciphertext"), collector.AAD)

    def test_round_trip_and_plaintext_absent(self):
        plaintext = "fixture-only-secret-do-not-log 한국어".encode()
        envelope = collector.seal(plaintext, self.public_pem)
        self.assertEqual(self.decrypt(envelope), plaintext)
        self.assertNotIn("fixture-only-secret-do-not-log", json.dumps(envelope))
        self.assertEqual(set(envelope), {"format", "algorithm", "public_key_sha256", "wrapped_key", "nonce", "ciphertext"})

    def test_tampered_ciphertext_rejected(self):
        envelope = collector.seal(b"test", self.public_pem)
        payload = bytearray(base64.b64decode(envelope["ciphertext"]))
        payload[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(payload).decode()
        with self.assertRaises(InvalidTag):
            self.decrypt(envelope)

    def test_randomized_encryption(self):
        self.assertNotEqual(collector.seal(b"same", self.public_pem)["ciphertext"],
                            collector.seal(b"same", self.public_pem)["ciphertext"])

    def test_small_key_rejected(self):
        small = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        pem = small.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        with self.assertRaises(ValueError):
            collector.seal(b"test", pem)


class IsolationTests(unittest.TestCase):
    def test_script_contains_no_operating_bot_imports_or_credentials(self):
        code = (ROOT / "collect.py").read_text()
        for value in ("import monitor", "NAVER_COOKIES", "CLAUDE_API_KEY", "smtplib", "seen_posts.json", "subprocess"):
            self.assertNotIn(value, code)
        self.assertEqual(code.count("page.goto("), 1)
        self.assertIn("page.goto(search_url(source, query)", code)

    def test_workflow_upload_allowlist_and_guards(self):
        workflow = (ROOT.parents[1] / ".github/workflows/maps-public-oneoff.yml").read_text()
        self.assertIn("github.event.created == true", workflow)
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("retention-days: 1", workflow)
        for value in ("secrets.", "contents: write", "schedule:", "git push", "seen_posts", "naver-cafe-monitor"):
            self.assertNotIn(value, workflow)
        self.assertIn("path: ${{ runner.temp }}/maps-public-oneoff/envelope.json", workflow)


if __name__ == "__main__":
    unittest.main()
