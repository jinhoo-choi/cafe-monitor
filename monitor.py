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
import random
import time
import urllib.parse
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from email.header import Header
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────
# 환경변수 (GitHub Secrets)
# ─────────────────────────────────────────

GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_APP_PW   = os.environ["GMAIL_APP_PW"]
NOTIFY_EMAIL   = os.environ["NOTIFY_EMAIL"]
RECIPIENTS     = [r.strip() for r in NOTIFY_EMAIL.split(",") if r.strip()]
CC_EMAIL       = os.getenv("EMAIL_CC", "")
CC_RECIPIENTS  = [r.strip() for r in CC_EMAIL.split(",") if r.strip()]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
# 모델 분리 (환경변수로 개별 교체 가능 - 코드 수정 없이 GitHub Secret만 변경)
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", "claude-sonnet-4-6")   # 감성 분석 (핵심)
GROUPING_MODEL  = os.getenv("GROUPING_MODEL",  "claude-sonnet-4-6")   # 유사 이슈 묶음
# 하위호환: 기존 CLAUDE_MODEL 참조 코드가 있으면 감성 모델로 매핑
CLAUDE_MODEL    = SENTIMENT_MODEL

# ─────────────────────────────────────────
# 이메일 From/To 표시명 헤더 생성 (RFC 2047 인코딩)
# ─────────────────────────────────────────
BOT_NAME = "⚠️ eBiz 부정여론봇"


def _from_header() -> str:
    """발신자(From) 표시명 헤더. raw f-string 금지 - formataddr()+Header()로 RFC 2047 인코딩."""
    return formataddr((str(Header(BOT_NAME, "utf-8")), GMAIL_USER))


def _addr_header(addr: str) -> str:
    """임의 주소(addr)에 봇 표시명을 씌운 헤더. To 필드 전용 (본인 계정 고정용)."""
    return formataddr((str(Header(BOT_NAME, "utf-8")), addr))


def _notify_send_failure(context: str, refused: dict):
    """sendmail() 반환값(거부된 수신자)이 있을 때 본인에게 별도 경고 메일 발송.
    smtplib.sendmail()은 일부(심지어 본인 제외 전원)가 거부돼도 예외를 던지지 않고
    반환값으로만 알려주므로, 반환값을 무시하면 무음 실패가 발생함 - 이를 감지하기 위한 안전장치."""
    log(f"⚠️ 일부 수신자 거부됨 [{context}]: {list(refused.keys())}")
    try:
        detail_lines = "".join(
            f"<li>{html.escape(addr)} — {code}: {html.escape(str(msg))}</li>"
            for addr, (code, msg) in refused.items()
        )
        warn_msg = MIMEMultipart("alternative")
        warn_msg["Subject"] = f"🚨 이메일 일부 수신자 거부됨 - {context}"
        warn_msg["From"] = _from_header()
        warn_msg["To"] = _addr_header(GMAIL_USER)
        warn_msg["Date"] = formatdate(localtime=True)
        warn_msg["Message-ID"] = make_msgid(domain="gmail.com")
        warn_msg.attach(MIMEText(
            f"<p>[{html.escape(context)}] 발송 중 일부 수신자가 SMTP 서버에 의해 거부되었습니다.</p><ul>{detail_lines}</ul>",
            "html", "utf-8"
        ))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PW)
            s.sendmail(GMAIL_USER, GMAIL_USER, warn_msg.as_string())
    except Exception as e:
        log(f"⚠️ 거부 알림 메일 발송 자체도 실패: {e}")



COOKIE_FILE = "naver_cookies.json"

# ─────────────────────────────────────────
# 카페 설정 (다중 카페)
# ─────────────────────────────────────────

CAFES = [
    # login_required: 2026-07-13 비로그인 실검증 결과 기반
    #   True  = 회원 전용 열람 카페 (비로그인 시 본문 0자 확인됨)
    #   False = 비로그인 열람 가능 확인됨
    {"id": "likeusstock", "num_id": "28497937", "name": "미국주식이 미래다", "login_required": True},   # 미측정(검색0건) - 안전하게 True
    {"id": "vilab",       "num_id": "11525920", "name": "가치투자연구소", "login_required": True},
    {"id": "yamizal",     "num_id": "30676048", "name": "미국 주식에 미치다", "login_required": True},  # 판정유보 - 안전하게 True
    {"id": "geobuk2",     "num_id": "26251287", "name": "거북이 투자법", "login_required": True},
    {"id": "ustock",      "num_id": "15112066", "name": "평생주식카페", "login_required": False},
    {"id": "onepieceholicplus", "num_id": "22290117", "name": "월급쟁이 재테크 연구카페", "login_required": True},
    {"id": "pointns",          "num_id": "25873056", "name": "주식투자는 달팽이처럼", "login_required": True},
    {"id": "wjdrkrjqn",        "num_id": "30786704", "name": "정가를 거부하는 사람들", "login_required": False},
    {"id": "stocktraining",    "num_id": "29798500", "name": "나는 주식트레이더다", "login_required": True},
    {"id": "dlxogns01",        "num_id": "23676262", "name": "배당 투자자 모임", "login_required": False},
    {"id": "engmstudy",        "num_id": "14028420", "name": "짠돌이카페", "login_required": False},
    {"id": "hayate1",          "num_id": "11560463", "name": "주식광장", "login_required": False},
    {"id": "divclub",          "num_id": "31050378", "name": "은퇴후 50년", "login_required": True},
    {"id": "moneyinsights",    "num_id": "31449216", "name": "돈이 되는 모든 정보", "login_required": False},
]

KEYWORDS    = ["한국투자증권", "한투", "뱅키스"]
DB_FILE      = "seen_posts.json"
TIME_WINDOW = 24   # 탐지 범위 (시간) - 24시간 기준, 일 1회 발송

# ─────────────────────────────────────────
# 부정 강도 판단 기준 (score 0~10)
# ─────────────────────────────────────────
# 0   : 무관 (단순 언급·중립)
# 1~3 : 경미한 불만
# 4~6 : 명확한 불만·비판·피해 호소
# 7~10: 강한 비판·확산 가능성 높음
# ※ SCORE_THRESHOLD 이상인 경우만 담당자 알림 발송
SCORE_THRESHOLD = 1   # 부정 강도 이 값 이상인 경우만 알림 발송
MAX_ALERTS     = 30   # 이메일 발송 최대 건수 제한

