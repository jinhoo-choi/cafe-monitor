"""
test_blog_e2e.py
- search_naver_blog() → get_blog_detail() → analyze_sentiment() 전체 파이프라인 검증
- 실제 monitor.py 통합 전 최종 확인
"""

import os
import re
import json
import random
import smtplib
import requests
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_APP_PW   = os.environ["GMAIL_APP_PW"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
SENTIMENT_MODEL = "claude-sonnet-4-6"

KEYWORDS = ["한국투자증권", "한투", "뱅키스"]
NEGATIVE_HINTS = [
    "불만", "먹통", "최악", "탈출", "화남", "짜증", "안됨", "안돼",
    "피해", "피해자", "사기", "민원", "환불", "차단", "거부",
    "왜이럼", "왜이러", "미치겠", "황당", "어이없", "답답",
    "이상해", "이상한", "문제", "느려", "버벅", "튕겨", "튕김",
    "안되네", "안되는", "안됩니다", "기다리고", "기다림",
    "연결안", "상담원", "대기", "처리안", "왜안되", "왜못",
    "오류", "에러", "버그", "장애", "먹힘",
    "HTS", "MTS", "고객센터", "실패",
    "계좌개설", "해외계좌",
    "접속불가", "로그인불가", "주문불가", "체결불가",
]

BLOG_POST_ID_PATTERN = re.compile(r'/(\d{6,})(\?|$)')
BLOG_BODY_SELECTORS = [".se-main-container", "#postViewArea", ".post_ct", ".se-component-content"]
BLOG_TITLE_SELECTORS = [".se-title-text", ".htitle", "h3.se_textarea"]
BLOG_DATE_SELECTORS = [".blog_date", "[class*='date']"]

logs = []
def log(msg):
    line = f"[E2E] {msg}"
    print(line)
    logs.append(line)

def search_naver_blog(page, keyword):
    encoded = urllib.parse.quote(keyword)
    url = f"https://m.search.naver.com/search.naver?where=m_blog&query={encoded}&sm=mtb_opt&nso=so%3Add%2Cp%3Aall"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(random.randint(2000, 3000))

    anchors = page.query_selector_all("a[href*='blog.naver.com']")
    seen_urls = set()
    post_urls = []
    for a in anchors:
        href = a.get_attribute("href") or ""
        m = BLOG_POST_ID_PATTERN.search(href)
        if not m:
            continue
        clean_url = href.split("?")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        post_urls.append({"url": clean_url, "post_id": m.group(1), "keyword": keyword})
    return post_urls

def get_blog_detail(page, post_url):
    result = {"title": "", "body": "", "date_str": ""}
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
    except Exception as e:
        log(f"  로드 실패: {e}")
        return result
    for sel in BLOG_TITLE_SELECTORS:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                result["title"] = t
                break
    for sel in BLOG_BODY_SELECTORS:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                result["body"] = t[:2000]
                break
    for sel in BLOG_DATE_SELECTORS:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                result["date_str"] = t
                break
    return result

def analyze_sentiment(title, body, keyword):
    prompt = f"""다음은 네이버 블로그 게시글입니다.
이 게시글이 '한국투자증권(한투/뱅키스)' 자체에 대한 불만·비판·리스크인지 판단하고, 3줄 이내로 요약해주세요.

[제목]
{title}

[본문]
{body[:1500] if body else "(본문 없음)"}

▶ 부정으로 판단: 서비스·앱·장애·응대·수수료·전산·출금·주문·HTS/MTS·신뢰 관련 불만·비판·피해, 욕설·비방·사기주장
▶ 부정 아님: 단순 질문·정보공유·증권사비교, 타증권사 불만, 단순언급, 광고성 게시글

반드시 JSON만 응답: {{"is_negative": true/false, "summary": "요약", "score": 0~10, "reply": ""}}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": SENTIMENT_MODEL, "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    resp = r.json()
    text = resp["content"][0]["text"].strip()
    m = re.search(r'\{.*\}', text, re.S)
    result = json.loads(m.group())
    try:
        result["score"] = int(float(result.get("score") or 0))
    except (ValueError, TypeError):
        result["score"] = 0
    return result

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    body = "\n".join(logs)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[블로그 E2E 검증] 결과 | {now_str} KST"
    msg["From"] = f"블로그검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{body}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
            )

            all_posts = {}
            for kw in KEYWORDS:
                log(f"검색: [{kw}]")
                results = search_naver_blog(page, kw)
                log(f"  → {len(results)}건")
                for post in results:
                    pid = post["post_id"]
                    if pid not in all_posts:
                        all_posts[pid] = post

            posts = list(all_posts.values())[:5]  # 테스트니까 5건만
            log(f"\n총 {len(all_posts)}건 중 상위 5건 상세 분석 진행\n")

            cutoff = datetime.now(KST) - timedelta(hours=24)

            for i, post in enumerate(posts, 1):
                log(f"--- [{i}/5] {post['url']} ---")
                detail = get_blog_detail(page, post["url"])
                title, body, date_str = detail["title"], detail["body"], detail["date_str"]
                log(f"  제목: {title[:60]}")
                log(f"  본문길이: {len(body)}자")
                log(f"  날짜: {date_str}")

                combined = (title + " " + body).lower()
                has_hint = any(h in combined for h in NEGATIVE_HINTS)
                log(f"  룰필터 통과(부정힌트): {has_hint}")

                if has_hint:
                    result = analyze_sentiment(title, body, post["keyword"])
                    log(f"  ✅ AI 분석 결과: is_negative={result['is_negative']}, score={result['score']}")
                    log(f"     요약: {result['summary']}")
                else:
                    log(f"  → AI 분석 생략 (부정힌트 없음)")
                log("")

            browser.close()
        send_result_email()
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"❌ 오류: {err}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
