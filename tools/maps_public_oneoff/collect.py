"""One bounded, anonymous public-search snapshot. No post-page navigation or secrets."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KST = timezone(timedelta(hours=9))
START_DATE = "2026-10-01"
END_DATE = "2026-10-06"
QUERIES = (
    '"미래에셋" "MAPS"', '"미래에셋" "맵스"',
    '"미래에셋증권" "MAPS"', '"미래에셋" "M-STOCK"',
    '"미래에셋" "엠스탁"', '"미래에셋" "개편"',
)
MAX_CANDIDATES = 240
MAX_PER_SEARCH = 40
MAX_SECONDS = 480
AAD = b"maps-public-search-snapshot-v1"
FIELDS = ("id", "captured_at", "source", "url", "title", "excerpt", "date_raw", "published_date",
          "date_precision", "period_status", "matched_queries", "access_status",
          "evidence_type", "privacy_redacted", "review_status")


def canonical_url(raw: str) -> tuple[str, str] | None:
    """Discard all tracking/search-access parameters; never navigate canonical URLs."""
    try:
        p = urlsplit(raw)
        if p.scheme not in ("https", "http") or p.username or p.password:
            return None
        host = (p.hostname or "").lower()
        path = p.path
        if host in {"blog.naver.com", "m.blog.naver.com"}:
            m = re.fullmatch(r"/([A-Za-z0-9_-]+)/([0-9]{6,})/?", path)
            if m:
                return "blog", f"https://blog.naver.com/{m[1]}/{m[2]}"
            if path in {"/PostView.naver", "/PostView.nhn"}:
                q = parse_qs(p.query)
                owner, pid = q.get("blogId", [""])[0], q.get("logNo", [""])[0]
                if re.fullmatch(r"[A-Za-z0-9_-]+", owner) and re.fullmatch(r"[0-9]{6,}", pid):
                    return "blog", f"https://blog.naver.com/{owner}/{pid}"
        if host in {"cafe.naver.com", "m.cafe.naver.com"}:
            m = re.fullmatch(r"/([A-Za-z0-9_-]+)/([0-9]+)/?", path)
            if m:
                return "cafe", f"https://cafe.naver.com/{m[1]}/{m[2]}"
            m = re.fullmatch(r"/f-e/cafes/([0-9]+)/articles/([0-9]+)/?", path)
            if m:
                return "cafe", f"https://cafe.naver.com/f-e/cafes/{m[1]}/articles/{m[2]}"
            if path in {"/ArticleRead.nhn", "/ArticleRead.naver"}:
                q = parse_qs(p.query)
                cid, aid = q.get("clubid", [""])[0], q.get("articleid", [""])[0]
                if cid.isdigit() and aid.isdigit():
                    return "cafe", f"https://cafe.naver.com/f-e/cafes/{cid}/articles/{aid}"
    except (ValueError, TypeError):
        return None
    return None


def clean_text(value: str, limit: int) -> tuple[str, bool]:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip()
    original = value
    # Do not retain contact details, account numbers, holdings, or child information.
    for pattern in (
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?<!\d)(?:01[016789]|0[2-6]\d?)[ -]?\d{3,4}[ -]?\d{4}(?!\d)",
        r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?!\d)",
        r"(?<!\d)\d[\d,.]*(?:\s*(?:억|천만|백만|만|천))?\s*원",
        r"(?<!\d)\d[\d,.]*\s*주\s*(?:보유|샀|매수|갖고)",
    ):
        value = re.sub(pattern, "[개인정보·금액 생략]", value)
    if re.search(r"(?:미성년|어린이|아들|딸|자녀|아이\s*계좌|초등|중학생|고등학생)", value):
        value = "[미성년자 관련 가능성이 있어 발췌 생략]"
    return value[:limit], value != original


def parse_date(raw: str, now: datetime) -> tuple[str, str]:
    raw = str(raw or "").strip()
    m = re.fullmatch(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*\.?", raw)
    if m:
        try:
            return datetime(*map(int, m.groups()), tzinfo=KST).date().isoformat(), "day"
        except ValueError:
            return "", "unknown"
    m = re.fullmatch(r"(\d+)\s*(분|시간|일)\s*전", raw)
    if m:
        count = int(m[1])
        delta = {"분": timedelta(minutes=count), "시간": timedelta(hours=count), "일": timedelta(days=count)}[m[2]]
        return (now - delta).date().isoformat(), "relative_estimate"
    if raw in {"방금", "오늘", "어제"}:
        return (now - timedelta(days=1 if raw == "어제" else 0)).date().isoformat(), "relative_estimate"
    return "", "unknown"


def record_from_card(card: dict, query: str, now: datetime) -> dict | None:
    identity = canonical_url(card.get("href", ""))
    if not identity:
        return None
    source, url = identity
    texts = []
    for text in card.get("texts", [])[:3]:
        text = str(text).strip()
        if text and text not in texts:
            texts.append(text)
    if not texts:
        return None
    title, redacted1 = clean_text(texts[0], 160)
    excerpt, redacted2 = clean_text(" ".join(texts[1:]) if len(texts) > 1 else "", 350)
    date_raw, _ = clean_text(card.get("date", ""), 40)
    date, precision = parse_date(date_raw, now)
    status = "date_unverified" if not date else ("within_window" if START_DATE <= date <= END_DATE else "outside_window")
    # Relative dates around launch are estimates, not verified publication dates.
    if precision == "relative_estimate":
        status += "_estimated"
    return dict(zip(FIELDS, (
        "maps-" + hashlib.sha256(url.encode()).hexdigest()[:16], now.isoformat(),
        source, url, title, excerpt, date_raw, date, precision, status, [query],
        "public_search_snippet_only", "unverified_candidate",
        redacted1 or redacted2, "manual_review_required",
    )))


def merge_record(existing: dict, new: dict) -> None:
    existing["matched_queries"] = list(dict.fromkeys(existing["matched_queries"] + new["matched_queries"]))
    if len(new["excerpt"]) > len(existing["excerpt"]):
        existing["excerpt"] = new["excerpt"]
    if not existing["published_date"] and new["published_date"]:
        for key in ("date_raw", "published_date", "date_precision", "period_status"):
            existing[key] = new[key]
    existing["privacy_redacted"] |= new["privacy_redacted"]


def archive_bytes(records: list[dict], audit: dict) -> bytes:
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=FIELDS)
    writer.writeheader()
    for row in records:
        safe = dict(row)
        safe["matched_queries"] = " | ".join(safe["matched_queries"])
        # Neutralize spreadsheet formula injection from untrusted public text.
        for key, value in safe.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                safe[key] = "'" + value
        writer.writerow(safe)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("candidates.json", json.dumps(records, ensure_ascii=False, indent=2))
        z.writestr("candidates.csv", "\ufeff" + csv_buffer.getvalue())
        z.writestr("audit.json", json.dumps(audit, ensure_ascii=False, indent=2))
    return output.getvalue()


def seal(data: bytes, public_pem: bytes) -> dict:
    public_key = serialization.load_pem_public_key(public_pem)
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
        raise ValueError("Public key type/size rejected")
    session_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(session_key).encrypt(nonce, data, AAD)
    wrapped_key = public_key.encrypt(session_key, padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=AAD,
    ))
    b64 = lambda value: base64.b64encode(value).decode("ascii")
    return {
        "format": "maps-envelope-v1", "algorithm": "RSA-OAEP-SHA256+A256GCM",
        "public_key_sha256": hashlib.sha256(public_pem).hexdigest(),
        "wrapped_key": b64(wrapped_key), "nonce": b64(nonce), "ciphertext": b64(ciphertext),
    }


EXTRACT_CARDS = r"""() => {
  const main = document.querySelector('main') || document.querySelector('#main_pack');
  if (!main) return [];
  const results = [];
  for (const a of main.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!/^https?:\/\/(?:m\.)?(?:blog|cafe)\.naver\.com\//i.test(href)) continue;
    const anchorText = (a.innerText || '').trim();
    if (!anchorText || anchorText.length < 3) continue;
    let root = a.closest('li') || a.closest('.view_wrap') || a.closest('.api_ani_send');
    if (!root) root = a.parentElement?.parentElement;
    const texts = [anchorText];
    // Same-target text links only: no author profiles or complete card text persisted.
    if (root) for (const other of root.querySelectorAll('a[href]')) {
      if (other.getAttribute('href') === href) {
        const text = (other.innerText || '').trim();
        if (text && !texts.includes(text)) texts.push(text);
      }
    }
    const dateCandidates = root ? [...root.querySelectorAll('time, [class*=date], .sub, .user_info')]
      .map(el => (el.innerText || '').trim()).filter(x => x.length <= 50) : [];
    let date = dateCandidates.find(x => /^(?:\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?|\d+\s*(?:분|시간|일)\s*전|방금|오늘|어제)$/.test(x)) || '';
    if (!date && root) {
      // Only emit a date string; surrounding author/location text is not returned.
      const match = (root.innerText || '').slice(0, 2000).match(/(?:^|\n)(\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\.?|\d+\s*(?:분|시간|일)\s*전|방금|오늘|어제)(?:\n|$)/);
      if (match) date = match[1];
    }
    results.push({href, texts: texts.slice(0, 3), date});
    if (results.length >= 150) break;
  }
  return results;
}"""


def search_url(source: str, query: str) -> str:
    if source not in {"blog", "cafe"}:
        raise ValueError("Invalid source")
    return "https://search.naver.com/search.naver?" + urlencode({
        "ssc": f"tab.{source}.all", "sm": "tab_opt", "query": query,
        "nso": "so:dd,p:from20261001to20261006",
    })


def collect() -> tuple[list[dict], dict]:
    from playwright.sync_api import sync_playwright
    now = datetime.now(KST)
    began = time.monotonic()
    records: dict[str, dict] = {}
    audit = {"captured_at_kst": now.isoformat(), "window_start": START_DATE,
             "window_end": END_DATE, "source": "anonymous_public_search",
             "original_posts_opened": 0, "queries": [], "complete": False,
             "limitations": ["search_index_sample_not_population", "snippet_only_not_verified_customer",
                             "date_filter_not_proof_of_post_date", "relative_dates_are_estimates",
                             "at_most_two_loaded_search_views_per_query", "no_private_content_access"]}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", accept_downloads=False)
        # No cookie file, storage-state import, trace, screenshot, HAR, or console forwarding.
        def restrict(route):
            parsed = urlsplit(route.request.url)
            host = (parsed.hostname or "").lower()
            allowed = (host == "naver.com" or host.endswith(".naver.com") or
                       host == "pstatic.net" or host.endswith(".pstatic.net"))
            if parsed.scheme == "https" and allowed:
                route.continue_()
            else:
                route.abort()
        context.route("**/*", restrict)
        page = context.new_page()
        stop = False
        for source in ("blog", "cafe"):
            for query in QUERIES:
                if stop or time.monotonic() - began > MAX_SECONDS or len(records) >= MAX_CANDIDATES:
                    stop = True
                    break
                status = {"source": source, "query": query, "views": 0, "candidates": 0, "status": "ok"}
                try:
                    response = page.goto(search_url(source, query), wait_until="domcontentloaded", timeout=25000)
                    if response and response.status >= 400:
                        status["status"] = "http_blocked"
                        stop = response.status in {401, 403, 429}
                    elif urlsplit(page.url).hostname != "search.naver.com":
                        status["status"] = "unexpected_redirect_stopped"
                        stop = True
                    else:
                        page.wait_for_timeout(2500)
                        seen_for_search = set()
                        for view in range(2):
                            if re.search(r"자동입력 방지|보안문자|비정상적인 접근|접근이 제한|unusual traffic", page.locator("body").inner_text(timeout=5000), re.I):
                                status["status"] = "access_check_stopped"
                                stop = True
                                break
                            cards = page.evaluate(EXTRACT_CARDS)
                            status["views"] += 1
                            for card in cards:
                                row = record_from_card(card, query, now)
                                if not row or row["source"] != source:
                                    continue
                                url = row["url"]
                                if len(seen_for_search) >= MAX_PER_SEARCH or len(records) >= MAX_CANDIDATES:
                                    break
                                seen_for_search.add(url)
                                if url in records:
                                    merge_record(records[url], row)
                                else:
                                    records[url] = row
                            status["candidates"] = max(status["candidates"], len(seen_for_search))
                            if view == 0:
                                page.mouse.wheel(0, 1800)
                                page.wait_for_timeout(2000)
                        if not seen_for_search and status["status"] == "ok":
                            status["status"] = "no_candidates_or_layout_changed"
                except Exception as error:
                    # Error text may contain URLs or scraped text. Keep only a fixed category.
                    status["status"] = "navigation_or_parse_error"
                    status["error_type"] = type(error).__name__
                audit["queries"].append(status)
        context.close()
        browser.close()
    audit["complete"] = (not stop and len(audit["queries"]) == len(QUERIES) * 2
                         and all(item["status"] == "ok" for item in audit["queries"]))
    audit["unique_candidates"] = len(records)
    return list(records.values()), audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("MAPS_EXECUTE") != "1":
        raise RuntimeError("Explicit execution flag required")
    if os.environ.get("GITHUB_REPOSITORY") != "jinhoo-choi/cafe-monitor":
        raise RuntimeError("Unexpected execution repository")
    if datetime.now(KST).date().isoformat() != END_DATE:
        raise RuntimeError("One-day execution window closed")
    # Reject key problems before doing any collection. No private key exists on runner.
    public_pem = args.public_key.read_bytes()
    seal(b"validation", public_pem)
    records, audit = collect()
    sealed = seal(archive_bytes(records, audit), public_pem)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sealed, separators=(",", ":")), encoding="utf-8")
    print("Encrypted public-search snapshot created. No source content logged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Collection failed safely; no source details emitted.", file=sys.stderr)
        raise SystemExit(1)