NEGATIVE_HINTS = [   # AI 호출 전 룰필터 - 하나라도 있으면 Claude 분석 진행
    # 감성 표현
    "불만", "먹통", "최악", "탈출", "화남", "짜증", "안됨", "안돼",
    "피해", "피해자", "사기", "민원", "환불", "차단", "거부",
    "왜이럼", "왜이러", "미치겠", "황당", "어이없", "답답",
    "이상해", "이상한", "문제", "느려", "버벅", "튕겨", "튕김",
    # 서비스 불만 구어체
    "안되네", "안되는", "안됩니다", "기다리고", "기다림",
    "연결안", "상담원", "대기", "처리안",
    "왜안되", "왜못",
    # 증권사 서비스 장애/오류 관련
    "오류", "에러", "버그", "장애", "먹힘",
    "HTS", "MTS", "고객센터", "실패",
    "계좌개설", "해외계좌",
    # 강한 부정 표현
    "접속불가", "로그인불가", "주문불가", "체결불가",
]

# ─────────────────────────────────────────
# 광고성 게시글 제외 패턴 (AI 호출 전 제목 기준 사전 차단)
# ─────────────────────────────────────────
# 사기 업체가 증권사 브랜드를 도용해 올리는 광고글 유형:
#   "한국투자증권 사기 피해 회복 전 알아야 할 핵심 체크포인트" 등
# → 한국투자증권 자체 서비스 불만이 아닌 외부 광고이므로 부정여론에서 제외
AD_SKIP_PATTERNS = [
    # 사기 피해 회복 광고 유형 (구체적 복합구만 사용 - 단어 단독 사용 시 오탐 위험)
    "사기 피해 회복", "피해 회복 전", "피해금 회복", "피해 복구",
    "알아야 할 핵심 체크포인트", "피해 회복 체크포인트", "알아야 할 핵심",
    # 법률·상담 유도 광고 유형
    "무료 상담", "카카오톡 문의", "텔레그램 문의",
    "피해 전문", "전문 변호사", "법률 상담",
    # 투자 리딩방 광고 유형
    "리딩방", "투자 리딩", "수익 인증",
    # 가계부·생활비 일상 글 (월급쟁이 재테크 카페 등 - 한투 단순 언급)
    "가계부", "생활비", "무지출", "짠테크",
    "알뜰살뜰", "알뜰행복", "알뜰환희", "온스블리",
]

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────
# 네이버 블로그 전용 설정
# ─────────────────────────────────────────
BLOG_BODY_SELECTORS = [
    ".se-main-container",
    "#postViewArea",
    ".post_ct",
    ".se-component-content",
]
BLOG_TITLE_SELECTORS = [
    ".se-title-text",
    ".htitle",
    "h3.se_textarea",
]
BLOG_DATE_SELECTORS = [
    ".blog_date",
    "[class*='date']",
]
# 블로그 특화 광고/노이즈 패턴 (기존 AD_SKIP_PATTERNS에 추가로 적용)
BLOG_AD_SKIP_PATTERNS = [
    "협찬", "체험단", "제공받아", "원고료", "유료광고", "유료 광고",
    "재무설계 상담", "무료 상담 신청", "1:1 상담 신청",
    "TOP10", "TOP5", "완벽정리", "총정리",
]
# 게시글 URL에서 postId 추출 (블로거 프로필 링크와 실제 글 링크 구분용)
BLOG_POST_ID_PATTERN = re.compile(r'/(\d{6,})(\?|$)')

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
        subject = f"⚠️[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준 | 탐지 없음"
        body_txt = f"정상 실행되었으나 부정 탐지 게시글이 없습니다.\n\n실행 시각: {now_str} KST"
        color    = "#2e7d32"
        title    = "탐지 없음 - 정상 실행"
    elif status == "warning":
        subject  = f"[부정여론 탐지] ⚠️ 쿠키 만료 임박 | {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일"
        body_txt = f"쿠키 만료가 임박했습니다. 조치가 필요합니다.\n\n{detail}"
        color    = "#e65100"
        title    = "⚠️ 쿠키 만료 임박 - 재등록 필요"
    else:
        subject  = f"⚠️[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준 | 오류"
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
    msg["From"]    = _from_header()
    msg["To"]      = _addr_header(GMAIL_USER)
    msg["Date"]    = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        refused = s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    if refused:
        _notify_send_failure(f"상태 이메일({status})", refused)
    log(f"상태 이메일 발송 ({status}) → {GMAIL_USER}")

# ─────────────────────────────────────────
# DB (중복 방지)
# ─────────────────────────────────────────

_db_cache = None  # 런타임 메모리 캐시 (파일 I/O 최소화)

def init_db():
    global _db_cache
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    _db_cache = _load_db()  # 시작 시 1회 로드

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
    global _db_cache
    if _db_cache is None:
        _db_cache = _load_db()
    return unique_id not in _db_cache

def mark_seen(unique_id):
    global _db_cache
    if _db_cache is None:
        _db_cache = _load_db()
    _db_cache[unique_id] = datetime.now(KST).isoformat()  # KST로 저장
    _save_db(_db_cache)  # 즉시 파일 동기화 (중간 중단 대비)

def cleanup_db(days=30):
    """30일 지난 항목 자동 삭제"""
    global _db_cache
    data = _db_cache if _db_cache is not None else _load_db()
    cutoff = datetime.now(KST) - timedelta(days=days)
    cleaned = {}
    for k, v in data.items():
        try:
            dt = datetime.fromisoformat(v)
            # naive면 KST로 간주
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            if dt > cutoff:
                cleaned[k] = v
        except Exception:
            pass  # 파싱 불가 항목 제거
    if len(cleaned) < len(data):
        _db_cache = cleaned
        _save_db(cleaned)
        log(f"DB 정리: {len(data) - len(cleaned)}건 삭제 ({len(cleaned)}건 유지)")

def parse_date(date_str):
    """날짜 문자열을 KST aware datetime으로 변환 (GitHub Actions UTC 서버 환경 대응)"""
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
            # 네이버 f-e 검색 목록의 HH:MM은 UTC+9가 이중 적용된 값으로 내려옴
            # (실제 KST 06:53인데 15:53으로 표시됨) → 9시간 빼서 보정
            parsed = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            parsed = parsed - timedelta(hours=9)
            # 미래 시각이면 전날로 처리
            if parsed > now:
                parsed -= timedelta(days=1)
            return parsed
        if "." in date_str:
            clean = date_str.strip().rstrip(".")
            d = datetime.strptime(clean, "%Y.%m.%d")
            return d.replace(hour=0, minute=0, second=0, tzinfo=KST)
    except Exception:
        pass
    return now

