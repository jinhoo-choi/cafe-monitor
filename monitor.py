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
    {"id": "likeusstock", "num_id": "28497937", "name": "미국주식이 미래다"},
    {"id": "vilab",       "num_id": "11525920", "name": "가치투자연구소"},
    {"id": "yamizal",     "num_id": "30676048", "name": "미주미(야미잘)"},
]

KEYWORDS    = ["한국투자증권", "한투", "뱅키스", "BanKIS"]
TIME_WINDOW = 48   # 탐지 범위 (시간)

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
            clean = date_str.strip().rstrip(".")
            d = datetime.strptime(clean, "%Y.%m.%d")
            # 날짜만 있는 경우 23:59로 처리 (해당 날 가장 늦은 시각 → cutoff 판정 보수적)
            return d.replace(hour=23, minute=59, second=59, tzinfo=KST)
    except Exception:
        pass
    return now

def search_keyword(page, cafe_id, num_id, keyword):
    """카페 인카페 검색으로 키워드 포함 게시글 수집 (24시간 이내)"""
    # 인카페 검색 URL (실제 확인된 구조)
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q={encoded}"
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(4000)

    log(f"  검색: [{keyword}] → {url[:80]}")

    cutoff = datetime.now(KST) - timedelta(hours=TIME_WINDOW)
    posts  = []

    all_rows = page.query_selector_all("table.article-table tbody tr")
    rows = [r for r in all_rows if "board-notice" not in (r.get_attribute("class") or "")]
    log(f"  검색 결과 {len(rows)}건")

    # 첫 번째 행에서 날짜 셀렉터 확인용 디버그
    if rows:
        try:
            import os
            debug_path = f"debug_search_{cafe_id}.html"
            if not os.path.exists(debug_path):
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
        except Exception:
            pass

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

            date_str  = date_el.inner_text().strip() if date_el else ""

            # 날짜 없으면 오늘로 처리 (검색결과는 최신글 위주라 통과시킴)
            if not date_str:
                post_time = datetime.now(KST)
            else:
                post_time = parse_date(date_str)

            # 24시간 초과 게시글은 중단 (검색결과는 최신순)
            if post_time < cutoff:
                break

            posts.append({
                "post_id":   post_id,
                "title":     title,
                "url":       f"https://cafe.naver.com/f-e/cafes/{num_id}/articles/{post_id}",
                "post_time": post_time,
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
    """본문 텍스트 + 조회수·댓글수·공감수 반환"""
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
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp = response.json()
        if "error" in resp:
            log(f"AI API 오류: {resp['error'].get('message','')}")
            return {"is_negative": False, "summary": "분석 실패", "score": 0}
        text = resp["content"][0]["text"].strip()
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
                num_id    = cafe["num_id"]
                cafe_name = cafe["name"]
                log(f"\n── {cafe_name} ({cafe_id}) ──")

                # STEP 1: 목록 수집
                posts = get_post_list(page, cafe_id, num_id)

                # STEP 2: 신규 여부 확인
                new_posts = [p for p in posts if is_new(p["post_id"])]
                log(f"신규 게시글: {len(new_posts)}건")
                total_crawled += len(new_posts)

                # STEP 3: 본문 수집 + AI 분석 (검색으로 이미 키워드 필터됨)
                for post in new_posts:

                    # 검색 시 매칭된 키워드 사용 (없으면 제목에서 재확인)
                    matched = post.get("keyword") or next(
                        (kw for kw in KEYWORDS if kw.lower() in post["title"].lower()), None
                    )
                    if not matched:
                        mark_seen(post["post_id"])
                        continue

                    total_keywords += 1
                    log(f"키워드 [{matched}] 탐지: {post['title'][:40]}...")

                    # 본문 + 지표 수집
                    body = get_post_detail(page, post["url"], cafe_id)
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
