"""Collect dated public Naver evidence. Never send notifications or invoke a model."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from negative_queue import Queue

ALIASES = {
    "KIS": ("한국투자증권", "뱅키스", "한투"),
    "MIR": ("미래에셋증권", "엠스톡"),
    "KI": ("키움증권", "영웅문"),
    "SAM": ("삼성증권", "mPOP"),
    "KB": ("KB증권", "마블미니", "M-able"),
    "NH": ("나무증권", "나무앱"),
    "SH": ("신한투자증권", "신한 SOL증권"),
    "TOSS": ("토스증권", "토스 주식"),
}
# Batches are private; only these public article URLs can be sent for review.
def identity(url):
    u = urlsplit(url)
    if u.scheme != "https":
        return None
    if u.hostname in {"blog.naver.com", "m.blog.naver.com"}:
        m = re.fullmatch(r"/([\w.-]+)/(\d+)/?", u.path)
        if m:
            return "blog:" + ":".join(m.groups()), "https://blog.naver.com/" + "/".join(m.groups())
    if u.hostname == "cafe.naver.com" and re.fullmatch(r"/f-e/cafes/\d+/articles/\d+", u.path):
        parts = u.path.split("/")
        return "cafe:" + parts[3] + ":" + parts[5], "https://cafe.naver.com" + u.path
    return None


def published(raw, now):
    text = raw.strip()
    m = re.match(r"^(20\d\d)[.년/-]\s*(\d\d?)[.월/-]\s*(\d\d?)(?!\d)", text)
    if m:
        try:
            return date(*map(int, m.groups()))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d+)\s*(분|시간|일)\s*전", text)
    if m:
        return (now - timedelta(**{ {"분":"minutes","시간":"hours","일":"days"}[m[2]]:int(m[1])})).date()
    return {"방금": now.date(),"어제": now.date()-timedelta(days=1)}.get(text)


def matches(text):
    flat = re.sub(r"\s+", "", text).casefold()
    text_by_issuer = {"KIS":flat.replace("신한투자","")}
    return [issuer for issuer, aliases in ALIASES.items()
            if any(alias.replace(" ","").casefold() in text_by_issuer.get(issuer,flat) for alias in aliases)]


def candidates(browser, bot, state, now, session, last_ok=None, *, board=None):
    public = browser.new_context()
    discovery = browser.new_context(storage_state=session) if session else public
    blog, cafe, article = public.new_page(), discovery.new_page(), public.new_page()
    latest, counts = {}, {}
    channel = "cafe" if board else "blog"
    for issuer, aliases in ALIASES.items():
        stat = counts[issuer + "/" + channel] = {"listed":0, "read":0, "cached":0, "failed":0, "unverifiedSearch":0,"scope":board["id"] if board else "latest search page"}
        last = (last_ok or {}).get(issuer+"/"+channel)
        since = datetime.fromisoformat(last)-timedelta(hours=3) if last else now-timedelta(days=7)
        stat["from"] = since.isoformat(timespec="minutes")
        for term in aliases:
            try:
                if board is None:
                    found = bot.search_naver_blog(blog, term)
                else:
                    coverage = {}
                    found = bot.get_post_list(cafe, board["id"], board["num_id"], [term],
                                              cutoff=since,coverage=coverage)
                    if coverage.get("capped"):
                        stat["unverifiedSearch"] += 1
                if not found:
                    page = cafe if board else blog
                    if not page.get_by_text(re.compile("검색결과가 없습니다|검색 결과가 없습니다|검색 결과가 없어요")).count():
                        stat["unverifiedSearch"] += 1
            except Exception:
                stat["failed"] += 1
                continue
            for hit in found:
                pair = identity(hit["url"])
                if not pair:
                    continue
                key, url = pair
                if key not in latest:
                    latest[key] = (url, board, hit.get("title",""), {})
                matched = latest[key][3]
                if issuer+"/"+channel not in matched:
                    stat["listed"] += 1
                    matched[issuer+"/"+channel] = stat
    records = {}
    for key, (url, board, list_title, matched) in latest.items():
        def count(field):
            for channel_stat in matched.values():
                channel_stat[field] += 1
        previous = state.get(key, {})
        # No edit marker in Naver lists. Revalidate recent pages once daily and
        # reuse the stored content digest across both slots and all issuers.
        if previous.get("checked") == now.date().isoformat() and previous.get("listTitle") == list_title:
            count("cached")
            continue
        try:
            if board:
                details = {}
                _, body = bot.get_post_detail(article, url, board["id"], details)
                title = list_title
            else:
                details = bot.get_blog_detail(article, url.replace("://blog.","://m.blog.",1))
                title, body = details["title"], details["body_full"]
            on = published(details.get("date_str", ""), now)
            if not title or not on or not body or not identity(article.url) or identity(article.url)[0] != key:
                count("failed")
                continue
            count("read")
            digest = hashlib.sha256((title+"\n"+body).encode()).hexdigest()
            state[key] = {"checked":now.date().isoformat(),"listTitle":list_title,"revision":digest}
            if on > now.date() or (now.date()-on).days > 7 or previous.get("revision") == digest:
                continue
            issuers = matches(title+" "+body)
            if not issuers:
                continue
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            around = sorted({j for i,line in enumerate(lines) if matches(line)
                             for j in range(max(i-1,0),min(i+2,len(lines)))})
            excerpt = " ".join(lines[i] for i in around) or " ".join(lines)
            records[key] = {"sourceId":key, "on":on.isoformat(),"revision":digest,
                            "companies":issuers, "url":url, "title":title[:180],"excerpt":excerpt[:1400]}
        except Exception:
            count("failed")
    return records, counts


def main():
    # monitor's legacy import requires mail/API variables. Do not pass any real ones.
    for name in ("GMAIL_USER","GMAIL_APP_PW","NOTIFY_EMAIL","CLAUDE_API_KEY"):
        os.environ[name] = ""
    import monitor as bot
    from playwright.sync_api import sync_playwright
    queue = Queue()
    queue.preflight()
    path = "broker-voice/latest.json"
    old, sha = queue.get(path)
    old = old or {}
    now = datetime.now(bot.KST)
    cutoff = (now.date()-timedelta(days=9)).isoformat()
    state = {}
    pending = {}
    for kind, target in (("statePages",state),("pages",pending)):
        for signature in old.get(kind,[]):
            batch,_ = queue.get("broker-voice/batches/"+signature+".json")
            if batch is None:
                raise RuntimeError("private batch missing")
            if kind=="statePages":
                target.update({k:v for k,v in batch if v["checked"]>=cutoff})
            else:
                target.update({row["sourceId"]:row for row in batch if row["on"]>=cutoff})
    session = os.environ.pop("VOICE_NAVER_STATE","")
    # Per-source cursors allow new and previously unavailable boards to catch up.
    boards = {b["id"]: b for b in bot.CAFES}
    scopes = [None, *boards.values()]
    cursors = dict(old.get("sourceSuccess", {}))
    session = json.loads(session) if session else None

    def run_scope(board):
        source = board["id"] if board else "blog"
        prefix = "cafe:"+board["num_id"]+":" if board else "blog:"
        local_state = {k:v for k,v in state.items() if k.startswith(prefix)}
        since = cursors.get(source, {} if board else old.get("lastSuccess", {}))
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                rows, counts = candidates(browser, bot, local_state, now, session, since, board=board)
            finally:
                browser.close()
        return source, local_state, rows, counts

    checked = now.isoformat(timespec="seconds")
    per_source, stats, revisions = {}, {}, {}
    # Source IDs and article IDs occupy disjoint namespaces across boards.
    # Collect isolated sources concurrently; only this thread writes the index.
    with open(os.devnull,"w") as sink, contextlib.redirect_stdout(sink), ThreadPoolExecutor(max_workers=3) as pool:
        for source, local_state, rows, counts in pool.map(run_scope, scopes):
            revisions.update(rows)
            pending.update(rows)
            cursors.setdefault(source, {})
            for channel, stat in counts.items():
                if not stat["failed"] and not stat["unverifiedSearch"]:
                    cursors[source][channel] = checked
                total = stats.setdefault(channel, {"scope":"all registered cafes" if source!="blog" else "latest search page",
                                                  "from":stat["from"]})
                total["from"] = min(total["from"], stat["from"])
                for field in ("listed","read","cached","failed","unverifiedSearch"):
                    total[field] = total.get(field,0) + stat[field]
            # Keep state immutable during parallel scans. Merge completed shards below.
            per_source[source] = {"channels": counts, "state": local_state}
    for shard in per_source.values():
        state.update(shard.pop("state"))
    index = {"checkedAt":checked, "channels":stats, "sourceStats":per_source, "sourceSuccess":cursors}
    last_ok = dict(old.get("lastSuccess",{}))
    for channel, stat in stats.items():
        if not stat["failed"] and not stat["unverifiedSearch"]:
            last_ok[channel] = checked
    index["lastSuccess"] = last_ok
    for kind, rows, size in (("statePages",sorted(state.items()),150),
                             ("pages",sorted(pending.values(),key=lambda r:(r["on"],r["sourceId"])),12)):
        index[kind] = []
        for start in range(0,len(rows),size):
            batch = rows[start:start+size]
            signature = hashlib.sha256(json.dumps(batch,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            location = "broker-voice/batches/"+signature+".json"
            if signature not in old.get(kind,[]):
                current,_ = queue.get(location)
                if current is None:
                    queue.put(location,batch)
            index[kind].append(signature)
    queue.put(path,index,sha)
    if queue.get(path)[0] != index:
        raise RuntimeError("private index readback mismatch")
    print("verified channels",len(stats),"new revisions",len(revisions),"cafes searched",len(boards),"model calls 0")
    if all(not s["read"] and not s["cached"] and (s["failed"] or s["unverifiedSearch"]) for s in stats.values()):
        raise RuntimeError("all sources unverified; private diagnostics saved")


if __name__ == "__main__":
    main()