def search_keyword(page, cafe_id, num_id, keyword):
    """카페 인카페 검색으로 키워드 포함 게시글 수집 (24시간 이내)"""
    encoded = urllib.parse.quote(keyword)
    # 가계부/절약 카페는 제목 전용 검색으로 오탐 방지
    if cafe_id in ["onepieceholicplus"]:
        url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=TITLE&page=1&q={encoded}"
    else:
        url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q={encoded}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        log(f"  페이지 로드 타임아웃, 재시도: {e}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(random.randint(3000, 6000))

    log(f"  검색: [{keyword}] → {url[:80]}")

    cutoff = datetime.now(KST) - timedelta(hours=TIME_WINDOW)
    posts  = []

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
            all_tds = row.query_selector_all("td")
            if len(all_tds) >= 4:
                candidate = all_tds[3].inner_text().strip().split("\n")[0].strip()
                if re.match(r"^(\d{1,2}:\d{2}|\d{4}\.\d{2}\.\d{2}\.?|\d+분 전|\d+시간 전|방금|어제)$", candidate):
                    date_str = candidate.rstrip(".")
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
    all_posts = {}

    for keyword in KEYWORDS:
        results = search_keyword(page, cafe_id, num_id, keyword)
        for p in results:
            pid = p["post_id"]
            if pid not in all_posts:
                all_posts[pid] = p
            else:
                existing_kw = all_posts[pid].get("keyword", "")
                if keyword not in existing_kw:
                    all_posts[pid]["keyword"] = existing_kw + ", " + keyword

    posts = list(all_posts.values())
    log(f"키워드 검색 완료: {len(posts)}건 수집 (중복 제거)")
    return posts

# ─────────────────────────────────────────
# 네이버 블로그 검색 - 게시글 URL 목록 수집
# ─────────────────────────────────────────

def search_naver_blog(page, keyword):
    """네이버 모바일 블로그 검색 - 최신순 정렬, 게시글 URL만 추출
    검색 결과 카드 템플릿이 최소 2종류 혼재되어 제목·본문을 리스트에서
    파싱하지 않고 URL만 뽑아서 get_blog_detail()로 개별 페이지에서 수집한다.
    로그인 불필요 (공개 검색 페이지).
    """
    encoded = urllib.parse.quote(keyword)
    url = f"https://m.search.naver.com/search.naver?where=m_blog&query={encoded}&sm=mtb_opt&nso=so%3Add%2Cp%3Aall"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        log(f"  블로그 검색 페이지 로드 실패, 재시도: {e}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

    page.wait_for_timeout(random.randint(2000, 3500))
    log(f"  블로그 검색: [{keyword}] → {url[:80]}")

    anchors = page.query_selector_all("a[href*='blog.naver.com']")
    seen_urls = set()
    post_urls = []
    for a in anchors:
        href = a.get_attribute("href") or ""
        m = BLOG_POST_ID_PATTERN.search(href)
        if not m:
            continue  # 블로거 프로필 링크 등 postId 없는 링크 제외
        clean_url = href.split("?")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        post_urls.append({"url": clean_url, "post_id": m.group(1), "keyword": keyword})

    log(f"  블로그 검색 결과 {len(post_urls)}건 (고유 URL)")
    return post_urls


def get_blog_post_list(page, keywords):
    """키워드별 블로그 검색 결과 통합 (카페의 get_post_list와 동일 패턴)"""
    all_posts = {}
    for keyword in keywords:
        results = search_naver_blog(page, keyword)
        for p in results:
            pid = p["post_id"]
            if pid not in all_posts:
                all_posts[pid] = p
            else:
                existing_kw = all_posts[pid].get("keyword", "")
                if keyword not in existing_kw:
                    all_posts[pid]["keyword"] = existing_kw + ", " + keyword
    posts = list(all_posts.values())
    log(f"블로그 키워드 검색 완료: {len(posts)}건 수집 (중복 제거)")
    return posts


def get_blog_detail(page, post_url):
    """블로그 개별 포스트에서 제목/본문/날짜 수집 (로그인 불필요, iframe 없음)
    반환: {"title": str, "body": str, "body_full": str, "date_str": str}
    - body: AI 분석용 (2000자 제한, 비용 절약)
    - body_full: 키워드 실존 검증용 (전체 본문 - 키워드가 후반부에 있어도 탐지)
    """
    result = {"title": "", "body": "", "body_full": "", "date_str": ""}
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(random.randint(1500, 2500))
    except Exception as e:
        log(f"  블로그 본문 페이지 로드 실패: {e}")
        return result

    for sel in BLOG_TITLE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    result["title"] = text
                    break
        except Exception:
            continue

    for sel in BLOG_BODY_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    result["body_full"] = text          # 검증용 전체
                    result["body"] = text[:2000]        # AI 분석용
                    break
        except Exception:
            continue

    for sel in BLOG_DATE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    result["date_str"] = text
                    break
        except Exception:
            continue

    if not result["body"]:
        log(f"  블로그 본문 비어 있음: {post_url}")

    return result

# ─────────────────────────────────────────
# 게시글 본문 수집

# ─────────────────────────────────────────

def get_post_detail(page, post_url, cafe_id):
    """본문 텍스트 반환 - f-e URL로 접근 후 iframe에서 본문 파싱
    반환: (body_2000, body_full) 튜플
    - body_2000: AI 분석용 (2000자 제한)
    - body_full: 키워드 실존 검증용 (전체 본문)"""
    BODY_SELECTORS = [
        ".se-main-container",
        ".ArticleContentBox",
        ".article-viewer",
        ".ContentRenderer",
        "#tbody",
        ".article_body",
        ".se-component-content",
    ]
    SELECTOR_COMBINED = ", ".join(BODY_SELECTORS)

    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        log(f"  본문 페이지 로드 실패 (제목만으로 분석 진행): {e}")
        page.wait_for_timeout(1000)
        return "", ""

    try:
        page.wait_for_selector(SELECTOR_COMBINED, timeout=8000)
    except Exception:
        page.wait_for_timeout(random.randint(1500, 2500))

    body = ""

    # 1순위: ca-fe URL 패턴 iframe 우선 탐색 (실제 본문 렌더링 프레임)
    try:
        for frame in page.frames:
            frame_url = frame.url or ""
            if "ca-fe" in frame_url and "articles" in frame_url:
                for sel in BODY_SELECTORS:
                    try:
                        el = frame.query_selector(sel)
                        if el:
                            body = el.inner_text().strip()
                            if body:
                                break
                    except Exception:
                        continue
                if body:
                    log(f"  iframe(ca-fe) 본문 수집 성공")
                    break
    except Exception as e:
        log(f"  iframe(ca-fe) 접근 오류: {e}")

    # 2순위: naver.com 포함 전체 iframe 탐색 (메인 프레임 및 빈 프레임 제외)
    if not body:
        try:
            for frame in page.frames:
                frame_url = frame.url or ""
                if (
                    "naver.com" in frame_url
                    and frame_url != page.url
                    and frame_url != "about:blank"
                    and "ca-fe" not in frame_url  # 이미 위에서 탐색 완료
                ):
                    for sel in BODY_SELECTORS:
                        try:
                            el = frame.query_selector(sel)
                            if el:
                                body = el.inner_text().strip()
                                if body:
                                    break
                        except Exception:
                            continue
                    if body:
                        log(f"  iframe(fallback) 본문 수집 성공")
                        break
        except Exception as e:
            log(f"  iframe(fallback) 접근 오류: {e}")

    # 3순위: 모든 iframe 대상 탐색 (URL 조건 없이)
    if not body:
        try:
            for frame in page.frames:
                frame_url = frame.url or ""
                if frame_url == "about:blank":
                    continue
                for sel in BODY_SELECTORS:
                    try:
                        el = frame.query_selector(sel)
                        if el:
                            candidate = el.inner_text().strip()
                            if candidate:
                                body = candidate
                                break
                    except Exception:
                        continue
                if body:
                    log(f"  iframe(any) 본문 수집 성공")
                    break
        except Exception as e:
            log(f"  iframe(any) 접근 오류: {e}")

    # 4순위: 현재 page에서 직접 파싱
    if not body:
        try:
            for sel in BODY_SELECTORS:
                el = page.query_selector(sel)
                if el:
                    body = el.inner_text().strip()
                    if body:
                        log(f"  직접 파싱 본문 수집 성공")
                        break
        except Exception as e:
            log(f"  본문 직접 파싱 오류: {e}")

    if not body:
        log("  본문 비어 있음 - 제목만으로 AI 분석 진행")

    return body[:2000], body

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
- 전산장애·수수료 배상·금융감독원 제재/보상 요구 등 한국투자증권(법인 자체) 회사 귀책 리스크를 다루는 뉴스·기사 공유 (직접 겪은 불만이 아니어도 회사 신뢰도에 영향 주는 보도는 부정으로 판단, 강도는 사안의 심각성에 비례). 단, 한국투자신탁운용·한국투자밸류자산운용 등은 한국투자증권과 별개 법인이므로 해당 계열사 이슈는 이 기준에서 제외 (타 증권사 규정과 동일하게 처리)

▶ 부정 아님(is_negative: false)으로 판단할 것:
- 단순 질문·이벤트 문의·정보 공유·증권사 비교
- 시장 전반 불만 또는 타 증권사 불만
- 한국투자증권 단순 언급 (중립·긍정 문맥)
- 일반 투자 손실 (한국투자증권 귀책 아닌 경우)
- 영웅문·키움·미래에셋·삼성증권·NH·신한·KB 등 타 증권사 앱·서비스 문제 (댓글에 한투가 단순 언급된 경우 포함)
- 한국투자신탁운용·한국투자밸류자산운용 등 "한국투자" 계열사 문제 (한국투자증권과 별개 법인이므로 타 증권사와 동일하게 처리)
- 광고성·홍보성 게시글 (사기 피해 회복 안내, 법률 상담 유도, 무료 상담 홍보 등) → score=0
- 한국투자증권 브랜드를 사칭하는 사기꾼 주의 안내글 (한투 자체 서비스 문제 아님) → score=0
- 여러 뉴스·이슈를 단순 나열/정리한 게시글(예: "오늘의 신문 정리")에서 한투 관련 내용이 전체 항목 중 일부로만 언급되고, 글쓴이 자신의 불만·의견이 아닌 단순 뉴스 인용인 경우
- 한투 리서치센터의 목표주가·실적 전망치 조정 등 정상적인 애널리스트 의견 (전산장애·수수료·제재처럼 회사 귀책 사안이 아님)

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"is_negative": true or false, "summary": "게시글 내용을 3줄 이내 요약 (객관적 서술체)", "score": 0~10, "reply": "추천 대응 답변"}}
score는 부정 강도 (0=전혀 부정 아님, 10=매우 부정적). is_negative가 false면 score는 반드시 0으로 응답 (무관·중립 글에 1~2점을 주지 말 것)
reply는 같은 카페 회원이 친근하게 댓글 달아주는 느낌. 2~4문장. 편한 존댓말.
reply 작성 순서: ① 아래 [증권 기본 구조]에 근거해 가장 유력한 원인 1가지 제시 → ② 확인·해결 방법 → ③ 그래도 안 되면 고객센터(1544-5000) 안내. 단순 공감("당황스럽죠", "속상하시겠어요")만 하고 끝내는 감정 케어성 답변 금지. 원인 설명이 반드시 포함되어야 함.

