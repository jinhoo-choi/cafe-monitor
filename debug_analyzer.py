"""
debug_analyzer.py
- GitHub Actions 로그에서 [DEBUG] iframe 정보 추출
- Claude API로 get_post_detail() 수정 코드 생성
- ast.parse() + 안전성 검증 후 monitor.py 자동 푸시
- 결과 메일 발송 (성공/실패 모두)
"""

import os
import re
import ast
import json
import base64
import smtplib
import requests
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

KST          = timezone(timedelta(hours=9))
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_APP_PW = os.environ["GMAIL_APP_PW"]
GH_TOKEN     = os.environ["GH_TOKEN"]
CLAUDE_KEY   = os.environ["CLAUDE_API_KEY"]
REPO         = "jinhoo-choi/cafe-monitor"
CLAUDE_MODEL = "claude-sonnet-4-6"   # 코드 생성은 Sonnet 사용

def log(msg):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {msg}")

# ── 1. 최근 Actions 실행에서 [DEBUG] 라인 추출 ──────────────────────

def get_debug_lines():
    """가장 최근 'workflow_dispatch'(monitor.py 실행) run의 로그에서 [DEBUG] 라인 추출
    ※ schedule(10:00 KST) 트리거 자신의 run은 monitor.py를 실행하지 않으므로
       반드시 event=workflow_dispatch 로 필터링해야 함"""
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/runs"
        f"?event=workflow_dispatch&status=success&per_page=1",
        headers={"Authorization": f"token {GH_TOKEN}"},
    )
    runs = r.json().get("workflow_runs", [])
    if not runs:
        raise RuntimeError("workflow_dispatch run 없음 - monitor.py 실행 기록 없음")

    run_id = runs[0]["id"]
    log(f"대상 run_id(workflow_dispatch): {run_id} | created_at={runs[0]['created_at']}")

    # job_id 조회
    r2 = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs",
        headers={"Authorization": f"token {GH_TOKEN}"},
    )
    jobs = r2.json().get("jobs", [])
    if not jobs:
        raise RuntimeError("jobs 없음")
    job_id = jobs[0]["id"]
    log(f"job_id: {job_id}")

    # 로그 다운로드 (리디렉션 URL → Azure Blob)
    r3 = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"token {GH_TOKEN}"},
        allow_redirects=True,
        timeout=30,
    )
    log_text = r3.text

    # [DEBUG] 라인만 추출
    debug_lines = [l.strip() for l in log_text.splitlines() if "[DEBUG]" in l]
    log(f"[DEBUG] 라인 {len(debug_lines)}개 추출")
    return debug_lines, log_text

# ── 2. 현재 monitor.py 가져오기 ─────────────────────────────────────

def get_monitor_py():
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/contents/monitor.py",
        headers={"Authorization": f"token {GH_TOKEN}"},
    )
    d = r.json()
    content = base64.b64decode(d["content"]).decode()
    sha = d["sha"]
    return content, sha

# ── 3. Claude API로 get_post_detail() 수정 코드 생성 ────────────────

def generate_fix(debug_lines, current_src):
    """디버그 로그 기반으로 get_post_detail() 수정본 생성"""

    # 현재 get_post_detail 함수만 추출
    m = re.search(r"(def get_post_detail\(.*?)(?=\ndef )", current_src, re.S)
    if not m:
        raise RuntimeError("get_post_detail 함수 추출 실패")
    current_func = m.group(1)

    debug_text = "\n".join(debug_lines) if debug_lines else "(디버그 라인 없음)"

    prompt = f"""다음은 네이버 카페 부정여론 모니터링 시스템의 디버그 로그입니다.
Playwright로 네이버 카페 게시글 본문을 수집하는 get_post_detail() 함수가
일부 카페(평생주식카페, 미국주식이 미래다)에서 본문을 가져오지 못하고 있습니다.

[디버그 로그 - 실제 로드된 frame URL들]
{debug_text}

[현재 get_post_detail() 코드]
{current_func}

위 디버그 로그를 분석해서:
1. 실제 iframe URL 패턴이 무엇인지 파악
2. 본문 수집 실패 원인 진단
3. get_post_detail() 함수 전체를 수정

반드시 지킬 규칙:
- 함수 시그니처 def get_post_detail(page, post_url, cafe_id): 유지
- [DEBUG] 로그 라인 모두 제거 (디버그 목적이므로)
- 수정 범위는 get_post_detail() 함수 내부만
- return body로 끝나야 함
- Python 3.11 문법, Playwright sync API 사용
- 로그는 log() 함수 사용
- 반드시 함수 코드만 출력. 설명 텍스트 없이 def get_post_detail로 시작하는 코드만."""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    r.raise_for_status()
    resp = r.json()
    new_func = resp["content"][0]["text"].strip()

    # 코드블록 마크다운 제거
    new_func = re.sub(r"^```python\n?", "", new_func)
    new_func = re.sub(r"\n?```$", "", new_func)
    new_func = new_func.strip()

    log(f"Claude 생성 코드 길이: {len(new_func)}자")
    return new_func

