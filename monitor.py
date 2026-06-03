"""
네이버 카페 부정여론 모니터링 (다중 카페)
- 24시간 내 게시글 중 키워드(한국투자증권/한투/뱅키스/BanKIS) 탐지
- Claude AI로 부정 뉘앙스 분석 및 요약
- 부정 강도 1 이상 게시글 담당자 이메일 발송
- 탐지 없음 / 오류 시 발신자 전용 상태 이메일 발송
"""

import os
import json
import html
import smtplib
import traceback
import re
import time
import urllib.parse
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────
# 환경변수 (GitHub Secrets)
# ─────────────────────────────────────────

GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_APP_PW   = os.environ["GMAIL_APP_PW"]
NOTIFY_EMAIL   = os.environ["NOTIFY_EMAIL"]
RECIPIENTS     = [r.strip() for r in NOTIFY_EMAIL.split(",") if r.strip()]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
CLAUDE_MODEL   = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

COOKIE_FILE = "naver_cookies.json"
# ─────────────────────────────────────────
# 카페 설정 (다중 카페)
# ─────────────────────────────────────────

CAFES = [
    {"id": "likeusstock", "num_id": "28497937", "name": "미국주식이 미래다"},
    {"id": "vilab",       "num_id": "11525920", "name": "가치투자연구소"},
    {"id": "yamizal",     "num_id": "30676048", "name": "미국 주식에 미치다"},
    {"id": "geobuk2",     "num_id": "26251287", "name": "거북이 투자법"},
    {"id": "ustock",      "num_id": "15112066", "name": "평생주식카페"},
    {"id": "onepieceholicplus", "num_id": "22290117", "name": "월급쟁이 재테크 연구카페"},
]

KEYWORDS    = ["한국투자증권", "한투", "뱅키스", "BanKIS"]
DB_FILE      = "seen_posts.json"
TIME_WINDOW = 24   # 탐지 범위 (시간) - 24시간 기준, 일 1회 발송

# ─────────────────────────────────────────
# 부정 강도 판단 기준 (score 0~10)
# ─────────────────────────────────────────
# 0~3 : 단순 언급 / 중립 / 경미한 불만
# 4~6 : 명확한 불만·비판·피해 호소
# 7~10: 강한 비판·확산 가능성 높음
# ※ SCORE_THRESHOLD 이상인 경우만 담당자 알림 발송
SCORE_THRESHOLD = 1   # 부정 강도 이 값 이상인 경우만 알림 발송
MAX_ALERTS     = 30   # 이메일 발송 최대 건수 제한
NEGATIVE_HINTS = [   # AI 호출 전 룰필터 - 하나라도 있으면 Claude 분석 진행
    # 감성 표현
    "불만", "먹통", "최악", "탈출", "화남", "짜증", "안됨", "안돼",
    "피해", "피해자", "사기", "민원", "환불", "차단", "거부",
    # 증권사 서비스 관련
    "오류", "에러", "버그", "장애", "지연", "먹힘",
    "체결", "매도", "매수", "출금", "접속", "로그인",
    "주문", "HTS", "MTS", "고객센터", "실패", "취소",
]

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────
# 로그 유틸
# ─────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {msg}")

# ─────────────────────────────────────────
# 상태 이메일 (발신자 전용: 탐지 없음 / 오류)
# ─────────────────────────────────────────

def send_status_email(status, detail=""):
    """
    발신자(GMAIL_USER)에게만 발송하는 운영 상태 알림
    status: "no_result" | "error"
    """
    now_kst = datetime.now(KST)
    now_str = now_kst.strftime("%Y.%m.%d %H:%M")

    if status == "no_result":
        subject = f"[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 {now_kst.strftime('%M')}분 기준 | 탐지 없음"
        body_txt = f"정상 실행되었으나 부정 탐지 게시글이 없습니다.\n\n실행 시각: {now_str} KST"
        color    = "#2e7d32"
        title    = "탐지 없음 - 정상 실행"
    else:
        subject  = f"[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 {now_kst.strftime('%M')}분 기준 | 오류"
        body_txt = f"오류가 발생했습니다.\n\n{detail}"
        color    = "#b71c1c"
        title    = "실행 오류 발생"

    html_body = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<!--[if mso]>