▶ 원인 추정 시 활용할 [증권 기본 구조] (이 범위 안에서만 사실 기반으로 설명):
- 국내 주식은 체결(매매 성립)과 결제(소유권·대금의 실제 이전, T+2영업일)가 분리됨. 매도 체결 후에도 결제 완료 전까지는 앱 화면에 따라(체결 기준 vs 결제 기준) 종목·수량·평가금액이 남아 보이거나 화면 간 불일치가 발생할 수 있으며, 이는 오류가 아닌 정상 동작인 경우가 많음
- 매도 대금은 T+2영업일에 예수금으로 확정되어 출금 가능(D+2 예수금). 주말·공휴일은 영업일 제외 → 금요일 매도 시 화요일 출금
- 미국 주식은 결제 T+1영업일이며 시차·환전으로 반영 시점이 국내와 다름
- 배당·유상증자 등 권리는 기준일 보유 기준이며, 권리락·배당락일에 주가가 인위적으로 조정됨(손실 아님)
- 예수금 부족·미수 발생 시 D+2일까지 미변제 시 반대매매 가능

추가 규칙: (1) 고객센터 번호가 필요하면 반드시 "1544-5000"만 사용. 다른 번호는 절대 만들어내지 말 것 (2) 위 [증권 기본 구조]로 설명되지 않는 사안은 함부로 "정상"이라 단정하지 말고, URL·구체적 수치·정책 등 확인 안 된 내용은 "정확한 건 앱이나 고객센터에서 확인해보세요" 수준으로 마무리 (3) 뉴스 공유·사건 사고 글은 "저도 봤는데 좀 당황스럽네요" 같은 가벼운 반응 수준으로, 금감원·보상 등 극단적 표현 금지 (4) 질문글이면 아는 선에서 짧게 + 불확실하면 "정확한 건 직접 확인해보시는 게 나을 것 같아요" (5) 대응 불필요한 경우 그 이유 한 줄 (6) [증권 기본 구조]에 없는 앱 메뉴명·버튼명·탭 이름 등 구체적 UI 경로는 지어내지 말 것. 기능 존재 자체는 언급 가능하나("찾아보시면 있을 수도"), "정확한 위치는 앱 내 검색 또는 고객센터(1544-5000)에서 확인" 으로 마무리."""

    try:
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
        if "error" in resp:
            err_type = resp["error"].get("type", "")
            if err_type == "overloaded_error":
                log("AI Overloaded - 10초 후 재시도")
                time.sleep(10)
                response = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": SENTIMENT_MODEL, "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]},
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
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise ValueError("JSON 응답 없음")
        result = json.loads(match.group())
        # score 타입 정규화 (AI가 문자열·float·null 반환 시 TypeError 방지)
        try:
            result["score"] = int(float(result.get("score") or 0))
        except (ValueError, TypeError):
            result["score"] = 0
        # AI가 만들어낸 전화번호 할루시네이션 방지
        if "reply" in result and result["reply"]:
            phone_pattern = re.compile(r"(\(?)((?:\d{2,4}[-.])?\d{3,4}[-.]\d{4})(\)?)")
            def fix_phone(m):
                num = re.sub(r"[^0-9]", "", m.group(2))
                if num == "15445000":
                    return m.group()
                return f"{m.group(1)}1544-5000{m.group(3)}"
            result["reply"] = phone_pattern.sub(fix_phone, result["reply"])
        return result
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

    # 소스 배지 (카페/블로그 시각적 구분)
    source = post.get("source", "cafe")
    if source == "blog":
        badge_html = ('<td style="background:#e3f2fd;color:#1565c0;font-size:10px;'
                      'font-weight:bold;padding:3px 8px;border-radius:3px;">📝 블로그</td>')
    else:
        badge_html = ('<td style="background:#fdecea;color:#c62828;font-size:10px;'
                      'font-weight:bold;padding:3px 8px;border-radius:3px;">💬 카페</td>')

    related_posts = post.get("related_posts", [])
    common_kws    = post.get("common_keywords", [])
    if related_posts:
        kw_str = " · ".join(common_kws) if common_kws else ""
        related_items = "".join(
            f'<tr><td style="padding:3px 0;font-size:12px;color:#555;">'
            f'· <a href="{html.escape(p["url"],quote=True)}" style="color:#555;text-decoration:none;">'
            f'{html.escape(p["title"][:40])}...</a>'
            f' <span style="color:#bbb;font-size:11px;">{p.get("cafe_name","")}</span></td></tr>'
            for p in related_posts
        )
        related_html = f'''<table width="100%" cellpadding="0" cellspacing="0" border="0"
          style="margin-top:8px;background:#fff8e1;border-left:3px solid #ffc107;">
          <tr><td style="padding:8px 12px;">
            <p style="margin:0 0 5px 0;font-size:10px;font-weight:bold;color:#f57f17;">
              📎 유사 이슈 {len(related_posts)}건 추가 탐지 {f"({kw_str})" if kw_str else ""}</p>
            <table cellpadding="0" cellspacing="0">{related_items}</table>
          </td></tr></table>'''
    else:
        related_html = ""

    post_dt  = post.get("post_time")
    raw_date = post.get("date_str", "")
    if post_dt:
        if post_dt.tzinfo is not None:
            dt_kst = post_dt.astimezone(KST)
        else:
            dt_kst = post_dt.replace(tzinfo=KST)
        date_str = dt_kst.strftime("%Y.%m.%d %H:%M")
    elif raw_date:
        date_str = raw_date.rstrip(".")
    else:
        date_str = "-"

    return f"""
    <!--[if true]><table width="100%" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-bottom:1px solid #f0f0f0;">
      <tr>
        <td style="padding:20px 28px;">

          <table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">
            <tr>
              {badge_html}
              <td style="padding-left:8px;font-size:10px;font-weight:bold;
                    color:#c62828;letter-spacing:0.6px;">게시글 {idx} / {total} &nbsp;·&nbsp; {post.get('cafe_name','')}</td>
            </tr>
          </table>

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
          {related_html}

        </td>
      </tr>
    </table>
    <!--[if true]></td></tr></table><![endif]-->"""


def group_similar_alerts(alert_posts):
    """Claude AI가 유사 이슈 여부를 판단해서 묶음 처리"""
    if len(alert_posts) <= 1:
        return alert_posts, []

    titles_text = "\n".join(
        f"{i}. [{p['cafe_name']}] {p['title']}" for i, p in enumerate(alert_posts)
    )
    prompt = f"""아래는 네이버 카페·블로그에서 탐지된 한국투자증권 관련 게시글 목록입니다.
