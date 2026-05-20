"""
네이버 카페 부정여론 모니터링 (다중 카페)
- 24시간 내 게시글 중 키워드(한국투자증권/한투/뱅키스/BanKIS) 탐지
- Claude AI로 부정 뉘앙스 분석 및 요약
- 부정 탐지 시 담당자 이메일 발송
- 탐지 없음 / 오류 시 발신자 전용 상태 이메일 발송
"""

import os
import json
import html
import sqlite3
import smtplib
import traceback
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
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]

COOKIE_FILE = "naver_cookies.json"
DB_FILE     = "seen_posts.db"

# ─────────────────────────────────────────
# 카페 설정 (다중 카페)
# ─────────────────────────────────────────

CAFES = [
    {"id": "likeusstock", "name": "미국주식이 미래다"},
    {"id": "vilab",       "name": "가치투자연구소"},
    {"id": "yamizal",     "name": "미주미(야미잘)"},
]

KEYWORDS    = ["한국투자증권", "한투", "뱅키스", "BanKIS"]
TIME_WINDOW = 24   # 탐지 범위 (시간)

# ─────────────────────────────────────────
# 부정 강도 판단 기준 (score 0~10)
# ─────────────────────────────────────────
# 0~3 : 단순 언급 / 중립 / 경미한 불만 → 알림 미발송
# 4~6 : 명확한 불만·비판·피해 호소      → 알림 발송
# 7~10: 강한 비판·확산 가능성 높음      → 알림 발송 (긴급)
SCORE_THRESHOLD = 1   # 이 값 이상일 때만 담당자 알림 발송

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────
# 로그 유틸
# ─────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {msg}")

# ─────────────────────────────────────────
# DB (중복 방지)
# ─────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_posts (
            post_id TEXT PRIMARY KEY,
            seen_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def is_new(post_id):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT 1 FROM seen_posts WHERE post_id=?", (post_id,)).fetchone()
    conn.close()
    return row is None

