#!/usr/bin/env python3
"""CanLII decision crawler — Camoufox engine, per karthikbala/cloak-setup.

Template shape: probe -> discover -> fetch -> store -> resume.
Rules encoded (AGENTS.md):
  - sticky DataImpulse residential session (__cr.ca), rotate every ROTATE_EVERY docs
  - fresh session id = IP lottery; redraw up to MAX_DRAWS with cooldown
  - DataDome walls self-clear on a good IP: render, wait ~45s, cool 20s, retry once
  - never lower PACE (4s); 429 -> stop and cool down
  - content-addressed storage + captures.jsonl provenance; resume is free
  - secrets only via environment variables

Run:
  DATAIMPULSE_USER=... DATAIMPULSE_PASS=... python3 canlii_crawler.py
"""
import fcntl
import datetime
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

OUT = Path(os.environ.get("OUT", "canlii_data"))
TARGET = "https://www.canlii.org/en/"
GOAL = int(os.environ.get("GOAL", "100"))
WORKER = os.environ.get("WORKER", "1")      # each worker: own session pin
DISCOVER_TARGET = 120          # collect a few more URLs than needed
PACE = 4.0
ROTATE_EVERY = 40
MAX_DRAWS = int(os.environ.get("MAX_DRAWS", "8"))
MAX_SCREEN_DRAWS = 16
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "12"))
DRAW_COOLDOWN_S = 90
WALL_COOLDOWN_S = 20

# DataDome interstitial signatures (precise; bare 'captcha' would false-positive
# on legit pages that reference the DataDome tag)
WALL_MARKERS = ("captcha-delivery.com/interstitial", "cmsg", "rt':'c'", 'rt\':\'c',
                "please enable js and disable any ad blocker")
# hard "restricted" block = burned exit IP; it never self-clears -> fail fast
RESTRICTED_MARKERS = ("temporarily restricted", "unusual activity",
                      "access is restricted")

AJAX_SEARCH = ("https://www.canlii.org/en/search/ajaxSearch.do"
               "?indexLang=en&type=decision&page={pg}")
DOC_PATH_RE = re.compile(r"(/en/[a-z-]+/[a-z0-9-]+/doc/\d{4}/[^\"\s\\]+\.html)")


def is_wall(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in WALL_MARKERS)


def proxy_for(sessid: str) -> dict:
    user = os.environ["DATAIMPULSE_USER"]
    pw = os.environ["DATAIMPULSE_PASS"]
    return {
        "server": "http://gw.dataimpulse.com:823",
        "username": f"{user}__cr.ca;sessid.{sessid}",
        "password": pw,
    }


def is_restricted(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in RESTRICTED_MARKERS)


