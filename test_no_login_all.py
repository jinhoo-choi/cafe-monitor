"""
test_no_login_all.py
- 14개 카페 전체에서 로그인 없이 검색+본문열람 가능 여부 전수 검증
"""
import smtplib, os, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]

CAFES = [
    {"id": "likeusstock", "num_id": "28497937", "name": "미국주식이 미래다"},
    {"id": "vilab",       "num_id": "11525920", "name": "가치투자연구소"},
    {"id": "yamizal",     "num_id": "30676048", "name": "미국 주식에 미치다"},
    {"id": "geobuk2",     "num_id": "26251287", "name": "거북이 투자법"},
    {"id": "ustock",      "num_id": "15112066", "name": "평생주식카페"},
    {"id": "onepieceholicplus", "num_id": "22290117", "name": "월급쟁이 재테크 연구카페"},
    {"id": "pointns",          "num_id": "25873056", "name": "주식투자는 달팽이처럼"},
    {"id": "wjdrkrjqn",        "num_id": "30786704", "name": "정가를 거부하는 사람들"},
    {"id": "stocktraining",    "num_id": "29798500", "name": "나는 주식트레이더다"},
    {"id": "dlxogns01",        "num_id": "23676262", "name": "배당 투자자 모임"},
    {"id": "engmstudy",        "num_id": "14028420", "name": "짠돌이카페"},
    {"id": "hayate1",          "num_id": "11560463", "name": "주식광장"},
    {"id": "divclub",          "num_id": "31050378", "name": "은퇴후 50년"},
    {"id": "moneyinsights",    "num_id": "31449216", "name": "돈이 되는 모든 정보"},
]

logs = []
def log(msg):
    print(msg)
    logs.append(msg)

def send_result_email(summary_line):
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[비로그인 전수검증] {summary_line} | {now_str} KST"
    msg["From"] = f"검증봇 <{GMAIL_USER}>"
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(f"<pre>{chr(10).join(logs)}</pre>", "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

def main():
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()  # 쿠키 전혀 없음
            page = context.new_page()

            for cafe in CAFES:
                cafe_id, num_id, name = cafe["id"], cafe["num_id"], cafe["name"]
                log(f"\n{'='*55}")
                log(f"[{name}] ({cafe_id})")
                log(f"{'='*55}")

                row = {"name": name, "search_ok": False, "count": 0,
                       "body_ok": False, "redirect": False}

                # 검색 테스트
                q = urllib.parse.quote("한투")
                search_url = f"https://cafe.naver.com/f-e/cafes/{num_id}/menus/0?viewType=L&ta=ARTICLE_COMMENT&page=1&q={q}"
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)
                    redirected = "nid.naver.com" in page.url
                    row["redirect"] = redirected
                    rows = page.query_selector_all("a.article")
                    row["count"] = len(rows)
                    row["search_ok"] = not redirected
                    log(f"  검색: {'✅' if row['search_ok'] else '🔴 로그인 필요'} ({len(rows)}건, 리디렉션={redirected})")

                    # 검색 결과가 있으면 첫 게시글 본문도 확인
                    if rows:
                        href = rows[0].get_attribute("href") or ""
                        # href가 상대경로일 수 있으니 절대경로 보정
                        if href.startswith("/"):
                            post_url = "https://cafe.naver.com" + href
                        elif href.startswith("http"):
                            post_url = href
                        else:
                            post_url = None

                        if post_url:
                            page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
                            try:
                                page.wait_for_selector(".se-main-container, .ArticleContentBox, #tbody", timeout=6000)
                            except Exception:
                                page.wait_for_timeout(2000)
                            body_redirect = "nid.naver.com" in page.url
                            body_text = ""
                            for frame in page.frames:
                                fu = frame.url or ""
                                if "naver.com" in fu and fu != page.url and fu != "about:blank":
                                    for sel in [".se-main-container", ".ArticleContentBox", "#tbody"]:
                                        el = frame.query_selector(sel)
                                        if el:
                                            t = el.inner_text().strip()
                                            if t:
                                                body_text = t
                                                break
                                    if body_text:
                                        break
                            if not body_text:
                                for sel in [".se-main-container", ".ArticleContentBox", "#tbody"]:
                                    el = page.query_selector(sel)
                                    if el:
                                        body_text = el.inner_text().strip()
                                        if body_text:
                                            break
                            row["body_ok"] = bool(body_text) and not body_redirect
                            log(f"  본문열람: {'✅' if row['body_ok'] else '🔴'} ({len(body_text)}자, 리디렉션={body_redirect})")
                except Exception as e:
                    log(f"  ❌ 오류: {e}")

                results.append(row)

            browser.close()
    except Exception as e:
        import traceback
        log(f"\n❌ 전체 오류: {traceback.format_exc()}")

    log(f"\n\n{'='*55}")
    log("전체 요약")
    log(f"{'='*55}")
    ok_search = sum(1 for r in results if r["search_ok"])
    ok_body = sum(1 for r in results if r["body_ok"])
    for r in results:
        s = "✅" if r["search_ok"] else "🔴"
        b = "✅" if r["body_ok"] else "🔴"
        log(f"  검색{s} 본문{b} | {r['name']}")
    log(f"\n검색 성공: {ok_search}/{len(CAFES)}")
    log(f"본문 성공: {ok_body}/{len(CAFES)}")

    summary = f"검색 {ok_search}/{len(CAFES)} · 본문 {ok_body}/{len(CAFES)}"
    send_result_email(summary)

if __name__ == "__main__":
    main()