def mark_seen(post_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO seen_posts VALUES (?,?)",
                 (post_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────
# 상태 이메일 (발신자 전용: 탐지 없음 / 오류)
# ─────────────────────────────────────────

def send_status_email(status, detail=""):
    """
    발신자(GMAIL_USER)에게만 발송하는 운영 상태 알림
    status: "no_result" | "error"
    """
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")

    if status == "no_result":
        subject = f"[모니터링] 네이버 카페 부정여론 탐지봇 | 탐지 없음 - {now_str}"
        body_txt = f"정상 실행되었으나 부정 탐지 게시글이 없습니다.\n\n실행 시각: {now_str} KST"
        color    = "#2e7d32"
        title    = "탐지 없음 - 정상 실행"
    else:
        subject  = f"[모니터링 오류] 네이버 카페 부정여론 탐지봇 | {now_str}"
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
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER   # 발신자에게만
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    log(f"상태 이메일 발송 ({status}) → {GMAIL_USER}")

# ─────────────────────────────────────────
# 게시글 목록 수집
# ─────────────────────────────────────────

def parse_date(date_str):
    now = datetime.now(KST)
    try:
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
            d = datetime.strptime(date_str.strip(), "%Y.%m.%d")
            # 날짜만 있는 경우 당일 현재 시각으로 처리 (cutoff 오판 방지)
            return d.replace(hour=now.hour, minute=now.minute, tzinfo=KST)
    except Exception:
        pass
    return now

def get_post_list(page, cafe_id):
    url = f"https://cafe.naver.com/{cafe_id}"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)   # 5초로 증가

    # iframe 접근
    frame = None
    for f in page.frames:
        if cafe_id in f.url and f.url != url and "about:blank" not in f.url:
            frame = f
            break
    if not frame:
        for f in page.frames:
            if "ArticleList" in f.url or "articles" in f.url:
                frame = f
                break
    # iframe 못 찾으면 추가 대기 후 재시도
    if not frame or "about:blank" in frame.url:
        log("iframe 미감지 - 추가 대기 후 재시도")
        page.wait_for_timeout(5000)
        for f in page.frames:
            if cafe_id in f.url and "about:blank" not in f.url:
                frame = f
                break
    if not frame or "about:blank" in frame.url:
        frame = page

    log(f"접속 frame: {frame.url[:80]}")

    # 디버깅: 전체 HTML 저장
    try:
        with open(f"debug_{cafe_id}.html", "w", encoding="utf-8") as f:
            f.write(frame.content())
        log(f"디버그 HTML 저장 완료: debug_{cafe_id}.html")
    except Exception as e:
        log(f"디버그 HTML 저장 실패: {e}")

    cutoff = datetime.now(KST) - timedelta(hours=TIME_WINDOW)
    posts  = []

    rows = frame.query_selector_all("tr.article")
    if not rows:
        rows = frame.query_selector_all("[data-article-id]")
    if not rows:
        rows = frame.query_selector_all(".article-board-list tr")
    if not rows:
        rows = frame.query_selector_all(".board-list li")
    if not rows:
        rows = frame.query_selector_all(".cafe-board-list tr")

    log(f"게시글 행 {len(rows)}개 감지")

    for row in rows[:100]:
        try:
            title_el = (row.query_selector("a.article") or
                        row.query_selector("td.td_article a"))
            date_el  = (row.query_selector("td.td_date") or
                        row.query_selector(".date"))
            if not title_el:
                continue

            title     = title_el.inner_text().strip()
            href      = title_el.get_attribute("href") or ""
            post_id   = (href.split("articleid=")[-1].split("&")[0]
                         if "articleid=" in href
                         else href.split("/")[-1].split("?")[0])
            date_str  = date_el.inner_text().strip() if date_el else ""
            post_time = parse_date(date_str)

            if post_time < cutoff:
                continue

            posts.append({
                "post_id":   post_id,
                "title":     title,
                "url":       f"https://cafe.naver.com/{cafe_id}/{post_id}",
                "post_time": post_time,
            })
        except Exception as e:
            log(f"목록 파싱 오류: {e}")

    log(f"{TIME_WINDOW}시간 이내 게시글 {len(posts)}건 수집")
    return posts

# ─────────────────────────────────────────
# 게시글 본문 + 지표 수집
# ─────────────────────────────────────────

def get_post_detail(page, post_url, cafe_id):
    """본문 텍스트 + 조회수·댓글수·공감수 반환"""
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    frame = None
    for f in page.frames:
        if "ArticleRead" in f.url or cafe_id in f.url:
            frame = f
            break
    if not frame:
        frame = page.frames[-1] if len(page.frames) > 1 else page

    # 본문
    body = ""
    try:
        body_el = (frame.query_selector(".se-main-container") or
                   frame.query_selector("#tbody") or
                   frame.query_selector(".article_body"))
        if body_el:
            body = body_el.inner_text().strip()[:2000]
    except Exception as e:
        log(f"본문 파싱 오류: {e}")

    if not body:
        log("본문 비어 있음 - 제목만으로 AI 분석 진행")

    # 조회수
    views = "-"
    try:
        for sel in [".article_header .count", ".ArticleViewArea .body_info .view_count",
                    "em.visited", ".article-view-info .count_info",
                    "span.count", ".article_view .right_area strong"]:
            el = frame.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace(",", "")
                if txt.isdigit():
                    views = f"{int(txt):,}"
                    break
    except Exception:
        pass

    # 댓글수
    comments = "-"
    try:
        for sel in [".ArticleCommentView .comment_count strong",
                    ".cmt_info .count", ".comment_area .count_comment",
                    "span.count_comment", ".CommentCount"]:
            el = frame.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace(",", "")
                if txt.isdigit():
                    comments = f"{int(txt):,}"
                    break
    except Exception:
        pass

    # 공감수
    likes = "-"
    try:
        for sel in [".sympathy_area .num", ".like_count strong",
                    ".btn_like .count", ".ArticleTool .count_like",
                    "em.u_cnt._count"]:
            el = frame.query_selector(sel)
            if el:
                txt = el.inner_text().strip().replace(",", "")
                if txt.isdigit():
                    likes = f"{int(txt):,}"
                    break
    except Exception:
        pass

    log(f"지표 수집 완료 - 조회:{views} 댓글:{comments} 공감:{likes}")
    return body, views, comments, likes

# ─────────────────────────────────────────
# Claude AI 감성 분석
# ─────────────────────────────────────────

def analyze_sentiment(title, body, keyword):
    prompt = f"""다음은 네이버 카페 게시글입니다.
이 게시글에서 '{keyword}'에 대한 내용이 부정적인지 분석하고, 3줄 이내로 요약해주세요.

[제목]
{title}

[본문]
{body[:1500] if body else "(본문 없음 - 제목만으로 판단)"}

분석 기준:
- 불만, 비판, 욕설, 피해 사례, 불신, 부정적 경험 → 부정 (is_negative: true)
- 단순 언급, 중립 정보, 긍정적 내용 → 부정 아님 (is_negative: false)

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"is_negative": true or false, "summary": "게시글 내용을 3줄 이내 요약 (객관적 서술체)", "score": 0~10}}
score는 부정 강도 (0=전혀 부정 아님, 10=매우 부정적)"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        text = response.json()["content"][0]["text"].strip()
        if "```" in text:
            text = text.split("```")[1].replace("json", "").strip()
        return json.loads(text)
    except Exception as e:
        log(f"AI 분석 오류: {e}")
        return {"is_negative": False, "summary": "분석 실패", "score": 0}

# ─────────────────────────────────────────
# 이메일 발송 (담당자용)
# ─────────────────────────────────────────

def build_card(post, idx, total):
    title    = post["title"]
    url      = post["url"]
    keyword  = post["matched_kw"]
    score    = post["score"]
    summary  = post["summary"]
    views    = post.get("views", "-")
    comments = post.get("comments", "-")
    likes    = post.get("likes", "-")
    post_dt  = post.get("post_time")
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

          <table cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-size:10px;font-weight:bold;color:#bbb;padding-right:4px;">조회</td>
              <td style="font-size:12px;font-weight:bold;color:#222;padding-right:8px;">{views}</td>
              <td style="color:#e0e0e0;padding-right:8px;">|</td>
              <td style="font-size:10px;font-weight:bold;color:#bbb;padding-right:4px;">댓글</td>
              <td style="font-size:12px;font-weight:bold;color:#c62828;padding-right:8px;">{comments}</td>
              <td style="color:#e0e0e0;padding-right:8px;">|</td>
              <td style="font-size:10px;font-weight:bold;color:#bbb;padding-right:4px;">공감</td>
              <td style="font-size:12px;font-weight:bold;color:#222;">{likes}</td>
            </tr>
          </table>

        </td>
      </tr>
    </table>
    <!--[if true]></td></tr></table><![endif]-->"""


def send_alert_batch(alert_posts, crawled_count, keyword_count):
    """탐지 게시글 담당자 이메일 발송 (아웃룩/Gmail/모바일 호환)"""
    total    = len(alert_posts)
    keywords = list({p["matched_kw"] for p in alert_posts})
    kw_str   = "·".join(keywords)
    now_str  = datetime.now(KST).strftime("%Y.%m.%d %H:%M")

    now_kst  = datetime.now(KST)
    subject  = f"(eBiz본부) 부정여론 탐지봇_{now_kst.strftime('%m')}월{now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준 {total}건"
    banner_badge = f"AI 부정여론 탐지 · {total}건"
    banner_title = f"네이버 카페 부정 언급 {total}건 탐지"

    cards_html = "".join(build_card(p, i+1, total) for i, p in enumerate(alert_posts))

    html = f"""<!DOCTYPE html>
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
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
    log(f"담당자 이메일 발송 완료 - {total}건")

# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    init_db()
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
                cafe_name = cafe["name"]
                log(f"\n── {cafe_name} ({cafe_id}) ──")

                # STEP 1: 목록 수집
                posts = get_post_list(page, cafe_id)

                # STEP 2: 신규 여부 확인
                new_posts = [p for p in posts if is_new(p["post_id"])]
                log(f"신규 게시글: {len(new_posts)}건")
                total_crawled += len(new_posts)

                # STEP 3: 본문 수집 + 키워드 탐지 + AI 분석
                for post in new_posts:

                    # 제목 키워드 먼저 확인 (대소문자 무시)
                    matched = next((kw for kw in KEYWORDS if kw.lower() in post["title"].lower()), None)

                    # 본문 + 지표 수집
                    body, views, comments, likes = get_post_detail(page, post["url"], cafe_id)

                    # 본문 키워드 확인 (제목 미매칭 시, 대소문자 무시)
                    if not matched:
                        matched = next((kw for kw in KEYWORDS if kw.lower() in body.lower()), None)

                    # 키워드 미매칭 → DB 기록 후 스킵
                    if not matched:
                        mark_seen(post["post_id"])
                        continue

                    total_keywords += 1
                    log(f"키워드 [{matched}] 탐지: {post['title'][:40]}...")
                    result = analyze_sentiment(post["title"], body, matched)
                    log(f"AI 결과 - 부정:{result['is_negative']} | 강도:{result['score']}/10")

                    # AI 분석까지 완료 후 DB 기록
                    mark_seen(post["post_id"])

                    # score >= SCORE_THRESHOLD 인 경우만 알림 대상
                    if result["is_negative"] and result["score"] >= SCORE_THRESHOLD:
                        post["cafe_name"]  = cafe_name
                        post["matched_kw"] = html.escape(matched)
                        post["score"]      = result["score"]
                        post["summary"]    = html.escape(result["summary"])
                        post["title"]      = html.escape(post["title"])
                        post["views"]      = views
                        post["comments"]   = comments
                        post["likes"]      = likes
                        all_alerts.append(post)

            browser.close()

            # STEP 4: 결과에 따라 이메일 분기
            log(f"\n크롤링 {total_crawled}건 → 키워드 탐지 {total_keywords}건 → AI 필터링 {len(all_alerts)}건")
            if all_alerts:
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
