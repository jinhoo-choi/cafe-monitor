@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "COOKIE_REFRESH_DIR=%USERPROFILE%\naver-cookie-refresh"
if not exist "%COOKIE_REFRESH_DIR%" mkdir "%COOKIE_REFRESH_DIR%"
cd /d "%COOKIE_REFRESH_DIR%" || goto failed

where py >nul 2>&1
if errorlevel 1 goto no_python
if not exist ".venv\Scripts\python.exe" py -m venv .venv
if not exist ".venv\Scripts\python.exe" goto failed
set "COOKIE_REFRESH_PYTHON=%COOKIE_REFRESH_DIR%\.venv\Scripts\python.exe"

"%COOKIE_REFRESH_PYTHON%" -c "import playwright" >nul 2>&1
if errorlevel 1 "%COOKIE_REFRESH_PYTHON%" -m pip install playwright
if errorlevel 1 goto failed
"%COOKIE_REFRESH_PYTHON%" -m playwright install chromium
if errorlevel 1 goto failed

set "COOKIE_REFRESH_FILE=%COOKIE_REFRESH_DIR%\naver_cookies_%RANDOM%_%RANDOM%.json"
set "COOKIE_REFRESH_SECRET_FILE=%COOKIE_REFRESH_FILE%.secret.json"
echo.
echo 1. 열린 브라우저에서 네이버 로그인과 필요한 추가 인증을 직접 완료하세요.
echo 2. 같은 브라우저에서 회원 카페 게시글 본문이 열리는지 확인하세요.
echo 3. 브라우저와 Playwright Inspector 창을 닫으면 쿠키 파일이 저장됩니다.
echo 비밀번호나 쿠키 원문을 채팅에 보내지 마세요.
"%COOKIE_REFRESH_PYTHON%" -m playwright codegen --save-storage="%COOKIE_REFRESH_FILE%" https://nid.naver.com/nidlogin.login
if errorlevel 1 goto failed

"%COOKIE_REFRESH_PYTHON%" -c "import os,json,time,math; from pathlib import Path; s=json.loads(Path(os.environ['COOKIE_REFRESH_FILE']).read_text(encoding='utf-8-sig')); assert isinstance(s,dict) and isinstance(s.get('cookies'),list) and isinstance(s.get('origins',[]),list), 'Invalid storage JSON'; cs=[c for c in s['cookies'] if isinstance(c,dict) and isinstance(c.get('value'),str) and c['value'].strip() and (str(c.get('domain','')).lstrip('.').lower()=='naver.com' or str(c.get('domain','')).lstrip('.').lower().endswith('.naver.com')) and isinstance(c.get('expires',-1),(int,float)) and not isinstance(c.get('expires',-1),bool) and math.isfinite(c.get('expires',-1)) and (c.get('expires',-1)==-1 or c.get('expires',0)>time.time())]; assert {'NID_AUT','NID_SES'} <= {c.get('name') for c in cs}, 'Valid login cookies missing'; v=json.dumps(s,ensure_ascii=False,separators=(',',':')); assert len(v.encode('utf-8'))<=48*1024, 'GitHub Secret size limit exceeded'; Path(os.environ['COOKIE_REFRESH_SECRET_FILE']).write_text(v,encoding='utf-8'); print('OK: JSON and required cookie fields checked. Authentication verification is still required.')"
if errorlevel 1 goto failed
clip < "%COOKIE_REFRESH_SECRET_FILE%"
if errorlevel 1 goto failed
start "" "https://github.com/jinhoo-choi/cafe-monitor/settings/secrets/actions"

echo.
echo 쿠키 JSON 전체를 클립보드에 복사했습니다.
echo GitHub에서 NAVER_COOKIES_JSON 수정 버튼을 누르세요.
echo Value에 Ctrl+V 후 Update secret을 누르세요.
echo 파일 이름이나 경로가 아니라 JSON 내용을 붙여넣습니다.
echo.
echo GitHub에서 저장을 완료한 다음 아무 키나 누르세요.
pause >nul
powershell -NoProfile -Command "Set-Clipboard -Value ''" >nul 2>&1
echo.
echo 이제 담당 대화에 'Secret 저장 완료. 쿠키 인증 검증해줘'라고 알려주세요.
echo 이 CMD는 Secret 저장 성공이나 회원 본문 인증 성공을 자동 판정하지 않습니다.
echo 원본 파일: %COOKIE_REFRESH_FILE%
echo Secret용 파일: %COOKIE_REFRESH_SECRET_FILE%
goto done

:no_python
echo Python의 py 명령을 찾지 못했습니다. Python 설치 후 다시 실행하세요.
goto done

:failed
echo.
echo 작업이 중단됐습니다. 이 실행의 쿠키가 등록됐다고 판단하지 마세요.
echo 화면에 표시된 오류를 확인하세요. 쿠키 원문은 공유하지 마세요.

:done
echo.
echo 종료하려면 아무 키나 누르세요.
pause >nul
endlocal