같은 이슈(동일한 서비스 문제, 장애, 사건, 불만)를 다루는 글끼리 묶어주세요.
제목 표현이 달라도(예: 블로그는 SEO 목적으로 제목을 다르게 쓰는 경우가 많음) 같은 사건을 다루면 같은 그룹으로 묶으세요.

{titles_text}

규칙:
- 같은 앱 오류/장애면 같은 그룹
- 같은 기능 문의면 같은 그룹
- 명확히 다른 주제면 별도 그룹
- 단독이면 자기 자신만 포함

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"groups": [[0, 1], [2], [3, 4]]}}
groups는 인덱스 리스트의 리스트. 모든 인덱스가 정확히 한 번씩 포함되어야 함."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": GROUPING_MODEL,
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        response.raise_for_status()
        resp = response.json()
        if "error" in resp:
            raise ValueError(resp["error"])
        text = resp["content"][0]["text"].strip()
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise ValueError("JSON 없음")
        data = json.loads(match.group())
        groups_idx = data.get("groups", [])

        all_idx = [i for g in groups_idx for i in g]
        if sorted(all_idx) != list(range(len(alert_posts))):
            raise ValueError("인덱스 불일치")

    except Exception as e:
        log(f"이슈 묶음 AI 판단 실패 ({e}) - 개별 처리")
        return alert_posts, []

    flat_posts = []
    for group in groups_idx:
        if len(group) == 1:
            flat_posts.append(alert_posts[group[0]])
        else:
            members = [alert_posts[i] for i in group]
            rep = max(members, key=lambda x: x.get("score", 0))
            related = [p for p in members if p is not rep]
            rep["related_posts"] = related
            rep["common_keywords"] = []
            flat_posts.append(rep)
            log(f"유사 이슈 묶음: {[alert_posts[i]['title'][:20] for i in group]}")

    return flat_posts, []


