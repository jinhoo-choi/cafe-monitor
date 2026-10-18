"""기존 메일 HTML의 순수 렌더러. API·SMTP·이력을 호출하지 않는다."""
import html
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

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


def render_email(alert_posts, crawled_count, keyword_count, unresolved_posts=None, now_kst=None, notice_html="", test_badge="", review_label="본문 미수집", review_note=""):
    unresolved_posts = unresolved_posts or []
    total    = len(alert_posts)
    now_kst  = now_kst or datetime.now(KST)
    now_str  = now_kst.strftime("%Y.%m.%d %H:%M")
    if total > 0:
        subject      = f"[부정여론 탐지] {now_kst.strftime('%m')}월 {now_kst.strftime('%d')}일 {now_kst.strftime('%H')}시 기준"
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
        banner_badge = f"확인필요 · {review_label} {len(unresolved_posts)}건"
        banner_title = f"{review_label} {len(unresolved_posts)}건 — 부정 여부 확인 필요"

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
                          color:#888;letter-spacing:0.6px;">⚠️ 확인필요 · {review_label} {len(unresolved_posts)}건</p>
                <table cellpadding="0" cellspacing="0" border="0" width="100%">
                  {rows_html}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 16px;font-size:11px;color:#bbb;">
                {review_note}
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
           style="max-width:600px;width:100%;background:#fff;border:1px solid #d5d9e0;">
      <tr>
        <td style="background:#c62828;padding:20px 28px;">
          <p style="margin:0 0 6px 0;font-size:10px;font-weight:bold;
                    color:#ffcdd2;letter-spacing:0.8px;">{test_badge}{banner_badge}</p>
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
            {now_str} KST &nbsp;&middot;&nbsp; ChatGPT 구독 분석
          </p>
        </td>
      </tr>
      {notice_html}<tr><td style="padding:0;">{cards_html}</td></tr>
      {unresolved_html}
      <tr>
        <td style="background:#f8f8f8;border-top:1px solid #ececec;
                   padding:12px 20px;text-align:center;
                   font-size:11px;color:#aaa;line-height:1.8;">
          탐지 키워드 : 한국투자증권, 한투, 뱅키스<br>
          담당자 : 최진후 차장<br>
          <span style="color:#ccc;">Powered by ChatGPT 구독 · 자동 수집 및 감성 분석</span>
          <br><br>
          <table cellpadding="0" cellspacing="0" border="0" style="margin:8px auto 0;border-collapse:collapse;">
            <tr>
              <td style="font-size:9px;color:#999;padding:0 0 4px 0;text-align:left;letter-spacing:0.5px;">
                ■ 부정 강도 기준 (ChatGPT 구독 채점)
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
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">0<br>무관</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">1~3<br>경미</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">4~6<br>불만</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">7~8<br>비판</td>
                    <td style="font-size:8px;color:#aaa;text-align:center;padding-top:3px;">9~10<br>강한 비판</td>
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
    return html_body


def _metadata(source, coverage):
    post_time = None
    if source.get('published_at'):
        try:
            post_time = datetime.fromisoformat(source['published_at'])
        except (ValueError, TypeError):
            pass
    cafe = source['post_id'].split(':', 1)[0]
    name = ('네이버 블로그' if source.get('source') == 'blog' else
            next((row.get('name', cafe) for row in coverage if row.get('source') == cafe), cafe))
    return {'title': source['title'], 'url': source['url'],
            'source': source.get('source', 'cafe'), 'cafe_name': html.escape(name),
            'post_time': post_time, 'date_str': html.escape(source.get('date_raw') or '')}


def _keywords(source):
    text = source['title'] + ' ' + source['body_excerpt']
    values = [a for a in ('한국투자증권', '뱅키스', 'BanKIS') if a.casefold() in text.casefold()]
    if re.search(r'(?<![가-힣A-Za-z])한투', text):
        values.append('한투')
    return ' · '.join(values)


def _notice(text):
    if not text:
        return ''
    return ('<tr><td style="padding:12px 28px;background:#fff8e1;'
            'color:#8a5a00;font-size:12px;line-height:1.7;">'
            + html.escape(text) + '</td></tr>')


