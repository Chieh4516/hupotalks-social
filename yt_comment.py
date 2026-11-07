#!/usr/bin/env python3
"""
琥珀漫談 YouTube 來源留言 yt_comment.py（2026-11-08）

在 GitHub Actions 上跑：排程公開的影片變成公開後，發「來源留言」（私人影片不能留言，2026-11-01 實測 403），
再開一則 GitHub Issue 列出要在 Studio 手動做的事（API 不能置頂留言）。只用 Python 標準函式庫。

授權：環境變數 YT_TOKEN_JSON（Mac 的 yt_token.json 整份內容，放在 GitHub Secret；程式不印出）。
用法：
  python yt_comment.py check     # 只讀：換權杖、列出每集的公開狀態與是否已留言
  python yt_comment.py due       # 到時間、已公開、還沒留言的就發；發完開 Issue（排程用）
清單 yt_comment_queue.json；紀錄 yt_comment_state.json（每集的留言 ID；發過不重發）。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "yt_comment_queue.json"
STATE = ROOT / "yt_comment_state.json"
API = "https://www.googleapis.com/youtube/v3"
LATE = timedelta(hours=3)        # 公開時間過了 3 小時還不是公開 → 當成失敗（寄信通知）
MAX_LEN = 10000                  # 留言上限（YouTube 說明約 1 萬字；超過不自動刪改）


class ApiError(Exception):
    pass


def http(method, url, params=None, body=None, headers=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    h = dict(headers or {})
    if body is not None:
        if isinstance(body, dict) and h.get("Content-Type") == "application/json":
            data = json.dumps(body).encode()
        else:
            data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            err = json.loads(txt).get("error", {})
            msg = err.get("message", err) if isinstance(err, dict) else f"{err} {json.loads(txt).get('error_description', '')}"
        except Exception:
            msg = txt[:300]
        raise ApiError(f"HTTP {e.code}: {msg}") from None


def access_token():
    raw = os.environ.get("YT_TOKEN_JSON")
    if not raw:
        sys.exit("🔴 沒有 YT_TOKEN_JSON（GitHub Secret）")
    t = json.loads(raw)
    try:
        r = http("POST", t.get("token_uri", "https://oauth2.googleapis.com/token"), body={
            "client_id": t["client_id"], "client_secret": t["client_secret"],
            "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    except ApiError as e:
        sys.exit(f"🔴 YouTube 授權更新失敗（{e}）。在 Mac 重跑 yt_upload.py auth-url／auth-finish，再把新的 yt_token.json 貼回 Secret YT_TOKEN_JSON")
    return r["access_token"]


def yt(method, path, tok, params=None, body=None):
    h = {"Authorization": f"Bearer {tok}"}
    if body is not None:
        h["Content-Type"] = "application/json"
    return http(method, f"{API}/{path}", params, body, h)


def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def video(tok, vid):
    r = yt("GET", "videos", tok, {"part": "status,snippet", "id": vid})
    if not r.get("items"):
        raise ApiError(f"找不到影片 {vid}（或授權的不是琥珀漫談頻道）")
    return r["items"][0]


def existing_comment(tok, vid, channel, text):
    """已經有頻道自己發的、開頭相同的留言就回傳它的 ID（避免 Mac 或手動已經發過又重發）。"""
    head = text.strip().splitlines()[0][:40]
    page = None
    for _ in range(5):
        p = {"part": "snippet", "videoId": vid, "maxResults": 100, "textFormat": "plainText"}
        if page:
            p["pageToken"] = page
        r = yt("GET", "commentThreads", tok, p)
        for it in r.get("items", []):
            s = it["snippet"]["topLevelComment"]["snippet"]
            if s.get("authorChannelId", {}).get("value") == channel and s.get("textOriginal", s.get("textDisplay", "")).startswith(head):
                return it["id"]
        page = r.get("nextPageToken")
        if not page:
            return None
    return None


def open_issue(ep, cid):
    repo, gh = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and gh):
        print("（不在 GitHub Actions，跳過開 Issue）")
        return
    vid = ep["video_id"]
    todo = "\n".join(f"- [ ] {x}" for x in ep["studio_todo"])
    body = (f"第 {ep['no']} 集「{ep['title']}」已公開，來源留言已發出（`{cid}`）。\n\n"
            f"影片：https://youtu.be/{vid}\nStudio：https://studio.youtube.com/video/{vid}/edit\n\n"
            f"**請到 YouTube Studio（手機 App 也可以）：**\n{todo}\n\n做完把這則 Issue 關掉。")
    try:
        r = http("POST", f"https://api.github.com/repos/{repo}/issues",
                 body={"title": f"第 {ep['no']} 集已公開：請到 Studio 置頂來源留言", "body": body},
                 headers={"Authorization": f"Bearer {gh}", "Accept": "application/vnd.github+json",
                          "Content-Type": "application/json"})
        print(f"📋 已開 Issue：{r.get('html_url')}")
    except ApiError as e:
        print(f"⚠️ 開 Issue 失敗（{e}）；留言已發出，請自己到 Studio 置頂")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("check", "due"):
        sys.exit(__doc__)
    q, state = load(QUEUE, {"episodes": []}), load(STATE, {})
    now = datetime.now(timezone.utc)
    if cmd == "due" and not any(
            not state.get(ep["id"], {}).get("comment_id")
            and now >= datetime.fromisoformat(ep["publish_at"].replace("Z", "+00:00")) for ep in q["episodes"]):
        print("沒有到時間要留言的影片")
        return
    tok = access_token()
    bad = 0
    for ep in q["episodes"]:
        key, vid = ep["id"], ep["video_id"]
        done = state.get(key, {})
        at = datetime.fromisoformat(ep["publish_at"].replace("Z", "+00:00"))
        text = (ROOT / ep["comment_file"]).read_text(encoding="utf-8").strip()
        try:
            v = video(tok, vid)
        except ApiError as e:
            print(f"🔴 {key} {e}")
            bad += 1
            continue
        priv = v["status"]["privacyStatus"]
        if cmd == "check":
            print(f"{key} {vid} {priv:8} 公開時間 {at.astimezone().strftime('%m/%d %H:%M')}  留言 {len(text)} 字  "
                  f"{'已留言 ' + done['comment_id'] if done.get('comment_id') else '未留言'}")
            continue
        if done.get("comment_id") or now < at:
            continue
        if priv != "public":
            if now > at + LATE:
                print(f"🔴 {key} 公開時間已過 3 小時仍是 {priv}，請到 Studio 看看")
                bad += 1
            else:
                print(f"⏳ {key} 還是 {priv}，下一輪再看")
            continue
        if len(text) > MAX_LEN:
            print(f"🔴 {key} 留言 {len(text)} 字超過上限，不自動刪改")
            bad += 1
            continue
        try:
            cid = existing_comment(tok, vid, v["snippet"]["channelId"], text)
            if cid:
                print(f"⏭ {key} 頻道已經有這則留言（{cid}），不重發")
            else:
                r = yt("POST", "commentThreads", tok, {"part": "snippet"},
                       {"snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": text}}}})
                cid = r["id"]
                print(f"✅ {key} 來源留言已發出（{len(text)} 字，{cid}）")
        except ApiError as e:
            print(f"🔴 {key} 留言失敗：{e}")
            bad += 1
            continue
        state[key] = {"comment_id": cid, "at": now.isoformat(timespec="seconds")}
        save(STATE, state)
        open_issue(ep, cid)
    if cmd == "due":
        print("完成" if not bad else f"🔴 {bad} 個問題")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
