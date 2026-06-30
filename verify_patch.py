"""
verify_patch.py
- 최신 Actions 로그에서 본문 수집 성공/실패 현황 확인
- [DEBUG] 라인 제거됐는지, 본문 수집 성공 로그 있는지 검증
- 결과 메일 발송
"""

import os
import re
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
REPO         = "jinhoo-choi/cafe-monitor"

def log(msg):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {msg}")

def get_latest_log():
    """가장 최근 'workflow_dispatch'(monitor.py 실행) run의 로그 조회
    ※ schedule 트리거 자신의 run은 monitor.py를 실행하지 않으므로 제외해야 함"""
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
    r2 = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs",
        headers={"Authorization": f"token {GH_TOKEN}"},
    )
    job_id = r2.json()["jobs"][0]["id"]
    r3 = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"token {GH_TOKEN}"},
        allow_redirects=True, timeout=30,
    )
    return r3.text

def send_verify_email(status, stats):
    now_kst = datetime.now(KST)
    now_str = now_kst.strftime("%Y.%m.%d %H:%M")

    if status == "pass":
        subject = f"✅ [부정여론봇] 본문 수집 개선 확인 완료 | {now_str} KST"
        color, title = "#2e7d32", "✅ 본문 수집 정상화 확인"
    elif status == "partial":
        subject = f"⚠️ [부정여론봇] 본문 수집 일부 개선 | {now_str} KST"
        color, title = "#e65100", "⚠️ 부분 개선 — 추가 확인 필요"
    else:
        subject = f"❌ [부정여론봇] 본문 수집 여전히 실패 | {now_str} KST"
        color, title = "#b71c1c", "❌ 본문 수집 미개선 — 수동 확인 필요"

    rows = "".join(
        f'<tr><td style="padding:4px 0;font-size:13px;color:#555;">{k}</td>'
        f'<td style="padding:4px 8px;font-size:13px;font-weight:bold;color:#333;">{v}</td></tr>'
        for k, v in stats.items()
    )

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
            verify_patch.py · {now_str} KST
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:24px 28px;">
          <table cellpadding="0" cellspacing="0">{rows}</table>
        </td>
      </tr>
      <tr>
        <td style="background:#f8f8f8;border-top:1px solid #ececec;
                   padding:12px 20px;text-align:center;font-size:11px;color:#aaa;">
          담당자 : 최진후 차장 · Powered by Claude AI 자동 검증
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
    log(f"검증 결과 메일 발송 → {GMAIL_USER}")

def main():
    log("=== verify_patch 시작 ===")
    try:
        log_text = get_latest_log()

        debug_lines    = [l for l in log_text.splitlines() if "[DEBUG]" in l]
        success_lines  = [l for l in log_text.splitlines() if "iframe 본문 수집 성공" in l or "직접 파싱 본문 수집 성공" in l]
        unresolved     = [l for l in log_text.splitlines() if "본문 미수집 - 확인필요 목록 추가" in l]
        body_empty     = [l for l in log_text.splitlines() if "본문 비어 있음" in l]

        stats = {
            "[DEBUG] 라인 잔존":    f"{len(debug_lines)}개 {'✅ 없음' if not debug_lines else '❌ 아직 있음'}",
            "본문 수집 성공":        f"{len(success_lines)}건",
            "본문 미수집(확인필요)": f"{len(unresolved)}건",
            "본문 비어있음":         f"{len(body_empty)}건",
        }

        log(f"결과: {stats}")

        if not debug_lines and len(success_lines) > 0 and len(unresolved) == 0:
            status = "pass"
        elif len(success_lines) > 0 or len(unresolved) < 3:
            status = "partial"
        else:
            status = "fail"

        send_verify_email(status, stats)

    except Exception as e:
        log(f"❌ 오류: {e}")
        traceback.print_exc()
        send_verify_email("fail", {"오류": str(e)[:200]})
        raise

    log("=== verify_patch 완료 ===")

if __name__ == "__main__":
    main()