<style>body,table,td{{font-family:'Malgun Gothic',sans-serif!important;}}</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f2f5;">
  <tr><td align="center" style="padding:32px 16px;">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="max-width:600px;background:#fff;border:1px solid #d5d9e0;">
      <tr>
        <td style="background:{color};padding:20px 28px;">
          <p style="margin:0 0 4px 0;font-size:16px;font-weight:bold;color:#fff;">{title}</p>
          <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.7);">
            네이버 카페 부정여론 탐지봇 &nbsp;&middot;&nbsp; {now_str} KST
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:24px 28px;font-size:13px;color:#555;line-height:1.8;">
          <pre style="font-family:inherit;white-space:pre-wrap;margin:0;">{detail if detail else body_txt}</pre>
        </td>
      </tr>
      <tr>
        <td style="background:#f8f8f8;border-top:1px solid #ececec;
                   padding:12px 20px;text-align:center;
                   font-size:11px;color:#aaa;line-height:1.8;">
          탐지 키워드 : 한국투자증권, 한투, 뱅키스<br>
          담당자 : 최진후 차장
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"eBiz 부정여론봇 <{GMAIL_USER}>"
    msg["To"]      = GMAIL_USER
    msg["Date"]    = now_kst.strftime("%a, %d %b %Y %H:%M:%S +0900")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    log(f"상태 이메일 발송 ({status}) → {GMAIL_USER}")

# ─────────────────────────────────────────
# DB (중복 방지)
# ─────────────────────────────────────────

def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

def _load_db():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_new(unique_id):
    return unique_id not in _load_db()

def mark_seen(unique_id):
    data = _load_db()
    data[unique_id] = datetime.now().isoformat()
    _save_db(data)

def cleanup_db(days=30):
    """30일 지난 항목 자동 삭제"""
    data = _load_db()
    cutoff = datetime.now() - timedelta(days=days)
    cleaned = {k: v for k, v in data.items()
               if datetime.fromisoformat(v) > cutoff}
    if len(cleaned) < len(data):
        _save_db(cleaned)
        log(f"DB 정리: {len(data) - len(cleaned)}건 삭제 ({len(cleaned)}건 유지)")

def parse_date(date_str):
    now = datetime.now(KST)
    try:
        if date_str == "방금":
            return now
        if date_str == "어제":
            return now - timedelta(days=1)
        if "분 전" in date_str:
            return now - timedelta(minutes=int(date_str.replace("분 전", "").strip()))
        if "시간 전" in date_str:
            return now - timedelta(hours=int(date_str.replace("시간 전", "").strip()))
        if ":" in date_str:
            t = datetime.strptime(date_str, "%H:%M")
            parsed = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            # 미래 시각이면 전날로 처리
            if parsed > now:
                parsed -= timedelta(days=1)
            return parsed
        if "." in date_str:
            clean = date_str.strip().rstrip(".")
            d = datetime.strptime(clean, "%Y.%m.%d")
            # 날짜만 있는 경우 00:00으로 처리 (당일 자정 기준 → 3시간 이내 엄격 필터)
            return d.replace(hour=0, minute=0, second=0, tzinfo=KST)
    except Exception:
        pass
    return now