# ── 4. 안전성 검증 ──────────────────────────────────────────────────

def validate_fix(new_func, new_src):
    errors = []

    # 4-1. 문법 검사
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        errors.append(f"문법 오류: {e}")

    # 4-2. 함수 시그니처 유지
    if "def get_post_detail(page, post_url, cafe_id):" not in new_func:
        errors.append("함수 시그니처 변경됨")

    # 4-3. return body 존재
    if "return body" not in new_func:
        errors.append("return body 없음")

    # 4-4. [DEBUG] 라인 완전 제거
    if "[DEBUG]" in new_func:
        errors.append("[DEBUG] 라인 미제거")

    # 4-5. 다른 핵심 함수가 건드려지지 않았는지
    must_exist = [
        "def main(", "def analyze_sentiment(", "def send_alert_batch(",
        "def get_post_list(", "def search_keyword(", "def parse_date(",
        "def group_similar_alerts(", "def send_status_email(",
    ]
    for fn in must_exist:
        if fn not in new_src:
            errors.append(f"핵심 함수 누락: {fn}")

    # 4-6. 수정 범위 확인 (get_post_detail 외 함수 변경 없는지)
    # 원본과 새 코드에서 get_post_detail 외 부분이 동일한지 체크
    if "[DEBUG]" in new_src:
        errors.append("새 코드에 [DEBUG] 잔존")

    return errors

# ── 5. monitor.py에 새 함수 적용 ────────────────────────────────────

def apply_fix(current_src, new_func):
    """현재 src에서 get_post_detail() 함수를 새 함수로 교체"""
    pattern = r"def get_post_detail\(.*?)(?=\ndef )"
    m = re.search(pattern, current_src, re.S)
    if not m:
        raise RuntimeError("get_post_detail 교체 위치 찾기 실패")

    new_src = current_src[:m.start()] + new_func + "\n" + current_src[m.end():]
    return new_src

# ── 6. GitHub 푸시 ───────────────────────────────────────────────────

def push_fix(new_src, sha, analysis_summary):
    content = base64.b64encode(new_src.encode()).decode()
    r = requests.put(
        f"https://api.github.com/repos/{REPO}/contents/monitor.py",
        headers={"Authorization": f"token {GH_TOKEN}"},
        json={
            "message": f"fix(auto): iframe 본문 수집 개선 - debug_analyzer 자동 적용\n\n{analysis_summary}",
            "content": content,
            "sha": sha,
        },
    )
    result = r.json()
    if "commit" in result:
        return result["commit"]["sha"][:7]
    raise RuntimeError(f"푸시 실패: {result}")

# ── 7. 결과 메일 발송 ────────────────────────────────────────────────