def wait_self_clear(page, url, tries=(10, 10, 15, 20, 20)) -> tuple:
    """Render the page and let DataDome's device check clear itself (~75s).

    Fast-fail: the 'Access restricted' hard block means the exit IP is burned
    (prior bot activity) — it never self-clears, so don't wait the full window.
    Returns (True, '') on pass, (False, reason) on failure."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        return False, f"goto: {type(e).__name__}"
    for waited in tries:
        time.sleep(waited)
        html = page.content()
        if not is_wall(html) and not is_restricted(html):
            return True, ""
        if is_restricted(html):
            return False, "restricted (burned exit IP)"
    return False, "interstitial never cleared"


OS_PROFILES = ["macos", "windows", "linux"]


def fetch_js(page, url: str) -> dict:
    """In-page fetch so the request carries the session identity."""
    return page.evaluate("""async (u) => {
        const r = await fetch(u, {credentials: 'include',
                                  headers: {'Accept': 'text/html'},
                                  signal: AbortSignal.timeout(60000)});
        return {status: r.status, text: await r.text()};
    }""", url)


def fetch_with_wall_retry(page, url: str):
    res = fetch_js(page, url)
    if is_wall(res["text"]) or is_restricted(res["text"]):
        ok, _ = wait_self_clear(page, url)
        if not ok:
            return None
        time.sleep(WALL_COOLDOWN_S)
        res = fetch_js(page, url)
        if is_wall(res["text"]) or is_restricted(res["text"]):
            return None
    return res


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%FT%TZ")


def valid_decision(text):
    # CanLII's judgment containers; reject search pages and soft blocks.
    return (not is_wall(text) and not is_restricted(text)
            and bool(re.search(r'id=["\'](?:judgment|originalDocument|document-content)["\']', text, re.I)))


def store(body: bytes, record: dict) -> str:
    digest = hashlib.sha256(body).hexdigest()
    # One lock covers deduplication, goal accounting, object and log writes.
    with (OUT / "captures.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if record.get("status") == "ok":
            seen = load_seen()
            if record["url"] in seen or len(seen) >= GOAL:
                return digest
        obj = OUT / "objects" / digest[:2] / f"{digest}.html"
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(body)
        with (OUT / "captures.jsonl").open("a") as fh:
            fh.write(json.dumps({**record, "sha256": digest,
                                 "object": str(obj.relative_to(OUT))}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return digest


def extract_doc_links(html: str, base: str = "https://www.canlii.org") -> set:
    links = set()
    for m in re.finditer(r'href="([^"]*?/doc/[^\s"#?]*?\.html)"', html):
        u = m.group(1)
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = base + u
        if not u.startswith("https://www.canlii.org"):
            continue
        if not re.search(r"/doc/\d{4}/", u):
            continue
        links.add(u)
    return links


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}][w{WORKER}] {msg}", flush=True)


def load_seen() -> set:
    seen = set()
    logf = OUT / "captures.jsonl"
    if logf.exists():
        for line in logf.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") == "ok":
                seen.add(r["url"])
    return seen


def load_urls() -> list:
    f = OUT / "urls.txt"
    if f.exists():
        return [l.strip() for l in f.read_text().splitlines() if l.strip()]
    return []


def discover(page) -> int:
    """Collect decision URLs from CanLII's own search API (ajaxSearch.do),
    called in-page so the request carries the session identity."""
    found = set()
    for pg in range(1, 7):
        if len(found) >= DISCOVER_TARGET:
            break
        try:
            res = fetch_js(page, AJAX_SEARCH.format(pg=pg))
        except Exception as e:
            log(f"discover: page {pg} fetch error: {type(e).__name__}")
            break
        if res["status"] != 200 or is_wall(res["text"]):
            store(res["text"].encode(), {"at": utc_now(), "url": AJAX_SEARCH.format(pg=pg),
                                        "status": "discovery_error", "http": res["status"]})
            log(f"discover: page {pg} -> HTTP {res['status']}"
                f"{' WALL' if is_wall(res['text']) else ''}")
            if not found:
                return 0
            break
        paths = set(DOC_PATH_RE.findall(res["text"]))
        added = len(paths - found)
        found |= paths
        log(f"discover: page {pg} -> +{added} (total {len(found)})")
        time.sleep(PACE + random.uniform(0.0, 2.5))
    if not found:
        return 0
    (OUT / "urls.pending").write_text(
        "\n".join(sorted("https://www.canlii.org" + p for p in found)) + "\n")
    (OUT / "urls.pending").replace(OUT / "urls.txt")
    return len(found)


def collect(page, urls, seen) -> str:
    """Fetch decision pages. Returns 'done', 'rotate', or 'wall'."""
    done = 0
    for url in urls:
        seen = load_seen()
        if len(seen) >= GOAL:
            return "done"
        if url in seen:
            continue
        res = fetch_with_wall_retry(page, url)
        if res is None:
            log(f"wall persisted at {url} — session burned")
            return "wall"
        if res["status"] == 429:
            log("HTTP 429 — stopping this run")
            store(res["text"].encode(), {"at": utc_now(), "url": url, "status": "error", "http": 429})
            raise SystemExit(29)
        if res["status"] == 200 and valid_decision(res["text"]):
            body = res["text"].encode()
            title = ""
            m = re.search(r"<title>(.*?)</title>", res["text"], re.S | re.I)
            if m:
                title = m.group(1).strip()
            store(body, {"at": utc_now(), "url": url,
                         "status": "ok", "http": res["status"],
                         "title": title})
            seen.add(url)
            done += 1
            log(f"{done} this session / {len(seen)} total — {title[:60]}")
        else:
            log(f"{url} -> HTTP {res['status']}{' WALL' if is_wall(res['text']) else ''} — skipped")
            store(res["text"].encode(), {"at": utc_now(), "url": url,
                                         "status": "error", "http": res["status"]})
        time.sleep(PACE + random.uniform(0.0, 2.5))
        if len(seen) >= GOAL:
            log(f"GOAL REACHED: {len(seen)} decisions stored")
            return "done"
        if done >= ROTATE_EVERY:
            log(f"{done} docs this session — rotating")
            return "rotate"
    return "done"


DC_KEYWORDS = ("leaseweb", "ovh", "hetzner", "digitalocean", "linode", "vultr",
               "amazon", "aws", "google", "azure", "choopa", "cogent", "zayo",
               "level3", "lumen", "centurylink", "host", "cloud", "server",
               "datacamp", "m247", "twent", "oxio")


def pre_screen(sessid: str) -> tuple:
    """Cheap curl check before any browser launch: is this session's exit IP
    CANADIAN and RESIDENTIAL? The pool leaks non-CA exits (observed UK BT) and
    datacenter exits (observed Leaseweb) — both are hard-blocked by DataDome.
    Rejecting here costs ~5s, not 2.5min."""
    import json as _json
    import subprocess
    user = os.environ["DATAIMPULSE_USER"]
    pw = os.environ["DATAIMPULSE_PASS"]
    url = (f"http://{user}__cr.ca;sessid.{sessid}:{pw}"
           f"@gw.dataimpulse.com:823")
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "20", "-x", url,
             "https://ipinfo.io/json"],
            capture_output=True, timeout=30).stdout
        info = _json.loads(out)
        ip = info.get("ip", "?")
        country = info.get("country", "?")
        org = (info.get("org") or "")
        low = org.lower()
        if country != "CA":
            return False, f"{ip} {country} {org[:40]} — non-CA"
        # hosting/proxy/type check via ip-api (direct, cheap)
        api = subprocess.run(
            ["curl", "-s", "--max-time", "15",
             f"http://ip-api.com/json/{ip}?fields=proxy,hosting,mobile,isp"],
            capture_output=True, timeout=20).stdout
        a = _json.loads(api)
        isp = (a.get("isp") or "")[:40]
        if a.get("hosting") or a.get("proxy"):
            return False, f"{ip} {org[:40]} — DATACENTER/hosting exit"
        if any(k in low for k in DC_KEYWORDS):
            return False, f"{ip} {org[:40]} — DC keyword"
        return True, f"{ip} CA {isp}"
    except Exception as e:
        return False, f"lookup failed: {type(e).__name__}"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    from camoufox.sync_api import Camoufox

    # headed on the real display when one exists (headless is a known
    # detection signal for DataDome per ScrapFly's 2026 study)
    headless = os.environ.get("HEADLESS", "")
    if headless:
        headless = headless.lower() in ("1", "true", "yes")
    else:
        headless = not os.environ.get("DISPLAY")

    seen = load_seen()
    urls = load_urls()
    log(f"resume state: {len(seen)} stored, {len(urls)} discovered URLs")

    sessid = os.environ.get("DI_SESSID") or ""
    pin_f = OUT / f"session_w{WORKER}.txt"
    if not sessid and pin_f.exists():
        sessid = pin_f.read_text().strip()
        log(f"reusing pinned session {sessid} (probe will re-validate it)")
    if not sessid:
        sessid = os.urandom(5).hex()
    # failed draws (blocked probe) count against MAX_DRAWS; planned rotations
    # after ROTATE_EVERY docs are a normal part of the run and must not.
    failed_draws = 0
    crashes = 0
    sessions = 0
    while True:
        seen = load_seen()
        if len(seen) >= GOAL:
            log(f"COMPLETE: {len(seen)} decisions stored")
            return
        if sessions >= MAX_SESSIONS:
            raise SystemExit("Total browser session limit reached; stopping")
        sessions += 1
        if failed_draws >= MAX_DRAWS:
            raise SystemExit(f"{MAX_DRAWS} failed draws in a row with "
                             f"{GOAL - len(seen)} docs left — proxy pool hostile; stop")
        if failed_draws:
            log(f"cooldown {DRAW_COOLDOWN_S}s before next draw ({failed_draws} failed so far)")
            time.sleep(DRAW_COOLDOWN_S)
            sessid = os.urandom(5).hex()  # redraw: never retry the same burned IP
        # pre-screen: only hand a session to the browser if its exit IP is CA
        ps_tries = 0
        while True:
            ok, info = pre_screen(sessid)
            log(f"pre-screen {sessid}: {info}")
            if ok:
                break
            ps_tries += 1
            if ps_tries >= MAX_SCREEN_DRAWS:
                raise SystemExit("Proxy screening exhausted; stopping")
            sessid = os.urandom(4).hex()
            time.sleep(5)
        log(f"attempt: session {sessid} — launching Camoufox "
            f"(headless={headless}, geoip, humanize)")
        profile = OS_PROFILES[failed_draws % len(OS_PROFILES)]  # kept for diagnostics

        try:
            with Camoufox(headless=headless, geoip=True, humanize=True,
                          **({} if os.environ.get("CAMOUFOX_OS") else {}),
                          **({"os": os.environ["CAMOUFOX_OS"]}
                             if os.environ.get("CAMOUFOX_OS") else {}),
                          **({"locale": os.environ["CAMOUFOX_LOCALE"].split(",")}
                             if os.environ.get("CAMOUFOX_LOCALE") else {}),
                          proxy=proxy_for(sessid)) as browser:
                page = browser.new_page()
                geo = page.evaluate(
                    "[navigator.language, Intl.DateTimeFormat()"
                    ".resolvedOptions().timeZone]")
                log(f"probe: home page (geo: {geo})")
                ok, reason = wait_self_clear(page, TARGET)
                if not ok:
                    store(page.content().encode(), {"at": utc_now(), "url": TARGET, "status": "blocked", "reason": reason})
                    failed_draws += 1
                    log(f"probe blocked ({reason}) "
                        f"(failed draw {failed_draws}/{MAX_DRAWS}) — redrawing")
                    continue
                failed_draws = 0  # a good draw resets the streak
                # probe passed: this session id is a known-good asset — pin it
                pin_f.write_text(sessid)
                log(f"probe PASSED — session {sessid} pinned")

                urls = load_urls()
                log(f"shared candidate list: {len(urls)} URLs")
                if len(urls) < DISCOVER_TARGET // 2:
                    # one worker discovers; others wait for urls.txt
                    lock = OUT / "discovery.lock"
                    try:
                        discovery_lock = lock.open("a")
                        fcntl.flock(discovery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        owner = True
                    except BlockingIOError:
                        discovery_lock.close()
                        owner = False
                    if owner:
                        try:
                            log("discovering decision URLs in-browser...")
                            n = discover(page)
                            if not n:
                                log("discovery failed — redrawing session")
                                failed_draws += 1
                                continue
                            log(f"discovery OK: {n} URLs")
                        finally:
                            fcntl.flock(discovery_lock, fcntl.LOCK_UN)
                            discovery_lock.close()
                        urls = load_urls()
                    else:
                        log("another worker is discovering — waiting for urls.txt")
                        for _ in range(120):
                            time.sleep(5)
                            urls = load_urls()
                            if urls:
                                break
                        if not urls:
                            log("timed out waiting for discovery — redrawing")
                            failed_draws += 1
                            continue

                urls = urls[(int(WORKER) - 1)::int(os.environ.get("NWORKERS", "1"))]
                log(f"url slice: {len(urls)} candidates for w{WORKER}")
                outcome = collect(page, urls, seen)
                log(f"session outcome: {outcome}")
                if outcome == "done":
                    if len(load_seen()) >= GOAL:
                        return
                    raise SystemExit("Worker candidates exhausted before global goal")
                if outcome == "wall":
                    store(page.content().encode(), {"at": utc_now(), "url": page.url, "status": "blocked"})
                    raise SystemExit("Persistent wall; stopping worker")
                elif outcome == "rotate":
                    sessid = os.urandom(5).hex()  # planned rotation, fresh draw
        except Exception as e:
            # browser crash / target closed / proxy hiccup — a failed draw,
            # never fatal: resume from captures.jsonl is free
            failed_draws += 1
            crashes += 1
            log(f"browser session died ({type(e).__name__}) "
                f"— failed draw {failed_draws}/{MAX_DRAWS}")
            if crashes >= 3 and headless is not True:
                # headed browser keeps dying — fall back to headless
                log("3 crashes in headed mode — falling back to headless=True")
                headless = True


if __name__ == "__main__":
    main()