def search_keyword(page, cafe_id, num_id, keyword):
    """카페 인카페 검색으로 키워드 포함 게시글 수집 (24시간 이내)"""
    # 인카페 검색 URL (실제 확인된 구조)
    encoded = urllib.parse.quote(keyword)
    url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q={encoded}"
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(4000)

    log(f"  검색: [{keyword}] → {url[:80]}")

    cutoff = datetime.now(KST) - timedelta(hours=TIME_WINDOW)
    posts  = []

    # 셀렉터 fallback (네이버 FE 구조 변경 대응)
    rows = []
    for sel in ["table.article-table tbody tr", ".article-board tbody tr", ".ArticleItem"]:
        all_rows = page.query_selector_all(sel)
        if all_rows:
            rows = [r for r in all_rows if "board-notice" not in (r.get_attribute("class") or "")]
            break
    log(f"  검색 결과 {len(rows)}건")

    for row in rows:
        try:
            title_el = row.query_selector("a.article")
            date_el  = row.query_selector("td.td_normal.type_date")
            if not title_el:
                continue

            title = title_el.inner_text().strip()
            if not title:
                continue

            href = title_el.get_attribute("href") or ""
            if "/articles/" in href:
                post_id = href.split("/articles/")[-1].split("?")[0]
            elif "articleid=" in href:
                post_id = href.split("articleid=")[-1].split("&")[0]
            else:
                post_id = href.rstrip("/").split("/")[-1].split("?")[0]

            if not post_id or not post_id.isdigit():
                continue

            date_str = ""
            # td[3]이 날짜 칸 (0:번호, 1:제목, 2:작성자, 3:날짜, 4:조회수)
            all_tds = row.query_selector_all("td")
            if len(all_tds) >= 4:
                candidate = all_tds[3].inner_text().strip().split("\n")[0].strip()
                if re.match(r"^(\d{1,2}:\d{2}|\d{4}\.\d{2}\.\d{2}\.?|\d+분 전|\d+시간 전|방금|어제)$", candidate):
                    date_str = candidate.rstrip(".")
            # 못 찾으면 전체 순회
            if not date_str:
                for td in all_tds:
                    candidate = td.inner_text().strip().split("\n")[0].strip()
                    if re.match(r"^(\d{1,2}:\d{2}|\d{4}\.\d{2}\.\d{2}\.?|\d+분 전|\d+시간 전|방금|어제)$", candidate):
                        date_str = candidate.rstrip(".")
                        break

            if not date_str:
                post_time = datetime.now(KST)
            else:
                post_time = parse_date(date_str)

            # cutoff 초과 스킵
            if post_time < cutoff:
                continue

            posts.append({
                "post_id":   post_id,
                "title":     title,
                "url":       f"https://cafe.naver.com/f-e/cafes/{num_id}/articles/{post_id}",
                "post_time": post_time,
                "date_str":  date_str,
                "keyword":   keyword,
            })
        except Exception as e:
            log(f"  파싱 오류: {e}")

    return posts


def get_post_list(page, cafe_id, num_id):
    """키워드별 검색으로 게시글 수집 (전체 목록 스캔 불필요)"""
    all_posts = {}  # post_id 기준 중복 제거

    for keyword in KEYWORDS:
        results = search_keyword(page, cafe_id, num_id, keyword)
        for p in results:
            pid = p["post_id"]
            if pid not in all_posts:
                all_posts[pid] = p
            else:
                # 이미 있으면 키워드만 추가
                existing_kw = all_posts[pid].get("keyword", "")
                if keyword not in existing_kw:
                    all_posts[pid]["keyword"] = existing_kw + ", " + keyword

    posts = list(all_posts.values())
    log(f"키워드 검색 완료: {len(posts)}건 수집 (중복 제거)")
    return posts

# ─────────────────────────────────────────
# 게시글 본문 + 지표 수집
# ─────────────────────────────────────────

