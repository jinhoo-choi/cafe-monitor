"""
test_grouping.py
- 같은 뉴스를 다루지만 제목이 다른 블로그 글들의 그룹핑 정확도 검증
- 기존(제목만) vs 개선(제목+요약) 비교
"""

import os
import re
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_APP_PW   = os.environ["GMAIL_APP_PW"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
GROUPING_MODEL = "claude-sonnet-4-6"

# 실제 같은 뉴스(전산장애 배상액 1위)를 다루는데 SEO 목적으로 제목이 전부 다른 가상 블로그 글 세트
TEST_POSTS = [
    {"cafe_name": "네이버 블로그", "source": "blog", "title": "한국투자증권, 1분기 전산장애 배상액 1위…잇단 오류에 금감원 보상요구까지",
     "summary": "한국투자증권의 2025년 1분기 전산장애 배상액이 8억3천만원으로 13개 증권사 중 최다를 기록했다. 금감원이 보상안 마련을 요구했다."},
    {"cafe_name": "네이버 블로그", "source": "blog", "title": "요즘 난리난 증권사 전산장애 배상금 순위 TOP3 정리해드림",
     "summary": "국내 증권사 전산장애 배상액 순위를 정리했다. 1위는 한국투자증권으로 8억원대의 배상금을 지급했으며 금감원이 개선을 요구한 상황이다."},
    {"cafe_name": "네이버 블로그", "source": "blog", "title": "한투 MTS 자꾸 오류나던데 이유가 있었네요 (전산장애 배상 1위)",
     "summary": "한국투자증권 MTS 전산 오류가 4년간 11건 발생했으며, 최근 배상액 규모가 업계 최다로 확인됐다. 개인적으로 불편했던 경험을 공유한다."},
    {"cafe_name": "네이버 블로그", "source": "blog", "title": "[투자] 한국투자증권 매입단가 오류 매도 후기",
     "summary": "매입단가 오류로 민원 제기 후 보상받은 개인 투자 후기. 전산장애 배상액 뉴스와는 무관한 개별 사례."},
    {"cafe_name": "미국주식이 미래다", "source": "cafe", "title": "[증권사]한투 쓰시는분 계신가요",
     "summary": "한투 앱 관련 불편·불만을 토로하며 다른 회원들의 이용 경험을 묻는 게시글."},
]

def group_title_only(posts):
    """기존 방식 - 제목만"""
    titles_text = "\n".join(
        f"{i}. [{p['cafe_name']}] {p['title']}" for i, p in enumerate(posts)
    )
    prompt = f"""아래는 네이버 카페에서 탐지된 한국투자증권 관련 게시글 목록입니다.
같은 이슈(동일한 서비스 문제, 장애, 불만)를 다루는 글끼리 묶어주세요.

{titles_text}

규칙:
- 같은 앱 오류/장애면 같은 그룹
- 같은 기능 문의면 같은 그룹
- 명확히 다른 주제면 별도 그룹
- 단독이면 자기 자신만 포함

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"groups": [[0, 1], [2], [3, 4]]}}
groups는 인덱스 리스트의 리스트. 모든 인덱스가 정확히 한 번씩 포함되어야 함."""
    return call_claude(prompt)

def group_title_summary(posts):
    """개선 방식 - 제목 + 요약"""
    items_text = "\n".join(
        f"{i}. [{p['cafe_name']}] {p['title']}\n   요약: {p['summary']}" for i, p in enumerate(posts)
    )
    prompt = f"""아래는 네이버 카페·블로그에서 탐지된 한국투자증권 관련 게시글 목록입니다.
같은 이슈(동일한 서비스 문제, 장애, 사건, 불만)를 다루는 글끼리 묶어주세요.
제목이 다르게 표현되어도 요약 내용상 같은 사건/이슈를 다루면 같은 그룹으로 묶으세요
(예: 블로그는 SEO 목적으로 제목을 다르게 쓰는 경우가 많음).

{items_text}

규칙:
- 같은 사건/뉴스/장애를 다루면 같은 그룹 (제목 표현이 달라도)
- 같은 기능 문의면 같은 그룹
- 명확히 다른 주제면 별도 그룹
- 단독이면 자기 자신만 포함

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"groups": [[0, 1], [2], [3, 4]]}}
groups는 인덱스 리스트의 리스트. 모든 인덱스가 정확히 한 번씩 포함되어야 함."""
    return call_claude(prompt)

def call_claude(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": GROUPING_MODEL, "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    resp = r.json()
    text = resp["content"][0]["text"].strip()
    m = re.search(r'\{.*\}', text, re.S)
    return json.loads(m.group())

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    body = "\n".join(logs)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[그룹핑 검증] 결과 | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{body}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    try:
        log("=== 테스트 게시글 목록 ===")
        for i, p in enumerate(TEST_POSTS):
            log(f"{i}. [{p['cafe_name']}] {p['title']}")

        log("\n=== ① 기존 방식 (제목만) ===")
        r1 = group_title_only(TEST_POSTS)
        log(f"결과: {r1}")
        groups1 = r1.get("groups", [])
        log(f"그룹 수: {len(groups1)}개 (원래 {len(TEST_POSTS)}건)")
        for g in groups1:
            titles = [TEST_POSTS[i]['title'][:30] for i in g]
            log(f"  그룹{g}: {titles}")

        log("\n=== ② 개선 방식 (제목+요약) ===")
        r2 = group_title_summary(TEST_POSTS)
        log(f"결과: {r2}")
        groups2 = r2.get("groups", [])
        log(f"그룹 수: {len(groups2)}개 (원래 {len(TEST_POSTS)}건)")
        for g in groups2:
            titles = [TEST_POSTS[i]['title'][:30] for i in g]
            log(f"  그룹{g}: {titles}")

        log(f"\n=== 비교 ===")
        log(f"기대값: 0,1,2번(전산장애 뉴스)은 한 그룹, 3번(매입단가 후기)과 4번(카페질문)은 별도")
        log(f"① 제목만: {len(groups1)}개 그룹")
        log(f"② 제목+요약: {len(groups2)}개 그룹")

        send_result_email()
    except Exception as e:
        import traceback
        log(f"❌ 오류: {traceback.format_exc()}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
