"""
test_blog_score.py
- 방금 검증된 블로그 글을 실제 monitor.py의 analyze_sentiment() 로직 그대로 채점
- 본문 전체 재수집 → Sonnet 분석 → 결과 메일 발송
"""

import os
import re
import json
import time
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

TEST_URL = "https://m.blog.naver.com/lfg79/224341880031"

def log(msg):
    print(f"[TEST] {msg}")

def get_blog_body_and_title():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        )
        page.goto(TEST_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        title = ""
        title_el = page.query_selector(".se-title-text, .htitle, h3.se_textarea")
        if title_el:
            title = title_el.inner_text().strip()

        body = ""
        el = page.query_selector(".se-main-container")
        if el:
            body = el.inner_text().strip()

        browser.close()
        return title, body

# ── monitor.py의 analyze_sentiment() 그대로 이식 ────────────────────
def analyze_sentiment(title, body, keyword):
    prompt = f"""다음은 네이버 카페 게시글입니다.
이 게시글이 '한국투자증권(한투/뱅키스/BanKIS)' 자체에 대한 불만·비판·리스크인지 판단하고, 3줄 이내로 요약해주세요.

[제목]
{title}

[본문]
{body[:1500] if body else "(본문 없음 - 제목만으로 판단)"}

▶ 부정(is_negative: true)으로 판단할 것:
- 한국투자증권의 서비스·앱·장애·응대·수수료·전산·출금·주문·HTS/MTS·신뢰와 직접 관련된 불만·비판·피해
- 욕설·비방·사기 주장·집단 민원 촉구

▶ 부정 아님(is_negative: false)으로 판단할 것:
- 단순 질문·이벤트 문의·정보 공유·증권사 비교
- 시장 전반 불만 또는 타 증권사 불만
- 한국투자증권 단순 언급 (중립·긍정 문맥)
- 일반 투자 손실 (한국투자증권 귀책 아닌 경우)
- 영웅문·키움·미래에셋·삼성증권·NH·신한·KB 등 타 증권사 앱·서비스 문제 (댓글에 한투가 단순 언급된 경우 포함)
- 광고성·홍보성 게시글 (사기 피해 회복 안내, 법률 상담 유도, 무료 상담 홍보 등) → score=0
- 한국투자증권 브랜드를 사칭하는 사기꾼 주의 안내글 (한투 자체 서비스 문제 아님) → score=0

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"is_negative": true or false, "summary": "게시글 내용을 3줄 이내 요약 (객관적 서술체)", "score": 0~10, "reply": "추천 대응 답변"}}
score는 부정 강도 (0=전혀 부정 아님, 10=매우 부정적)
reply는 같은 카페 회원이 친근하게 댓글 달아주는 느낌. 2~3문장. 편한 존댓말. 핵심만 짧게. 반드시 지킬 규칙: (1) 고객센터 번호가 필요하면 반드시 "1544-5000"만 사용. 다른 번호는 절대 만들어내지 말 것 (2) URL·구체적 수치·정책 등 확인 안 된 내용은 단정하지 말고 "정확한 건 앱이나 고객센터에서 확인해보세요" 수준으로 마무리 (3) 불만글이면 가볍게 공감 한 마디 + 앱/고객센터 확인 안내 (4) 뉴스 공유·사건 사고 글은 "저도 봤는데 좀 당황스럽네요" 같은 가벼운 반응 수준으로, 금감원·보상 등 극단적 표현 금지 (5) 질문글이면 아는 선에서 짧게 + 불확실하면 "정확한 건 직접 확인해보시는 게 나을 것 같아요" (6) 대응 불필요한 경우 그 이유 한 줄."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": SENTIMENT_MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    resp = response.json()
    text = resp["content"][0]["text"].strip()
    match = re.search(r'\{.*\}', text, re.S)
    result = json.loads(match.group())
    try:
        result["score"] = int(float(result.get("score") or 0))
    except (ValueError, TypeError):
        result["score"] = 0
    return result

def send_result_email(title, body, result):
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    html = f"""
    <h3>블로그 글 채점 결과 (실제 파이프라인 - {SENTIMENT_MODEL})</h3>
    <p><b>제목:</b> {title}</p>
    <p><b>본문 길이:</b> {len(body)}자</p>
    <hr>
    <p><b>부정 여부:</b> {result.get('is_negative')}</p>
    <p><b>부정 강도:</b> {result.get('score')}/10</p>
    <p><b>AI 요약:</b> {result.get('summary')}</p>
    <p><b>추천 대응:</b> {result.get('reply')}</p>
    <hr>
    <p><b>본문 전체:</b></p>
    <pre style="white-space:pre-wrap;">{body}</pre>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[블로그 채점 테스트] score={result.get('score')} | {now_str} KST"
    msg["From"] = f"블로그검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    print("결과 메일 발송 완료")

def main():
    try:
        log("본문 재수집 중...")
        title, body = get_blog_body_and_title()
        log(f"제목: {title}")
        log(f"본문 길이: {len(body)}자")

        log("Sonnet으로 채점 중...")
        result = analyze_sentiment(title, body, "한국투자증권")
        log(f"결과: {result}")

        send_result_email(title, body, result)
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(err)
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "[블로그 채점 테스트] 오류 발생"
            msg["From"] = f"블로그검증봇 <{GMAIL_USER}>"
            msg["To"] = GMAIL_USER
            msg.attach(MIMEText(f"<pre>{err}</pre>", "html", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(GMAIL_USER, GMAIL_APP_PW)
                s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
        except Exception as e2:
            print(f"오류 메일 발송도 실패: {e2}")
        raise

if __name__ == "__main__":
    main()