def get_post_detail(page, post_url, cafe_id):
    """본문 텍스트 반환"""
    # iframe 내부 URL로 직접 접근 (ca-fe 도메인)
    # post_url: https://cafe.naver.com/f-e/cafes/{num_id}/articles/{articleId}
    # → iframe src: https://cafe.naver.com/ca-fe/cafes/{num_id}/articles/{articleId}?fromNext=true
    inner_url = post_url.replace("/f-e/cafes/", "/ca-fe/cafes/") + "?fromNext=true"
    try:
        page.goto(inner_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception:
        # 타임아웃 시 현재 상태로 진행
        page.wait_for_timeout(1000)

    frame = page

    # 본문 셀렉터 (새 FE 구조)
    body = ""
    try:
        body_el = (frame.query_selector(".se-main-container") or
                   frame.query_selector(".ArticleContentBox") or
                   frame.query_selector(".article-viewer") or
                   frame.query_selector(".ContentRenderer") or
                   frame.query_selector("#tbody") or
                   frame.query_selector(".article_body") or
                   frame.query_selector(".se-component-content"))
        if body_el:
            body = body_el.inner_text().strip()[:2000]
    except Exception as e:
        log(f"본문 파싱 오류: {e}")

    if not body:
        log("본문 비어 있음 - 제목만으로 AI 분석 진행")

    return body

# ─────────────────────────────────────────
# Claude AI 감성 분석
# ─────────────────────────────────────────

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

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"is_negative": true or false, "summary": "게시글 내용을 3줄 이내 요약 (객관적 서술체)", "score": 0~10, "reply": "추천 대응 답변"}}
score는 부정 강도 (0=전혀 부정 아님, 10=매우 부정적)
reply는 카페에서 친한 투자 선배가 댓글 달아주는 느낌으로. 2~3문장. 존댓말이지만 편하게. 어려운 말 쓰지 말고 핵심만. 불만이면 간단히 공감하고 해결방향 한 줄, 질문이면 아는 범위에서 짧게 답변. 대응이 불필요한 경우(한국투자증권 귀책 아닌 개인 투자 손실, 단순 중립 언급 등)는 그 이유를 짧게 작성 (예: "개인 투자 손실로 한국투자증권 귀책 없음", "단순 정보 공유로 대응 불필요")."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        resp = response.json()
        if "error" in resp:
            err_type = resp["error"].get("type", "")
            if err_type == "overloaded_error":
                log("AI Overloaded - 10초 후 재시도")
                time.sleep(10)
                response = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": CLAUDE_MODEL, "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]},
                    timeout=30,
                )
                response.raise_for_status()
                resp = response.json()
                if "error" in resp:
                    log(f"AI 재시도 실패: {resp['error'].get('message','')}")
                    return {"is_negative": False, "summary": "분석 실패", "score": 0, "reply": ""}
            else:
                log(f"AI API 오류: {resp['error'].get('message','')}")
                return {"is_negative": False, "summary": "분석 실패", "score": 0, "reply": ""}
        text = resp["content"][0]["text"].strip()
        # JSON 블록 추출 (설명 텍스트 섞여도 안전하게)
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise ValueError("JSON 응답 없음")
        return json.loads(match.group())
    except Exception as e:
        log(f"AI 분석 오류: {e}")
        return {"is_negative": False, "summary": "분석 실패", "score": 0, "reply": ""}

# ─────────────────────────────────────────
# 이메일 발송 (담당자용)
# ─────────────────────────────────────────

