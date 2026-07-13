"""
refresh_cookies.py
- 네이버 로그인을 자동 수행해 새 세션 쿠키를 발급받고,
  GitHub Secret(NAVER_COOKIES_JSON)에 자동 반영한다.
- 워크플로우는 workflow_dispatch(수동 클릭)로만 실행됨 - 완전 자동 스케줄 아님.
- 캡차/보안인증이 감지되면 즉시 중단하고 실패 메일만 발송한다 (재시도 없음 - 
  반복 시도는 봇 탐지 패턴을 더 강화시킬 뿐이라 의도적으로 1회만 시도).
"""

import os
import re
import json
import base64
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from nacl import encoding, public

KST = timezone(timedelta(hours=9))

GMAIL_USER    = os.environ["GMAIL_USER"]
GMAIL_APP_PW  = os.environ["GMAIL_APP_PW"]
NOTIFY_EMAIL  = os.environ["NOTIFY_EMAIL"]
NAVER_ID      = os.environ["NAVER_ID"]
NAVER_PW      = os.environ["NAVER_PW"]
ADMIN_TOKEN   = os.environ["ADMIN_GITHUB_TOKEN"]
REPO          = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo, Actions가 자동 주입

CAPTCHA_HINTS = [
    "자동입력 방지", "보안문자", "captcha", "unusual", "비정상적인 접근",
    "다른 사람이 회원님의 계정", "휴대전화 인증", "본인 확인",
]

def log(msg):
    print(f"[쿠키갱신] {msg}")

def send_email(subject, body_html):
    now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{subject} | {now_str} KST"
    msg["From"] = f"쿠키갱신봇 <{GMAIL_USER}>"
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())

def encrypt_secret(pub_key_b64, secret_value):
    pub_key = public.PublicKey(pub_key_b64.encode(), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pub_key)
    encrypted = sealed_box.encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()

def update_github_secret(secret_name, secret_value):
    headers = {"Authorization": f"token {ADMIN_TOKEN}"}
    r = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key", headers=headers)
    r.raise_for_status()
    key_data = r.json()
    encrypted = encrypt_secret(key_data["key"], secret_value)
    resp = requests.put(
        f"https://api.github.com/repos/{REPO}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
    )
    resp.raise_for_status()
    return resp.status_code

def looks_like_verification_wall(page):
    """캡차/보안인증 등 사람 확인 절차가 떴는지 감지"""
    try:
        page_text = page.inner_text("body")
    except Exception:
        page_text = ""
    for hint in CAPTCHA_HINTS:
        if hint in page_text:
            return hint
    return None

def main():
    log("네이버 로그인 자동 갱신 시작")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            # 아이디/비밀번호 입력 - fill() 사용 (키 입력 시뮬레이션보다 붙여넣기에 가까워
            # 타이핑 패턴 기반 탐지를 덜 유발함)
            page.fill("#id", NAVER_ID)
            page.wait_for_timeout(300)
            page.fill("#pw", NAVER_PW)
            page.wait_for_timeout(300)

            page.click("#log\\.login")
            page.wait_for_timeout(3000)

            # ── 캡차/보안인증 감지 ──
            hint = looks_like_verification_wall(page)
            if hint:
                log(f"⚠️ 보안 인증 감지: '{hint}' - 자동 갱신 중단")
                send_email(
                    "🔴 쿠키 자동 갱신 실패 - 수동 확인 필요",
                    f"<p>네이버가 보안 인증을 요구해 자동 로그인을 중단했습니다.</p>"
                    f"<p>감지된 문구: <b>{hint}</b></p>"
                    f"<p>반복 시도는 계정 보안 조치를 유발할 수 있어 재시도하지 않았습니다.</p>"
                    f"<p><b>make_cookie.py로 로컬에서 수동 갱신해주세요.</b></p>"
                )
                browser.close()
                return

            # ── 신규 기기 등록 안내 팝업 처리 ("등록안함" 클릭) ──
            try:
                skip_btn = page.query_selector("a#new\\.dontsave, button:has-text('등록안함')")
                if skip_btn:
                    skip_btn.click()
                    page.wait_for_timeout(1500)
                    log("신규 기기 등록 팝업 - '등록안함' 처리")
            except Exception:
                pass

            # ── 로그인 성공 여부 확인 ──
            page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)

            if "nid.naver.com" in page.url:
                log("❌ 로그인 실패 - 여전히 로그인 페이지")
                send_email(
                    "🔴 쿠키 자동 갱신 실패 - 로그인 실패",
                    "<p>아이디/비밀번호가 틀렸거나 알 수 없는 이유로 로그인에 실패했습니다.</p>"
                    "<p>NAVER_ID / NAVER_PW Secret을 확인해주세요.</p>"
                )
                browser.close()
                return

            log("✅ 로그인 성공 - 쿠키 추출")
            storage = context.storage_state()
            new_cookie_json = json.dumps(storage, ensure_ascii=False)

            # 핵심 쿠키 존재 확인
            cookie_names = {c["name"] for c in storage.get("cookies", [])}
            has_core = {"NID_AUT", "NID_SES"}.issubset(cookie_names)
            log(f"핵심 로그인 쿠키 확보: {has_core} ({cookie_names & {'NID_AUT','NID_SES','nid_inf'}})")

            if not has_core:
                log("❌ 핵심 쿠키(NID_AUT/NID_SES) 누락 - 갱신 중단")
                send_email(
                    "🔴 쿠키 자동 갱신 실패 - 핵심 쿠키 누락",
                    "<p>로그인은 됐지만 NID_AUT/NID_SES 쿠키를 확보하지 못했습니다.</p>"
                    "<p>수동 확인이 필요합니다.</p>"
                )
                browser.close()
                return

            browser.close()

        # ── GitHub Secret 갱신 ──
        status = update_github_secret("NAVER_COOKIES_JSON", new_cookie_json)
        log(f"✅ NAVER_COOKIES_JSON Secret 갱신 완료 (status={status})")

        send_email(
            "✅ 쿠키 자동 갱신 성공",
            f"<p>네이버 로그인 쿠키가 자동으로 갱신되어 <b>NAVER_COOKIES_JSON</b> Secret에 반영됐습니다.</p>"
            f"<p>확보된 쿠키: {len(cookie_names)}개</p>"
            f"<p>다음 모니터링 실행부터 새 쿠키로 동작합니다.</p>"
        )

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"❌ 예외 발생: {err}")
        try:
            send_email(
                "🔴 쿠키 자동 갱신 실패 - 예외 발생",
                f"<pre>{err}</pre>"
            )
        except Exception:
            pass
        raise

if __name__ == "__main__":
    main()
