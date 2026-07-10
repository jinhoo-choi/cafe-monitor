"""
test_prompt_regression.py
- '회사 귀책 리스크 뉴스' 기준 추가 후 회귀 테스트
- 1) 기존 이벤트성 게시글이 여전히 score=0인지 (오탐 방지 확인)
- 2) 뉴스형 부정 게시글이 여전히 높은 score로 잡히는지
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
SENTIMENT_MODEL = "claude-sonnet-4-6"

# 실제 monitor.py에 반영한 최종 프롬프트 그대로 사용
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
- 전산장애·수수료 배상·금융감독원 제재/보상 요구 등 한국투자증권 회사 귀책 리스크를 다루는 뉴스·기사 공유 (직접 겪은 불만이 아니어도 회사 신뢰도에 영향 주는 보도는 부정으로 판단, 강도는 사안의 심각성에 비례)

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

# 회귀 테스트 케이스 (실제 과거 로그에서 나온 게시글들)
TEST_CASES = [
    {
        "label": "① 이벤트글 (기존 score=0 이어야 함 - 오탐 방지 확인)",
        "title": "최대 400만원 혜택 지급하는 한국투자증권 금융 상품 이벤트!",
        "body": "",
    },
    {
        "label": "② 단순 질문 (기존 score=0 이어야 함)",
        "title": "카카오뱅크 이용해서 한투하시는 분 계세요? 수수료가 어떻게 되나요?",
        "body": "",
    },
    {
        "label": "③ 회사 귀책 뉴스 (신규 기준 - 높은 score 나와야 함)",
        "title": "한국투자증권, 1분기 전산장애 배상액 1위…잇단 오류에 금감원 보상요구까지",
        "body": "한국투자증권은 2025년 1분기 전산장애 배상액이 8억3천만원으로 13개 증권사 중 최다를 기록했다. 수익률 오표기, 개인정보 오접속 등 최근 한 달 새 전산 오류가 잇따르며 금감원이 보상안 마련을 요구했다. MTS 전산장애가 4년간 11건 발생하는 등 소비자 보호 체계와 전산 신뢰성에 대한 비판이 제기되고 있다.",
    },
    {
        "label": "④ 실제 서비스 불만 (기존 로직 - 여전히 높은 score 나와야 함)",
        "title": "지금 한투 접속되시나요?",
        "body": "한투 앱이 계속 로그인이 안 되네요. 다른 분들도 그런가요?",
    },
    {
        "label": "⑤ 타사 언급 뉴스 (score 낮아야 함 - 한투 관련 없음)",
        "title": "스페이스X 0주 배정 사태, 결국 '사기 혐의' 경찰 내사까지…무슨 일이었나",
        "body": "스페이스X IPO에서 한국 투자자들에게 공모주가 한 주도 배정되지 않은 '코리아 패싱' 사태를 다룬 게시글. 한국투자신탁운용의 ETF 광고 문구 논란 및 금감원 현장검사·경찰 내사 진행 상황을 다룸.",
    },
]

logs = []
def log(msg):
    line = msg
    print(line)
    logs.append(line)

def send_result_email():
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    body = "\n".join(logs)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[프롬프트 회귀테스트] 결과 | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{body}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    try:
        for case in TEST_CASES:
            log(f"\n{'='*55}")
            log(case["label"])
            log(f"제목: {case['title']}")
            result = analyze_sentiment(case["title"], case["body"], "한국투자증권")
            log(f"→ is_negative={result['is_negative']}, score={result['score']}/10")
            log(f"   요약: {result['summary']}")
        send_result_email()
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"❌ 오류: {err}")
        send_result_email()
        raise

if __name__ == "__main__":
    main()