def build_card(post, idx, total):
    title    = html.escape(post["title"])
    url      = html.escape(post["url"], quote=True)
    keyword  = html.escape(post["matched_kw"])
    score    = post["score"]
    summary  = html.escape(post["summary"])
    reply    = html.escape(post.get("reply", ""))
    post_dt   = post.get("post_time")
    raw_date  = post.get("date_str", "")
    # 원본 날짜 문자열 우선 표시
    # raw_date 원본 문자열 그대로 표시 (parse_date가 이미 KST 기준이므로 변환 불필요)
    if raw_date and re.match(r"^\d{1,2}:\d{2}$", raw_date):
        # 시간만 있는 경우 → 날짜는 post_time에서 직접 strftime (변환 없이)
        if post_dt:
            date_str = post_dt.strftime("%Y.%m.%d ") + raw_date
        else:
            date_str = datetime.now(KST).strftime("%Y.%m.%d ") + raw_date
    elif raw_date:
        date_str = raw_date
    else:
        date_str = post_dt.strftime("%Y.%m.%d %H:%M") if post_dt else "-"

    return f"""
    <!--[if true]><table width="100%" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-bottom:1px solid #f0f0f0;">
      <tr>
        <td style="padding:20px 28px;">

          <p style="margin:0 0 8px 0;font-size:10px;font-weight:bold;
                    color:#c62828;letter-spacing:0.6px;">게시글 {idx} / {total} &nbsp;·&nbsp; {post.get('cafe_name','')}</p>

          <table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
            <tr>
              <td style="background:#fdecea;padding:3px 10px;
                         font-size:11px;font-weight:bold;color:#c62828;">
                탐지 키워드 : {keyword}
              </td>
              <td width="6"></td>
              <td style="background:#fdecea;padding:3px 10px;
                         font-size:11px;font-weight:bold;color:#c62828;">
                부정 강도 : {score}/10
              </td>
            </tr>
          </table>

          <table width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="margin-bottom:12px;">
            <tr>
              <td style="vertical-align:top;">
                <a href="{url}" style="text-decoration:none;color:#111;">
                  <span style="font-size:15px;font-weight:bold;line-height:1.5;">{title}</span>
                </a>
                <span style="font-size:11px;color:#bbb;margin-left:6px;">{date_str}</span>
              </td>
              <td width="60" style="vertical-align:top;text-align:right;padding-top:2px;">
                <a href="{url}"
                   style="font-size:10px;color:#c62828;font-weight:bold;
                          border:1px solid #c62828;padding:2px 6px;
                          text-decoration:none;">↗</a>
              </td>
            </tr>
          </table>

          <table width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="margin-bottom:14px;background:#fafafa;">
            <tr>
              <td width="3" style="background:#e0e0e0;font-size:0;">&nbsp;</td>
              <td style="padding:12px 14px;">
                <p style="margin:0 0 5px 0;font-size:10px;font-weight:bold;
                          color:#c62828;letter-spacing:0.6px;">✦ AI 요약</p>
                <p style="margin:0;font-size:13px;color:#555;line-height:1.8;">{summary}</p>
              </td>
            </tr>
          </table>
          <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;background:#f0f4ff;">
            <tr>
              <td width="3" style="background:#3d5afe;font-size:0;">&nbsp;</td>
              <td style="padding:10px 14px;">
                <p style="margin:0 0 4px 0;font-size:10px;font-weight:bold;color:#3d5afe;letter-spacing:0.6px;">💬 AI 추천 대응</p>
                <p style="margin:0;font-size:13px;color:#444;line-height:1.8;">{reply}</p>
              </td>
            </tr>
          </table>

        </td>
      </tr>
    </table>
    <!--[if true]></td></tr></table><![endif]-->"""


def send_alert_batch(alert_posts, crawled_count, keyword_count):
    """탐지 게시글 담당자 이메일 발송 (아웃룩/Gmail/모바일 호환)"""
    total    = len(alert_posts)
    now_str  = datetime.now(KST).strftime("%Y.%m.%d %H:%M")

    now_kst  = datetime.now(KST)
    subject  = f"[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 {now_kst.strftime('%M')}분 기준"
    banner_badge = f"AI 부정여론 탐지 · {total}건"
    banner_title = f"네이버 카페 부정 언급 {total}건 탐지"

    sorted_posts = sorted(alert_posts, key=lambda x: x.get("score", 0), reverse=True)
    cards_html = "".join(build_card(p, i+1, total) for i, p in enumerate(sorted_posts))

    html_body = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<!--[if mso]>
<style type="text/css">
  body, table, td {{ font-family: 'Malgun Gothic', sans-serif !important; }}