def _status_email(crawled_count, keyword_count, notices, instant, test):
    color = '#e65100' if notices else '#2e7d32'
    heading = '수집 상태 확인 필요' if notices else '탐지 없음 - 정상 실행'
    text = notices or '정상 실행되었으나 부정 탐지 게시글이 없습니다.'
    label = '[TEST] 본인 전용 · 운영 발송 0 · 운영 DB 쓰기 0' if test else '네이버 카페 부정여론 탐지봇'
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f2f5;"><tr><td align="center" style="padding:32px 16px;">
<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:#fff;border:1px solid #d5d9e0;">
<tr><td style="background:{color};padding:20px 28px;"><p style="margin:0 0 4px;font-size:16px;font-weight:bold;color:#fff;">{heading}</p>
<p style="margin:0;font-size:11px;color:#fff;">{label} · {instant.strftime('%Y.%m.%d %H:%M')} KST</p></td></tr>
<tr><td style="padding:24px 28px;font-size:13px;color:#555;line-height:1.8;">{html.escape(text)}<br><br>크롤링 {crawled_count}건 · 키워드 탐지 {keyword_count}건</td></tr>
<tr><td style="background:#f8f8f8;border-top:1px solid #ececec;padding:12px 20px;text-align:center;font-size:11px;color:#aaa;line-height:1.8;">탐지 키워드 : 한국투자증권, 한투, 뱅키스<br>담당자 : 최진후 차장<br>Powered by ChatGPT 구독 · 자동 수집 및 감성 분석</td></tr>
</table></td></tr></table></body></html>'''


def render(job, result, inputs, posts, selected, deferred, production=False, executive=False):
    collection = job['input'].get('collection', {})
    coverage = collection.get('coverage', [])
    crawled_count = sum(row.get('listed', 0) for row in coverage) if coverage else len(inputs)
    keyword_count = len(inputs)
    notices = []
    failures = sum(row.get('status') != 'ok' for row in coverage)
    if failures:
        notices.append(f'수집원 오류·접근 보류 {failures}곳 — 수집 실패를 탐지 없음으로 간주하지 않습니다.')
    if any(row.get('status') == 'cookie_required' for row in coverage):
        notices.append('네이버 로그인 쿠키 확인 필요 — NAVER_COOKIES_JSON 재등록 필요')
    elif collection.get('cookie_expiring'):
        notices.append('네이버 쿠키 7일 내 만료 예정(' + ', '.join(collection['cookie_expiring']) + ') — 재등록 준비')
    if collection.get('cookie_auth_status') and collection.get('cookie_auth_verified') is not True:
        notices.append('회원 전용 본문 인증 미확인 — 쿠키 이름 존재나 공개 홈 접근을 정상 인증으로 간주하지 않습니다.')
    if deferred:
        notices.append(f'이월 이슈 {len(deferred)}건 — 다음 회차 확인 대상입니다.')
    alerts = []
    for group in selected:
        identifier = group['representative_post_id']
        source, post = inputs[identifier], posts[identifier]
        value = _metadata(source, coverage)
        value.update(matched_kw=_keywords(source), score=post['score'], summary=post['summary'], reply=post['reply_draft'])
        value['related_posts'] = [_metadata(inputs[m], coverage) for m in group['member_post_ids'] if m != identifier]
        value['common_keywords'] = []
        alerts.append(value)
    reviews = [_metadata(inputs[identifier], coverage) for identifier, post in posts.items() if post['verdict'] == 'needs_review']
    instant = datetime.now(KST)
    if executive:
        if not alerts:
            raise ValueError('임원용 메일에는 확인된 부정 이슈가 필요합니다.')
        return render_email(alerts, crawled_count, keyword_count, now_kst=instant)
    if not alerts and not reviews:
        return _status_email(crawled_count, keyword_count, ' '.join(notices), instant, not production)
    missing = sum(source.get('body_status') != 'collected' for identifier, source in inputs.items() if posts[identifier]['verdict'] == 'needs_review')
    review_label = '본문 미수집' if missing == len(reviews) else '본문·귀속 확인'
    review_note = ('본문을 가져오지 못해 AI 분석이 생략된 게시글입니다. 직접 확인해주세요.'
                   if missing == len(reviews) else '본문 또는 회사 귀속을 확인하지 못한 게시글입니다. 정상으로 간주하지 않고 재확인합니다.')
    return render_email(alerts, crawled_count, keyword_count, reviews,
                        now_kst=instant, notice_html=_notice(' '.join(notices)),
                        test_badge='[TEST] 본인 전용 · ' if not production else '',
                        review_label=review_label, review_note=review_note)