def send_result_email(status, debug_lines, new_func=None, commit_sha=None, errors=None, exc=None):
    now_kst = datetime.now(KST)
    now_str = now_kst.strftime("%Y.%m.%d %H:%M")

    if status == "success":
        subject = f"✅ [부정여론봇] iframe 수정 자동 적용 완료 | {now_str} KST"
        color   = "#2e7d32"
        title   = "✅ 본문 수집 코드 자동 수정 완료"
        body_html = f"""
<p style="font-size:13px;color:#333;line-height:1.8;">
  디버그 로그 분석 후 <code>get_post_detail()</code> 함수가 자동 수정되어 푸시됐습니다.<br>
  커밋: <strong>{commit_sha}</strong>
</p>
<p style="font-size:12px;color:#555;font-weight:bold;margin-top:16px;">📋 디버그 로그 ({len(debug_lines)}줄)</p>
<pre style="background:#f5f5f5;padding:12px;font-size:11px;overflow-x:auto;">{"<br>".join(debug_lines) or "(없음)"}</pre>
<p style="font-size:12px;color:#555;font-weight:bold;margin-top:16px;">🔧 적용된 수정 코드</p>
<pre style="background:#f5f5f5;padding:12px;font-size:11px;overflow-x:auto;">{new_func[:1500] if new_func else ""}</pre>
<p style="font-size:12px;color:#888;margin-top:16px;">
  내일모레 verify_patch.py가 실행되어 수정 효과를 자동 검증합니다.
</p>"""
    elif status == "validation_fail":
        subject = f"⚠️ [부정여론봇] 자동 수정 검증 실패 - 수동 확인 필요 | {now_str} KST"
        color   = "#e65100"
        title   = "⚠️ 자동 수정 검증 실패 — 푸시 미진행"
        body_html = f"""
<p style="font-size:13px;color:#333;line-height:1.8;">
  Claude가 수정 코드를 생성했지만 안전성 검증에 실패하여 푸시하지 않았습니다.
</p>
<p style="font-size:12px;color:#c62828;font-weight:bold;">❌ 검증 실패 항목</p>
<pre style="background:#fdecea;padding:12px;font-size:12px;">{"<br>".join(errors or [])}</pre>
<p style="font-size:12px;color:#555;font-weight:bold;margin-top:16px;">📋 디버그 로그</p>
<pre style="background:#f5f5f5;padding:12px;font-size:11px;">{"<br>".join(debug_lines) or "(없음)"}</pre>"""
    else:
        subject = f"❌ [부정여론봇] 자동 수정 오류 | {now_str} KST"
        color   = "#b71c1c"
        title   = "❌ debug_analyzer 실행 오류"
        body_html = f"""
<p style="font-size:13px;color:#333;line-height:1.8;">오류가 발생하여 자동 수정이 진행되지 않았습니다.</p>
<pre style="background:#fdecea;padding:12px;font-size:12px;">{str(exc)[:1000] if exc else ""}</pre>"""

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;">
  <tr><td align="center" style="padding:32px 16px;">
    <table width="600" cellpadding="0" cellspacing="0"
           style="max-width:600px;background:#fff;border:1px solid #d5d9e0;">
      <tr>
        <td style="background:{color};padding:20px 28px;">
          <p style="margin:0 0 4px;font-size:16px;font-weight:bold;color:#fff;">{title}</p>
          <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.7);">
            debug_analyzer.py · {now_str} KST
          </p>
        </td>
      </tr>
      <tr><td style="padding:24px 28px;">{body_html}</td></tr>
      <tr>
        <td style="background:#f8f8f8;border-top:1px solid #ececec;
                   padding:12px 20px;text-align:center;font-size:11px;color:#aaa;">
          담당자 : 최진후 차장 · Powered by Claude AI 자동 수정
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"⚠️ eBiz 부정여론봇 <{GMAIL_USER}>"
    msg["To"]      = GMAIL_USER
    msg["Date"]    = now_kst.strftime("%a, %d %b %Y %H:%M:%S +0900")
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PW)
        s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    log(f"결과 메일 발송 → {GMAIL_USER}")

# ── 메인 ────────────────────────────────────────────────────────────

def main():
    log("=== debug_analyzer 시작 ===")
    debug_lines = []
    new_func    = None
    commit_sha  = None
    errors      = None

    try:
        # 1. 디버그 로그 추출
        debug_lines, _ = get_debug_lines()

        if not debug_lines:
            log("⚠️ [DEBUG] 라인 없음 — monitor.py가 이미 정상 동작 중일 수 있음")
            send_result_email("success", debug_lines,
                              new_func="[DEBUG] 라인 없음 - 수정 불필요",
                              commit_sha="(푸시 없음)")
            return

        # 2. 현재 monitor.py 가져오기
        current_src, sha = get_monitor_py()
        log(f"monitor.py 로드 완료 ({len(current_src)}자, sha={sha[:8]})")

        # 3. Claude로 수정 코드 생성
        log("Claude API 수정 코드 생성 중...")
        new_func = generate_fix(debug_lines, current_src)

        # 4. 새 src 생성
        new_src = apply_fix(current_src, new_func)

        # 5. 안전성 검증
        log("안전성 검증 중...")
        errors = validate_fix(new_func, new_src)

        if errors:
            log(f"❌ 검증 실패: {errors}")
            send_result_email("validation_fail", debug_lines,
                              new_func=new_func, errors=errors)
            return

        log("✅ 검증 통과")

        # 6. 푸시
        analysis_summary = f"디버그 라인 {len(debug_lines)}개 분석 완료"
        commit_sha = push_fix(new_src, sha, analysis_summary)
        log(f"✅ 푸시 완료: {commit_sha}")

        # 7. 성공 메일
        send_result_email("success", debug_lines,
                          new_func=new_func, commit_sha=commit_sha)

    except Exception as e:
        log(f"❌ 오류: {e}")
        traceback.print_exc()
        send_result_email("error", debug_lines, exc=e)
        raise

    log("=== debug_analyzer 완료 ===")

if __name__ == "__main__":
    main()

