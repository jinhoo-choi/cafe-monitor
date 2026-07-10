"""
test_blog_fetch.py
- blog.naver.com 실제 접근 가능 여부 검증용 1회성 테스트
- Playwright로 지정 URL 접근 → 본문 추출 시도 → 결과 로그 출력
- 검증 후 이 파일은 삭제 예정
"""

from playwright.sync_api import sync_playwright

TEST_URL = "https://m.blog.naver.com/lfg79/224341880031"

BODY_SELECTORS = [
    ".se-main-container",
    "#postViewArea",
    ".post_ct",
    "#viewTypeSelector",
    ".se-component-content",
]

def log(msg):
    print(f"[TEST] {msg}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        )

        log(f"접근 시도: {TEST_URL}")
        try:
            page.goto(TEST_URL, wait_until="domcontentloaded", timeout=20000)
            log(f"✅ 페이지 로드 성공 - 최종 URL: {page.url}")
        except Exception as e:
            log(f"❌ 페이지 로드 실패: {e}")
            browser.close()
            return

        page.wait_for_timeout(3000)

        # 프레임 구조 확인 (카페처럼 iframe 안에 있을 수도 있음)
        frames = [(f.url or "")[:80] for f in page.frames]
        log(f"프레임 수: {len(frames)}")
        for f in frames:
            log(f"  frame: {f}")

        # 제목 추출 시도
        title = ""
        try:
            title_el = page.query_selector(".se-title-text, .htitle, h3.se_textarea")
            if title_el:
                title = title_el.inner_text().strip()
                log(f"✅ 제목 추출 성공: {title[:60]}")
            else:
                log("⚠️ 제목 셀렉터 매칭 실패")
        except Exception as e:
            log(f"❌ 제목 추출 오류: {e}")

        # 본문 추출 시도 (메인 페이지 직접)
        body = ""
        for sel in BODY_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if text:
                        body = text
                        log(f"✅ 본문 수집 성공 (selector={sel}, {len(text)}자)")
                        log(f"  미리보기: {text[:150]}")
                        break
            except Exception:
                continue

        if not body:
            log("⚠️ 메인 페이지에서 본문 미발견 - iframe 확인 필요")
            # 혹시 iframe 안에 있는지 재확인
            for frame in page.frames:
                for sel in BODY_SELECTORS:
                    try:
                        el = frame.query_selector(sel)
                        if el:
                            text = el.inner_text().strip()
                            if text:
                                log(f"✅ iframe에서 본문 발견 (frame={frame.url[:60]}, selector={sel})")
                                log(f"  미리보기: {text[:150]}")
                                body = text
                                break
                    except Exception:
                        continue
                if body:
                    break

        if not body:
            log("❌ 본문 수집 완전 실패 - HTML 구조 재확인 필요")
            # 전체 HTML 일부 덤프 (구조 파악용)
            html = page.content()
            log(f"HTML 길이: {len(html)}자")
            log(f"HTML 일부: {html[:500]}")

        log("=== 테스트 완료 ===")
        browser.close()

if __name__ == "__main__":
    main()