</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f2f5;">
  <tr><td align="center" style="padding:32px 16px;">
    <table width="600" cellpadding="0" cellspacing="0" border="0"
           style="max-width:600px;background:#fff;border:1px solid #d5d9e0;">
      <tr>
        <td style="background:#c62828;padding:20px 28px;">
          <p style="margin:0 0 6px 0;font-size:10px;font-weight:bold;
                    color:#ffcdd2;letter-spacing:0.8px;">{banner_badge}</p>
          <p style="margin:0 0 8px 0;font-size:18px;font-weight:bold;
                    color:#fff;line-height:1.3;">{banner_title}</p>
          <!-- 크롤링 통계 -->
          <table cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-size:10px;color:rgba(255,255,255,0.6);padding-right:4px;">크롤링</td>
              <td style="font-size:12px;font-weight:bold;color:#fff;padding-right:12px;">{crawled_count}건</td>
              <td style="font-size:10px;color:rgba(255,255,255,0.4);padding-right:12px;">▶</td>
              <td style="font-size:10px;color:rgba(255,255,255,0.6);padding-right:4px;">키워드 탐지</td>
              <td style="font-size:12px;font-weight:bold;color:#fff;padding-right:12px;">{keyword_count}건</td>
              <td style="font-size:10px;color:rgba(255,255,255,0.4);padding-right:12px;">▶</td>
              <td style="font-size:10px;color:rgba(255,255,255,0.6);padding-right:4px;">AI 필터링</td>
              <td style="font-size:12px;font-weight:bold;color:#ffcdd2;">{total}건</td>
            </tr>
          </table>
          <p style="margin:6px 0 0;font-size:11px;color:rgba(255,255,255,0.5);">
            {now_str} KST &nbsp;&middot;&nbsp; Claude AI 분석
          </p>
        </td>
      </tr>
      <tr><td style="padding:0;">{cards_html}</td></tr>
      <tr>
        <td style="background:#f8f8f8;border-top:1px solid #ececec;
                   padding:12px 20px;text-align:center;
                   font-size:11px;color:#aaa;line-height:1.8;">
          탐지 키워드 : 한국투자증권, 한투, 뱅키스<br>
          담당자 : 최진후 차장<br>
          <span style="color:#ccc;">Powered by Claude AI · 자동 수집 및 감성 분석</span>
          <br><br>
          <table cellpadding="0" cellspacing="0" border="0" style="margin:8px auto 0;border-collapse:collapse;">
            <tr>
              <td style="font-size:9px;color:#999;padding:0 0 4px 0;text-align:left;letter-spacing:0.5px;">
                ■ 부정 강도 기준 (Claude AI 채점)
              </td>
            </tr>
            <tr>
              <td>
                <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
                  <tr>
                    <td style="background:#4caf50;width:40px;height:8px;font-size:0;">&nbsp;</td>
                    <td style="background:#8bc34a;width:40px;height:8px;font-size:0;">&nbsp;</td>
                    <td style="background:#ffc107;width:40px;height:8px;font-size:0;">&nbsp;</td>
                    <td style="background:#ff9800;width:40px;height:8px;font-size:0;">&nbsp;</td>
                    <td style="background:#f44336;width:40px;height:8px;font-size:0;">&nbsp;</td>
                  </tr>
                  <tr>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">0~1<br>무관</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">2~3<br>경미</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">4~5<br>불만</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">6~7<br>비판</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">8~10<br>긴급</td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"eBiz 부정여론봇 <{GMAIL_USER}>"
    msg["To"]      = ", ".join(RECIPIENTS)
    msg["Date"]    = now_kst.strftime("%a, %d %b %Y %H:%M:%S +0900")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, RECIPIENTS, msg.as_string())
    log(f"담당자 이메일 발송 완료 - {total}건")

# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    init_db()
    cleanup_db()
    log("=" * 48)
    log(f"모니터링 시작 | {len(CAFES)}개 카페")
    log(f"탐지 키워드: {', '.join(KEYWORDS)}")
    log(f"부정 강도 기준: {SCORE_THRESHOLD} 이상 알림 발송")
    log("=" * 48)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # 쿠키 파일 확인 (NAVER_COOKIES_JSON Secret으로 주입됨)
            if not os.path.exists(COOKIE_FILE):
                raise RuntimeError("naver_cookies.json 없음 - NAVER_COOKIES_JSON Secret 확인 필요")

            log("쿠키 파일로 접속")
            context = browser.new_context(storage_state=COOKIE_FILE)
            page    = context.new_page()

            # 로그인 상태 확인
            page.goto("https://www.naver.com", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if "nid.naver.com" in page.url:
                raise RuntimeError("쿠키 만료 - NAVER_COOKIES_JSON Secret 재등록 필요")
            log("로그인 상태 확인 완료")

            total_crawled  = 0
            total_keywords = 0
            all_alerts     = []

            # 카페별 순차 실행
            for cafe in CAFES:
                cafe_id   = cafe["id"]
                num_id    = cafe["num_id"]
                cafe_name = cafe["name"]
                log(f"\n── {cafe_name} ({cafe_id}) ──")

                # STEP 1: 목록 수집
                posts = get_post_list(page, cafe_id, num_id)

                # STEP 2: 신규 여부 확인 (중복 제거)
                new_posts = [p for p in posts if is_new(f"{cafe_id}:{p['post_id']}")]
                log(f"신규 게시글: {len(new_posts)}건")
                total_crawled += len(new_posts)

                # STEP 3: 본문 수집 + AI 분석 (검색으로 이미 키워드 필터됨)
                for post in new_posts:

                    # 검색 시 매칭된 키워드 사용 (없으면 제목에서 재확인)
                    matched = post.get("keyword") or next(
                        (kw for kw in KEYWORDS if kw.lower() in post["title"].lower()), None
                    )
                    if not matched:
                        mark_seen(f"{cafe_id}:{post['post_id']}")
                        continue

                    total_keywords += 1
                    log(f"키워드 [{matched}] 탐지: {post['title'][:40]}...")

                    # 본문 수집
                    body = get_post_detail(page, post["url"], cafe_id)

                    # AI 호출 전 룰필터 (부정 힌트 없으면 Claude 미호출 → 비용 절감)
                    combined_text = (post["title"] + " " + body).lower()
                    has_hint = any(hint in combined_text for hint in NEGATIVE_HINTS)

                    if not has_hint:
                        log(f"  룰필터 통과 - AI 분석 생략 (부정 힌트 없음)")
                        continue  # seen 처리 안 함 → 다음 실행에서 재검토 가능
                    
                    result = analyze_sentiment(post["title"], body, matched)
                    # AI 분석 성공 시만 seen 처리 (실패 시 다음 실행에서 재시도)
                    if result["summary"] != "분석 실패":
                        mark_seen(f"{cafe_id}:{post['post_id']}")
                    log(f"AI 결과 - 부정:{result['is_negative']} | 강도:{result['score']}/10")

                    # 부정 강도 SCORE_THRESHOLD 이상인 경우만 알림 발송
                    post["cafe_name"]  = cafe_name
                    post["matched_kw"] = matched
                    post["score"]      = result["score"]
                    post["summary"]    = result["summary"]
                    post["reply"]      = result.get("reply", "")
                    if result["score"] >= SCORE_THRESHOLD:
                        all_alerts.append(post)

            browser.close()

            # STEP 4: 결과에 따라 이메일 분기
            log(f"\n크롤링 {total_crawled}건 → 키워드 탐지 {total_keywords}건 → AI 필터링 {len(all_alerts)}건")
            if all_alerts:
                all_alerts = sorted(all_alerts, key=lambda x: x.get("score", 0), reverse=True)
                if len(all_alerts) > MAX_ALERTS:
                    log(f"알림 건수 초과 — {len(all_alerts)}건 중 {MAX_ALERTS}건만 발송")
                    all_alerts = all_alerts[:MAX_ALERTS]
                send_alert_batch(
                    all_alerts,
                    crawled_count=total_crawled,
                    keyword_count=total_keywords,
                )
            else:
                send_status_email("no_result")

    except Exception as e:
        err = traceback.format_exc()
        log(f"오류 발생: {e}")
        send_status_email("error", detail=err)
        raise

    log("모니터링 완료")

if __name__ == "__main__":
    main()