def send_alert_batch(alert_posts, crawled_count, keyword_count, unresolved_posts=None):
    """탐지 게시글 담당자 이메일 발송 (아웃룩/Gmail/모바일 호환)"""
    unresolved_posts = unresolved_posts or []
    total    = len(alert_posts)
    now_kst  = datetime.now(KST)
    now_str  = now_kst.strftime("%Y.%m.%d %H:%M")
    if total > 0:
        subject      = f"⚠️[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준"
        banner_badge = f"AI 부정여론 탐지 · {total}건"
        blog_count = sum(1 for p in alert_posts if p.get("source") == "blog")
        cafe_count = total - blog_count
        if blog_count and cafe_count:
            banner_title = f"부정 언급 {total}건 탐지 (카페 {cafe_count} · 블로그 {blog_count})"
        elif blog_count:
            banner_title = f"네이버 블로그 부정 언급 {total}건 탐지"
        else:
            banner_title = f"네이버 카페 부정 언급 {total}건 탐지"
    else:
        subject      = f"[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준 | 확인필요"
        banner_badge = f"확인필요 · 본문 미수집 {len(unresolved_posts)}건"
        banner_title = f"부정여론 없음 — 본문 미수집 {len(unresolved_posts)}건 확인 필요"

    sorted_posts = alert_posts  # main()에서 이미 score 내림차순 정렬됨
    cards_html = "".join(build_card(p, i+1, total) for i, p in enumerate(sorted_posts))

    # 확인필요 섹션 HTML (본문 미수집 게시글 - 제목 + 링크만)
    if unresolved_posts:
        rows_html = "".join(
            f'<tr>'
            f'<td style="padding:5px 0;font-size:12px;color:#555;vertical-align:top;">· </td>'
            f'<td style="padding:5px 0 5px 4px;">'
            f'<a href="{html.escape(p["url"], quote=True)}" style="font-size:13px;color:#333;text-decoration:none;">'
            f'{html.escape(p["title"])}</a>'
            f'<span style="font-size:11px;color:#bbb;margin-left:6px;">{html.escape(p.get("cafe_name",""))}</span>'
            f'<span style="font-size:11px;color:#ccc;margin-left:4px;">'
            f'{p["post_time"].astimezone(KST).strftime("%Y.%m.%d %H:%M") if p.get("post_time") else ""}'
            f'</span>'
            f'</td>'
            f'</tr>'
            for p in unresolved_posts
        )
        unresolved_html = f"""
      <tr>
        <td style="padding:0;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="border-top:2px solid #f0f0f0;">
            <tr>
              <td style="padding:16px 28px 6px;">
                <p style="margin:0 0 10px 0;font-size:11px;font-weight:bold;
                          color:#888;letter-spacing:0.6px;">⚠️ 확인필요 · 본문 미수집 {len(unresolved_posts)}건</p>
                <table cellpadding="0" cellspacing="0" border="0" width="100%">
                  {rows_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 16px;font-size:11px;color:#bbb;">
                본문을 가져오지 못해 AI 분석이 생략된 게시글입니다. 직접 확인해주세요.
              </td>
            </tr>
          </table>
        </td>
      </tr>"""
    else:
        unresolved_html = ""

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
      {unresolved_html}
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

    # 수신자별 개별 발송: 각 수신자에게는 자기 자신 주소만 To에 보이는 메일이 감.
    # (기업 스팸필터가 "받는사람 헤더에 내 주소가 없는 메일"을 의심하는 패턴을 원천 회피)
    if total > 0:
        bcc_all = RECIPIENTS + CC_RECIPIENTS
        kind = "담당자 전체"
    else:
        bcc_all = []
        kind = "발신자 전용 (확인필요 단독)"
    targets = list(dict.fromkeys([GMAIL_USER] + bcc_all))  # 본인 포함, 중복 제거(순서 유지)

    refused_all = {}
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        for addr in targets:
            m = MIMEMultipart("alternative")
            m["Subject"] = subject
            m["From"] = _from_header()
            m["To"] = _addr_header(addr)
            m["Date"] = formatdate(localtime=True)
            m["Message-ID"] = make_msgid(domain="gmail.com")
            m.attach(MIMEText(html_body, "html", "utf-8"))
            refused = s.sendmail(GMAIL_USER, [addr], m.as_string())
            if refused:
                refused_all.update(refused)

    log_target = f"{kind} ({len(targets)}명, 개별발송)"
    if refused_all:
        # 실제 배포 대상(본인 제외) 전원이 거부됐는지로 "무음 전원실패" 여부 판정 → 예외로 승격
        if bcc_all and set(refused_all.keys()) >= set(bcc_all):
            raise RuntimeError(f"SMTP 전원 거부(무음 실패) - {subject}: {refused_all}")
        _notify_send_failure(f"부정여론 탐지 알림 ({subject})", refused_all)
        log_target += f" — ⚠️ {len(refused_all)}명 거부됨"
    log(f"이메일 발송 완료 - {log_target}")

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

            # ── 쿠키 상태 확인 → cookie_valid 플래그로 우아한 성능저하 ──
            # 쿠키 만료/부재 시 전체 중단 대신: 로그인 필요 카페만 건너뛰고
            # 비로그인 가능 카페 + 블로그는 정상 모니터링 지속
            cookie_valid = True

            if not os.path.exists(COOKIE_FILE):
                cookie_valid = False
                log("⚠️ naver_cookies.json 없음 - 비로그인 모드로 진행 (로그인 필요 카페 건너뜀)")
            else:
                log("쿠키 파일로 접속")
                # 쿠키 만료 사전 감지 (7일 이내 만료 예정 경고)
                try:
                    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                        cookie_data = json.load(f)
                    cookies = cookie_data.get("cookies", [])
                    now_ts = datetime.now(KST).timestamp()
                    expiring_soon = []
                    # 로그인 유지에 실제로 쓰이는 핵심 쿠키만 검사 (나머지는 원래 수분~수일 단위로
                    # 자동 만료되는 세션/추적용 쿠키라 항상 "7일 이내"에 걸려 상시 오탐 발생)
                    CORE_LOGIN_COOKIES = {"NID_AUT", "NID_SES", "nid_inf"}
                    for c in cookies:
                        name = c.get("name", "unknown")
                        if name not in CORE_LOGIN_COOKIES:
                            continue
                        exp = c.get("expires", -1)
                        # now_ts < exp: 아직 유효한 쿠키 중에서
                        # exp < now_ts + 7일: 7일 이내 만료 예정인 것만 경고 (갱신 여유 확보)
                        # (이미 만료된 쿠키는 크롤링에 영향 없으므로 제외)
                        if now_ts < exp < now_ts + (7 * 24 * 3600):
                            expiring_soon.append(name)
                    if expiring_soon:
                        send_status_email("warning",
                            detail=f"쿠키 만료 임박 (7일 이내): {', '.join(expiring_soon)}\n"
                                   f"NAVER_COOKIES_JSON Secret 재등록을 준비해주세요.")
                        log(f"⚠️ 쿠키 만료 임박: {expiring_soon}")
                except Exception as e:
                    log(f"쿠키 만료 확인 중 오류: {e}")

            # context 생성: 쿠키가 있으면 로그인 상태로, 없으면 비로그인으로
            if cookie_valid:
                context = browser.new_context(storage_state=COOKIE_FILE)
            else:
                context = browser.new_context()
            page = context.new_page()

            # 로그인 상태 확인 (쿠키가 있을 때만) - 만료 확인 시 전체 중단 대신 비로그인 모드 전환
            if cookie_valid:
                page.goto("https://www.naver.com", wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if "nid.naver.com" in page.url:
                    cookie_valid = False
                    log("⚠️ 쿠키 만료 확인 - 비로그인 모드로 전환 (로그인 필요 카페 건너뜀)")
                    send_status_email("warning",
                        detail="쿠키가 만료되어 비로그인 모드로 실행합니다.\n"
                               "로그인 필요 카페는 이번 실행에서 건너뜁니다.\n"
                               "NAVER_COOKIES_JSON Secret을 재등록해주세요.")
                    # 비로그인 컨텍스트로 재생성
                    context.close()
                    context = browser.new_context()
                    page = context.new_page()
                else:
                    log("로그인 상태 확인 완료")

            total_crawled    = 0
            total_keywords   = 0
            all_alerts       = []
            unresolved_posts = []  # 본문 미수집으로 AI 분석 생략된 게시글
            skipped_cafes    = []  # 쿠키 만료로 건너뛴 로그인 필요 카페

            for cafe in CAFES:
                cafe_id   = cafe["id"]
                num_id    = cafe["num_id"]
                cafe_name = cafe["name"]

                # 우아한 성능저하: 쿠키 무효 시 로그인 필요 카페는 건너뜀
                if not cookie_valid and cafe.get("login_required", True):
                    skipped_cafes.append(cafe_name)
                    log(f"\n── {cafe_name} ({cafe_id}) ── 건너뜀 (쿠키 만료, 회원전용 카페)")
                    continue

                log(f"\n── {cafe_name} ({cafe_id}) ──")

                try:
                    # STEP 1: 목록 수집
                    posts = get_post_list(page, cafe_id, num_id)

                    # STEP 2: 신규 여부 확인 (중복 제거)
                    new_posts = [p for p in posts if is_new(f"{cafe_id}:{p['post_id']}")]
                    log(f"신규 게시글: {len(new_posts)}건")
                    total_crawled += len(new_posts)

                    # STEP 3: 본문 수집 + AI 분석
                    for post in new_posts:

                        matched = post.get("keyword") or next(
                            (kw for kw in KEYWORDS if kw.lower() in post["title"].lower()), None
                        )
                        if not matched:
                            mark_seen(f"{cafe_id}:{post['post_id']}")
                            continue

                        total_keywords += 1
                        log(f"키워드 [{matched}] 탐지: {post['title'][:40]}...")

                        # 본문 수집 (body: AI 분석용 2000자, body_full: 검증용 전체)
                        body, body_full = get_post_detail(page, post["url"], cafe_id)

                        # 키워드 실존 검증: 카페 검색은 본문+댓글 통합검색(ta=ARTICLE_COMMENT)이라
                        # 댓글에서만 키워드가 매칭된 글도 결과에 포함될 수 있음.
                        # 전체 본문(body_full) 기준으로 검증해 긴 글 후반부 언급도 정확히 탐지
                        if body_full:
                            title_body_check = (post["title"] + " " + body_full)
                            if not any(kw in title_body_check for kw in KEYWORDS):
                                log(f"  키워드 미확인 - 댓글 매칭 추정(본문에 없음), AI 분석 생략")
                                mark_seen(f"{cafe_id}:{post['post_id']}")
                                continue

                        # AI 호출 전 룰필터 ①: 부정 힌트 없으면 Claude 미호출
                        combined_text = (post["title"] + " " + body).lower()
                        has_hint = any(hint in combined_text for hint in NEGATIVE_HINTS)

                        if not has_hint:
                            # 본문 미수집 + 부정힌트 없음 → 확인필요 섹션에 적재
                            if not body:
                                post["cafe_name"]  = cafe_name
                                post["source"]     = "cafe"
                                post["matched_kw"] = matched
                                unresolved_posts.append(post)
                                mark_seen(f"{cafe_id}:{post['post_id']}")
                                log(f"  본문 미수집 - 확인필요 목록 추가")
                            else:
                                log(f"  룰필터 통과 - AI 분석 생략 (부정 힌트 없음)")
                                mark_seen(f"{cafe_id}:{post['post_id']}")
                            continue

                        # AI 호출 전 룰필터 ②: 광고성 게시글 제목 패턴 차단
                        is_ad = any(pat in post["title"] for pat in AD_SKIP_PATTERNS)
                        if is_ad:
                            log(f"  광고성 게시글 제외: {post['title'][:40]}")
                            mark_seen(f"{cafe_id}:{post['post_id']}")
                            continue

                        result = analyze_sentiment(post["title"], body, matched)
                        if result["summary"] != "분석 실패":
                            mark_seen(f"{cafe_id}:{post['post_id']}")
                        log(f"AI 결과 - 부정:{result['is_negative']} | 강도:{result['score']}/10")

                        post["cafe_name"]  = cafe_name
                        post["source"]     = "cafe"
                        post["matched_kw"] = matched
                        post["score"]      = result["score"]
                        post["summary"]    = result["summary"]
                        post["reply"]      = result.get("reply", "")
                        if result["score"] >= SCORE_THRESHOLD:
                            all_alerts.append(post)

                except Exception as cafe_err:
                    log(f"  ⚠️ [{cafe_name}] 오류 - 다음 카페로 계속: {cafe_err}")
                    try:
                        page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    continue

            # ── 네이버 블로그 섹션 (카페와 동일 page/context 재사용, 로그인 불필요) ──
            log(f"\n── 네이버 블로그 ──")
            try:
                blog_posts = get_blog_post_list(page, KEYWORDS)
                new_blog_posts = [p for p in blog_posts if is_new(f"blog:{p['post_id']}")]
                log(f"블로그 신규 게시글: {len(new_blog_posts)}건")
                total_crawled += len(new_blog_posts)
                cutoff = datetime.now(KST) - timedelta(hours=TIME_WINDOW)

                for post in new_blog_posts:
                    matched = post.get("keyword", "").split(",")[0].strip()
                    total_keywords += 1
                    log(f"블로그 키워드 [{matched}] 탐지: {post['url'][:60]}...")

                    detail = get_blog_detail(page, post["url"])
                    title, body, date_str = detail["title"], detail["body"], detail["date_str"]
                    body_full = detail.get("body_full", body)

                    if not title:
                        # 제목조차 못 가져오면 스킵 (삭제/비공개 게시글 가능성)
                        mark_seen(f"blog:{post['post_id']}")
                        continue

                    post_time = parse_date(date_str) if date_str else datetime.now(KST)
                    if post_time < cutoff:
                        mark_seen(f"blog:{post['post_id']}")
                        continue

                    # 키워드 실존 검증 (카페와 동일 안전장치)
                    # 전체 본문(body_full) 기준 - 긴 글 후반부 언급도 정확히 탐지
                    if body_full:
                        title_body_check = title + " " + body_full
                        if not any(kw in title_body_check for kw in KEYWORDS):
                            log(f"  키워드 미확인(본문에 없음), AI 분석 생략")
                            mark_seen(f"blog:{post['post_id']}")
                            continue

                    combined_text = (title + " " + body).lower()
                    has_hint = any(hint in combined_text for hint in NEGATIVE_HINTS)

                    if not has_hint:
                        if not body:
                            post["cafe_name"]  = "네이버 블로그"
                            post["source"]     = "blog"
                            post["title"]      = title
                            post["matched_kw"] = matched
                            post["post_time"]  = post_time
                            unresolved_posts.append(post)
                            log(f"  본문 미수집 - 확인필요 목록 추가")
                        else:
                            log(f"  룰필터 통과 - AI 분석 생략 (부정 힌트 없음)")
                        mark_seen(f"blog:{post['post_id']}")
                        continue

                    # 블로그 특화 광고 패턴 + 기존 광고 패턴 모두 체크
                    is_ad = any(pat in title for pat in AD_SKIP_PATTERNS + BLOG_AD_SKIP_PATTERNS)
                    if is_ad:
                        log(f"  광고성 게시글 제외: {title[:40]}")
                        mark_seen(f"blog:{post['post_id']}")
                        continue

                    result = analyze_sentiment(title, body, matched)
                    if result["summary"] != "분석 실패":
                        mark_seen(f"blog:{post['post_id']}")
                    log(f"AI 결과 - 부정:{result['is_negative']} | 강도:{result['score']}/10")

                    post["cafe_name"]  = "네이버 블로그"
                    post["source"]     = "blog"
                    post["title"]      = title
                    post["matched_kw"] = matched
                    post["score"]      = result["score"]
                    post["summary"]    = result["summary"]
                    post["reply"]      = result.get("reply", "")
                    post["post_time"]  = post_time
                    if result["score"] >= SCORE_THRESHOLD:
                        all_alerts.append(post)

            except Exception as blog_err:
                log(f"  ⚠️ [블로그] 오류 - 계속 진행: {blog_err}")

            context.close()
            browser.close()

            # STEP 4: 결과에 따라 이메일 분기
            log(f"\n크롤링 {total_crawled}건 → 키워드 탐지 {total_keywords}건 → AI 필터링 {len(all_alerts)}건")
            if skipped_cafes:
                log(f"⚠️ 쿠키 만료로 건너뛴 카페 {len(skipped_cafes)}곳: {', '.join(skipped_cafes)}")
            if all_alerts:
                all_alerts = sorted(all_alerts, key=lambda x: x.get("score", 0), reverse=True)
                # 그룹핑 먼저 (같은 이슈를 다룬 여러 글을 1건으로 묶음)
                # → 블로그 리포스팅 등으로 원본 건수가 많아도 실제 "이슈 수" 기준으로 30건 제한 적용
                all_alerts, _ = group_similar_alerts(all_alerts)
                all_alerts = sorted(all_alerts, key=lambda x: x.get("score", 0), reverse=True)
                if len(all_alerts) > MAX_ALERTS:
                    log(f"알림 건수 초과 — {len(all_alerts)}건 중 {MAX_ALERTS}건만 발송")
                    all_alerts = all_alerts[:MAX_ALERTS]
                grouped_cnt = sum(1 for p in all_alerts if p.get("related_posts"))
                if grouped_cnt:
                    log(f"유사 이슈 묶음: {grouped_cnt}건 그룹화")
                send_alert_batch(
                    all_alerts,
                    crawled_count=total_crawled,
                    keyword_count=total_keywords,
                    unresolved_posts=unresolved_posts,
                )
            elif unresolved_posts:
                # 부정여론은 없지만 본문 미수집 건이 있을 때 → 확인필요 섹션만 발송
                log(f"부정여론 없음, 확인필요 {len(unresolved_posts)}건 발송")
                send_alert_batch(
                    [],
                    crawled_count=total_crawled,
                    keyword_count=total_keywords,
                    unresolved_posts=unresolved_posts,
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







