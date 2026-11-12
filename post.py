#!/usr/bin/env python3
"""Dvia Concepts auto-poster.

Reads the daily post queue (a public JSON file that Claude updates every morning),
posts every item that is due to Facebook Page, Instagram (Reels) and YouTube Shorts,
and records results in log/posted.json so nothing is posted twice.

Runs on GitHub Actions every 15 minutes (see .github/workflows/post.yml).

Queue format (queue.json):
{
  "posts": [
    {
      "id": "2026-11-13-ad1",                 # unique id
      "time": "2026-11-13T08:00:00+03:00",    # when to post (ISO 8601 with offset)
      "title": "Headline of the ad",          # YouTube title (max 100 chars)
      "caption": "Caption text + contacts + #tags",
      "video_url": "https://drive.google.com/uc?export=download&id=...",
      "platforms": ["facebook", "instagram", "youtube"]
    }
  ]
}

Secrets (GitHub > repo > Settings > Secrets and variables > Actions):
  QUEUE_URL              public URL of queue.json
  FB_PAGE_ID             Facebook Page id
  FB_PAGE_TOKEN          long-lived Page access token
  IG_USER_ID             Instagram Business account id (linked to the Page)
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   YouTube OAuth
Optional: GRAPH_VERSION (default v23.0), DRY_RUN=1 (log only, post nothing)
A platform whose secrets are missing is skipped.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

import requests

GRAPH = os.environ.get("GRAPH_VERSION", "v23.0")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log", "posted.json")
LATE_LIMIT_HOURS = 6      # don't post something more than 6h after its slot
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def env(name):
    v = os.environ.get(name, "").strip()
    return v or None


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def load_log():
    try:
        with open(LOG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_log(log):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    # keep the last 60 days of entries
    cutoff = (now_utc() - dt.timedelta(days=60)).isoformat()
    log = {k: v for k, v in log.items() if v.get("at", "9") >= cutoff}
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=1, sort_keys=True)


def download(url):
    """Download a (Google Drive) file to a temp path; return the path."""
    s = requests.Session()
    r = s.get(url, stream=True, timeout=120, allow_redirects=True)
    # Large Drive files show a "can't scan for viruses" page; follow its confirm link.
    if "text/html" in r.headers.get("Content-Type", ""):
        import re
        html = r.text
        m = re.search(r'href="(/uc\?export=download[^"]+)"', html) or \
            re.search(r'action="([^"]+)"', html)
        if not m:
            raise RuntimeError("download returned an HTML page, not a file (is the file public?)")
        nxt = m.group(1).replace("&amp;", "&")
        if nxt.startswith("/"):
            nxt = "https://drive.google.com" + nxt
        params = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', html))
        r = s.get(nxt, params=params or None, stream=True, timeout=120)
    r.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    if os.path.getsize(path) < 10_000:
        raise RuntimeError("downloaded file is suspiciously small")
    return path


# ---------------- Facebook Page (video post) ----------------
def post_facebook(item, video_path):
    page, token = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")
    with open(video_path, "rb") as f:
        r = requests.post(
            f"https://graph-video.facebook.com/{GRAPH}/{page}/videos",
            data={"description": item["caption"], "title": item.get("title", "")[:255],
                  "access_token": token},
            files={"source": ("video.mp4", f, "video/mp4")}, timeout=600)
    j = r.json()
    if "id" not in j:
        raise RuntimeError(f"Facebook: {j}")
    return j["id"]


# ---------------- Instagram (Reel, resumable upload) ----------------
def post_instagram(item, video_path):
    ig, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    base = f"https://graph.facebook.com/{GRAPH}"
    r = requests.post(f"{base}/{ig}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": item["caption"][:2200],
        "share_to_feed": "true", "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"Instagram container: {r}")
    cid = r["id"]
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        up = requests.post(f"https://rupload.facebook.com/ig-api-upload/{GRAPH}/{cid}",
                           headers={"Authorization": f"OAuth {token}", "offset": "0",
                                    "file_size": str(size)}, data=f, timeout=600).json()
    if not up.get("success"):
        raise RuntimeError(f"Instagram upload: {up}")
    for _ in range(40):  # wait up to ~10 min for processing
        st = requests.get(f"{base}/{cid}", params={"fields": "status_code,status",
                                                    "access_token": token}, timeout=30).json()
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing: {st}")
        time.sleep(15)
    else:
        raise RuntimeError("Instagram processing timed out")
    pub = requests.post(f"{base}/{ig}/media_publish",
                        data={"creation_id": cid, "access_token": token}, timeout=60).json()
    if "id" not in pub:
        raise RuntimeError(f"Instagram publish: {pub}")
    return pub["id"]


# ---------------- YouTube Shorts ----------------
def post_youtube(item, video_path):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN"),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=env("YT_CLIENT_ID"), client_secret=env("YT_CLIENT_SECRET"),
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    title = (item.get("title") or "Dvia Concepts")[:90] + " #Shorts"
    body = {
        "snippet": {"title": title[:100], "description": item["caption"][:4900],
                    "categoryId": "28",  # Science & Technology
                    "tags": ["Dvia Concepts", "Kampala", "Uganda", "CCTV", "IT"]},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, mimetype="video/mp4",
                                                        resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


PLATFORMS = {
    "facebook": (post_facebook, ["FB_PAGE_ID", "FB_PAGE_TOKEN"]),
    "instagram": (post_instagram, ["IG_USER_ID", "FB_PAGE_TOKEN"]),
    "youtube": (post_youtube, ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]),
}


DRIVE = "https://www.googleapis.com/drive/v3/files"
EAT = dt.timezone(dt.timedelta(hours=3))


def drive_auth():
    """Prefer the Google login (refresh token with drive.readonly); fall back to an API key."""
    if all(env(s) for s in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
            "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60).json()
        if "drive" in r.get("scope", "") and r.get("access_token"):
            return {"headers": {"Authorization": "Bearer " + r["access_token"]}, "params": {}}
    if env("DRIVE_API_KEY"):
        return {"headers": {}, "params": {"key": env("DRIVE_API_KEY")}}
    sys.exit("No Drive access: add drive.readonly to the Google login (YT_REFRESH_TOKEN) or set DRIVE_API_KEY")


def drive_find(auth, parent, name):
    r = requests.get(DRIVE, headers=auth["headers"], params={
        "q": f"'{parent}' in parents and name = '{name}' and trashed = false",
        "fields": "files(id,name)", **auth["params"],
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}, timeout=60)
    if r.status_code != 200:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", "")
            reason = ",".join(e.get("reason", "") for e in err.get("errors", []))
        except ValueError:
            msg, reason = r.text[:300], ""
        sys.exit(f"Google Drive API error {r.status_code} ({reason}): {msg}")
    files = r.json().get("files", [])
    return files[0]["id"] if files else None


def load_queue():
    """Posts from queue.json in today's and yesterday's dated Drive folders
    (Dvia Daily Ads/<YYYY-MM-DD>/queue.json). Falls back to a single QUEUE_URL."""
    folder = env("DRIVE_FOLDER_ID")
    if folder:
        auth = drive_auth()
        posts = []
        today = now_utc().astimezone(EAT).date()
        for d in (today - dt.timedelta(days=1), today):
            day_id = drive_find(auth, folder, d.isoformat())
            if not day_id:
                continue
            qid = drive_find(auth, day_id, "queue.json")
            if not qid:
                continue
            r = requests.get(f"{DRIVE}/{qid}", headers=auth["headers"],
                             params={"alt": "media", **auth["params"]}, timeout=60)
            r.raise_for_status()
            posts += r.json().get("posts", [])
            print(f"queue {d}: {len(r.json().get('posts', []))} post(s)")
        return posts
    qurl = env("QUEUE_URL")
    if not qurl:
        sys.exit("Set DRIVE_API_KEY + DRIVE_FOLDER_ID (or QUEUE_URL) secrets")
    r = requests.get(qurl, timeout=60)
    r.raise_for_status()
    return r.json().get("posts", [])


def main():
    posts = load_queue()
    log = load_log()
    now = now_utc()
    failures = 0
    for item in posts:
        when = dt.datetime.fromisoformat(item["time"])
        if when > now or now - when > dt.timedelta(hours=LATE_LIMIT_HOURS):
            continue
        todo = []
        for p in item.get("platforms", []):
            key = f"{item['id']}:{p}"
            if p not in PLATFORMS:
                continue
            if log.get(key, {}).get("ok") or log.get(key, {}).get("tries", 0) >= 3:
                continue
            if not all(env(s) for s in PLATFORMS[p][1]):
                log[key] = {"ok": False, "at": now.isoformat(), "tries": 3,
                            "error": "skipped: secrets not set"}
                continue
            todo.append((p, key))
        if not todo:
            continue
        try:
            path = download(item["video_url"])
        except Exception as e:  # noqa: BLE001
            for p, key in todo:
                prev = log.get(key, {})
                log[key] = {"ok": False, "at": now.isoformat(),
                            "tries": prev.get("tries", 0) + 1, "error": f"download: {e}"}
            failures += 1
            continue
        for p, key in todo:
            prev = log.get(key, {})
            try:
                if DRY_RUN:
                    rid = "dry-run"
                else:
                    rid = PLATFORMS[p][0](item, path)
                log[key] = {"ok": True, "at": now.isoformat(), "id": rid,
                            "title": item.get("title", "")}
                print(f"posted {key} -> {rid}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                log[key] = {"ok": False, "at": now.isoformat(),
                            "tries": prev.get("tries", 0) + 1, "error": str(e)[:500]}
                print(f"FAILED {key}: {e}")
            save_log(log)
        os.remove(path)
    save_log(log)
    print(f"done; {failures} failure(s)")


if __name__ == "__main__":
    main()
