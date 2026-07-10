"""
test_blog_specific.py
- 사용자가 지정한 3개 블로그 URL을 실제 analyze_sentiment 로직으로 검증
"""

import os
import re
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_APP_PW   = os.environ["GMAIL_APP_PW"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
SENTIMENT_MODEL = "claude-sonnet-4-6"

TEST_URLS = [
    "https://blog.naver.com/newsfield_/224325145393",
    "https://blog.naver.com/romejo/224324166234",
    "https://blog.naver.com/bmybmybmy/224329084827",
]

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

AD_SKIP_PATTERNS = [
    "사기 피해 회복", "피해 회복 전", "피해금 회복", "피해 복구",
    "알아야 할 핵심 체크포인트", "피해 회복 체크포인트", "알아야 할 핵심",
    "무료 상담", "카카오톡 문의", "텔레그램 문의",
    "피해 전문", "전문 변호사", "법률 상담",
    "리딩방", "투자 리딩", "수익 인증",
    "협찬", "체험단", "제공받아", "원고료", "유료광고",
]

BODY_SELECTORS = [".se-main-container", "#postViewArea", ".post_ct", ".se-component-content"]
TITLE_SELECTORS = [".se-title-text", ".htitle", "h3.se_textarea"]
DATE_SELECTORS = [".blog_date", "[class*='date']"]

logs = []
def log(msg):
    line = f"[SPECIFIC] {msg}"
    print(line)
    logs.append(line)

def get_blog_detail(page, url):
    result = {"title": "", "body": "", "date_str": ""}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception as e:
        log(f"  로드 실패: {e}")
        return result
    for sel in TITLE_SELECTORS:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                result["title"] = t
                break
    for sel in BODY_SELECTORS:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                result["body"] = t
                break
    for sel in DATE_SELECTORS:
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

▶ 부정(is_negative: true)으로 판단할 것:
- 한국투자증권의 서비스·앱·장애·응대·수수료·전산·출금·주문·HTS/MTS·신뢰와 직접 관련된 불만·비판·피해
- 욕설·비방·사기 주장·집단 민원 촉구
- 전산장애·배상·금감원 제재 등 회사 귀책 리스크 뉴스

▶ 부정 아님(is_negative: false)으로 판단할 것:
- 단순 질문·이벤트 문의·정보 공유·증권사 비교
- 시장 전반 불만 또는 타 증권사 불만 (한국투자증권 무관)
- 한국투자증권 단순 언급 (중립·긍정 문맥)
- 광고성·홍보성 게시글

반드시 아래 JSON 형식으로만 응답하세요:
{{"is_negative": true or false, "summary": "게시글 내용을 3줄 이내 요약 (객관적 서술체)", "score": 0~10, "reply": "추천 대응 답변"}}
score는 부정 강도 (0=전혀 부정 아님, 10=매우 부정적)"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": SENTIMENT_MODEL, "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
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
    msg["Subject"] = f"[블로그 지정글 검증] 결과 | {now_str} KST"
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

            for i, url in enumerate(TEST_URLS, 1):
                log(f"\n{'='*50}")
                log(f"[{i}/{len(TEST_URLS)}] {url}")
                log(f"{'='*50}")

                detail = get_blog_detail(page, url)
                title, body, date_str = detail["title"], detail["body"], detail["date_str"]

                log(f"제목: {title}")
                log(f"본문 길이: {len(body)}자")
                log(f"날짜: {date_str}")

                if not title:
                    log("❌ 제목 수집 실패 - 셀렉터 미매칭 (구조 다를 가능성)")
                    continue

                combined = (title + " " + body).lower()
                has_hint = any(h in combined for h in NEGATIVE_HINTS)
                matched_hints = [h for h in NEGATIVE_HINTS if h in combined]
                log(f"룰필터① 부정힌트 통과: {has_hint} (매칭: {matched_hints[:5]})")

                if not has_hint:
                    log("→ AI 분석 생략 (부정힌트 없음) → 확인필요 섹션 대상")
                    continue

                is_ad = any(p in title for p in AD_SKIP_PATTERNS)
                log(f"룰필터② 광고패턴: {is_ad}")
                if is_ad:
                    log("→ 광고성 게시글로 제외")
                    continue

                result = analyze_sentiment(title, body, "한국투자증권")
                log(f"\n✅ AI 분석 결과:")
                log(f"   is_negative: {result['is_negative']}")
                log(f"   score: {result['score']}/10")
                log(f"   summary: {result['summary']}")
                log(f"   SCORE_THRESHOLD(1) 통과 → 알림발송: {result['score'] >= 1}")

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
