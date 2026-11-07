#!/usr/bin/env python3
"""
琥珀漫談 社群發文 social_post.py（2026-11-07）

FB 粉專、Instagram、Threads 用官方 API 發「圖卡＋文案」。FB 可改用 Meta 平台排程（schedule_fb，Mac 不必開著）；IG、Threads 的官方 API 沒有排程，只能到時間由程式發布（2026-11-08 查官方文件）。FB 與 IG 共用一組「Facebook 登入」權杖（IG 已連結粉專）。只發待發清單裡 approved=true 的貼文。
機密：meta_secrets.json（使用者填）、meta_tokens.json（程式自動產生的長效權杖）。兩個檔都不印、不上傳；
程式只印「有／沒有」與帳號名稱，不印權杖本身。

用法（在 hupotalks/ 最上層執行，只用 Python 標準函式庫）：
  python3 social_post.py whoami                 # 檢查三個平台的權杖、帳號名稱（FB 第一次會換成長效粉專權杖）
  python3 social_post.py preview e01_p1         # 印出三個平台實際會發的文字與圖卡網址
  python3 social_post.py test e01_p1 ig         # 只建立貼文容器、不發布（驗證權杖、圖卡網址、文字）；fb 會建未發布相片再刪掉
  python3 social_post.py post e01_p1 ig         # 立刻發布到指定平台（fb／ig／threads）；要 approved=true
  python3 social_post.py due                    # 發布所有 approved=true、時間已到、還沒發過的（排程任務用）
  python3 social_post.py status                 # 列出清單與各平台發布狀態
  python3 social_post.py schedule_fb            # FB 粉專改用 Meta 平台排程：把 approved、10 分鐘～30 天內的貼文一次排好（記在 state，due 不會再發 FB）
  python3 social_post.py fb_scheduled           # 讀回粉專上已排定的貼文（時間、開頭文字）

待發清單 social_queue.json：
  {"image_base": "https://raw.githubusercontent.com/Chieh4516/hupotalks-social/main/cards/",
   "posts": [{"id": "e01_p1", "caption": "episodes/…/社群預告/e01_p1_文案.txt", "at": "2026-11-10T21:00+08:00",
              "platforms": ["fb","ig","threads"], "approved": false}]}
文案檔格式：本文，接著【FB／Threads 結尾】【IG 結尾】【IG／FB 標籤】【Threads 主題】幾段；以「（」開頭的行是給人看的備註，不發出去。
發布紀錄：social_post_state.json（每則每個平台的貼文 ID 與時間；發過不重發）、social_post_log.jsonl。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # Mac：hupotalks/；GitHub Actions：repo 根目錄
SECRETS = ROOT / "meta_secrets.json"
TOKENS = ROOT / "meta_tokens.json"
QUEUE = ROOT / "social_queue.json"
STATE = ROOT / "social_post_state.json"
LOG = ROOT / "social_post_log.jsonl"
FB = "https://graph.facebook.com/v23.0"
TH = "https://graph.threads.net/v1.0"
PAGE_NAME = "琥珀漫談"
PLATFORMS = ("fb", "ig", "threads")


# ---------- 小工具 ----------
def load(p, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def log(**kw):
    kw["time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


class ApiError(Exception):
    pass


def call(method, url, params=None):
    params = {k: v for k, v in (params or {}).items() if v is not None}
    data = None
    if method == "GET":
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body).get("error", {})
            msg = f"{err.get('type', '')} {err.get('code', '')}: {err.get('message', body)}"
        except Exception:
            msg = body[:300]
        raise ApiError(f"HTTP {e.code} {msg}") from None


def secrets():
    s = load(SECRETS)
    if not s:
        sys.exit("🔴 找不到 meta_secrets.json")
    return s


# ---------- 權杖 ----------
def fb_page_token():
    """粉專權杖：第一次用使用者短效權杖換長效，再取粉專權杖（長效使用者權杖換來的粉專權杖不會過期）。"""
    if os.environ.get("META_PAGE_TOKEN"):          # GitHub Actions：從 Secrets 來
        return os.environ["META_PAGE_ID"], os.environ["META_PAGE_TOKEN"]
    tok = load(TOKENS, {})
    if tok.get("page_token"):
        return tok["page_id"], tok["page_token"]
    s = secrets()
    if not (s.get("page_user_token") and s.get("app_id") and s.get("app_secret")):
        raise ApiError("meta_secrets.json 缺 page_user_token／app_id／app_secret")
    long_user = call("GET", f"{FB}/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": s["app_id"],
        "client_secret": s["app_secret"], "fb_exchange_token": s["page_user_token"]})["access_token"]
    pages = call("GET", f"{FB}/me/accounts", {"access_token": long_user, "fields": "id,name,access_token"})["data"]
    page = next((p for p in pages if p["name"] == PAGE_NAME), None)
    if not page:
        raise ApiError(f"這個使用者權杖看不到「{PAGE_NAME}」粉專（看得到：{[p['name'] for p in pages]}）")
    tok.update(page_id=page["id"], page_token=page["access_token"],
               page_token_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save(TOKENS, tok)
    return page["id"], page["access_token"]


def _long_lived(kind, key, refresh_url, grant):
    """IG、Threads 的長效權杖 60 天；超過 7 天就續期（續期後再 60 天）。第一次從 meta_secrets.json 讀。"""
    tok = load(TOKENS, {})
    s = secrets()
    t = tok.get(key) or s.get(key)
    if not t:
        raise ApiError(f"meta_secrets.json 缺 {key}")
    if key not in tok and kind == "threads" and s.get("app_secret"):
        # Threads 從後台產生的可能是短效權杖：先試著換成長效（已是長效會失敗，就照用）
        try:
            t = call("GET", "https://graph.threads.net/access_token", {
                "grant_type": "th_exchange_token", "client_secret": s["app_secret"], "access_token": t})["access_token"]
        except ApiError:
            pass
    at = tok.get(key + "_at")
    age = (time.time() - datetime.fromisoformat(at).timestamp()) / 86400 if at else 999
    if age > 7:
        try:
            t = call("GET", refresh_url, {"grant_type": grant, "access_token": t})["access_token"]
            tok[key + "_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except ApiError as e:
            if not at:   # 剛產生不到 24 小時不能續期，先記下時間照用
                tok[key + "_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            else:
                print(f"⚠️ {kind} 權杖續期失敗（{e}），先用舊的")
    tok[key] = t
    save(TOKENS, tok)
    return t


def ig_account():
    """IG 走「Facebook 登入」：用粉專權杖，查粉專連結的 IG 專業帳號 ID（2026-11-07 改；一組權杖發粉專和 IG）。"""
    if os.environ.get("META_IG_ID"):
        return os.environ["META_IG_ID"], fb_page_token()[1]
    tok = load(TOKENS, {})
    pid, ptok = fb_page_token()
    if not tok.get("ig_id"):
        r = call("GET", f"{FB}/{pid}", {"fields": "instagram_business_account", "access_token": ptok})
        if "instagram_business_account" not in r:
            raise ApiError("粉專沒有連結 IG 專業帳號，或權杖缺 instagram_basic 權限")
        tok = load(TOKENS, {})
        tok["ig_id"] = r["instagram_business_account"]["id"]
        save(TOKENS, tok)
    return tok["ig_id"], ptok


def th_token():
    if os.environ.get("THREADS_TOKEN"):            # GitHub Actions：不續期（無法寫回 Secrets），60 天內要更新
        return os.environ["THREADS_TOKEN"]
    return _long_lived("threads", "threads_token", "https://graph.threads.net/refresh_access_token", "th_refresh_token")


# ---------- 文案 ----------
SECTIONS = {"【FB／Threads 結尾】": "link", "【IG 結尾】": "igtail", "【IG／FB 標籤】": "tags", "【Threads 主題】": "topic"}


def parse_caption(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    parts, cur = {"body": []}, "body"
    for line in text.splitlines():
        head = next((k for k in SECTIONS if line.startswith(k)), None)
        if head:
            cur = SECTIONS[head]
            parts[cur] = []
            continue
        if cur != "body" and (line.startswith("（") or not line.strip()):
            continue
        parts[cur].append(line)
    body = "\n".join(parts["body"]).strip()
    one = lambda k: "\n".join(parts.get(k, [])).strip()
    return {"body": body, "link": one("link"), "igtail": one("igtail"), "tags": one("tags"),
            "topic": one("topic").lstrip("#")}


def compose(post):
    c = parse_caption(post["caption"])
    fb = "\n\n".join(x for x in (c["body"], c["link"], c["tags"]) if x)
    ig = "\n\n".join(x for x in (c["body"], c["igtail"], c["tags"]) if x)
    th = "\n\n".join(x for x in (c["body"], c["link"]) if x)
    if len(th) > 500:
        raise ApiError(f"{post['id']} Threads 文字 {len(th)} 字，超過 500")
    if len(ig) > 2200:
        raise ApiError(f"{post['id']} IG 文字 {len(ig)} 字，超過 2200")
    return {"fb": fb, "ig": ig, "threads": th, "topic": c["topic"]}


def image_url(q, post):
    return q["image_base"] + post.get("image", post["id"] + ".jpg")


# ---------- 發布 ----------
def wait_ready(base, cid, token, field):
    for _ in range(20):
        st = call("GET", f"{base}/{cid}", {"fields": field, "access_token": token}).get(field, "")
        if st in ("FINISHED", ""):
            return
        if st in ("ERROR", "EXPIRED"):
            raise ApiError(f"容器狀態 {st}")
        time.sleep(3)
    raise ApiError("容器 60 秒內沒有準備好")


def publish(platform, q, post, really=True):
    txt, img = compose(post), image_url(q, post)
    if platform == "fb":
        pid, ptok = fb_page_token()
        r = call("POST", f"{FB}/{pid}/photos", {"url": img, "message": txt["fb"],
                                                "published": "true" if really else "false", "access_token": ptok})
        if not really:
            call("DELETE", f"{FB}/{r['id']}", {"access_token": ptok})
            return "（測試：未發布相片已建立並刪除）"
        return r.get("post_id") or r["id"]
    if platform == "ig":
        igid, t = ig_account()
        c = call("POST", f"{FB}/{igid}/media", {"image_url": img, "caption": txt["ig"], "access_token": t})["id"]
        wait_ready(FB, c, t, "status_code")
        if not really:
            return f"（測試：容器 {c} 已建立、未發布）"
        return call("POST", f"{FB}/{igid}/media_publish", {"creation_id": c, "access_token": t})["id"]
    if platform == "threads":
        t = th_token()
        c = call("POST", f"{TH}/me/threads", {"media_type": "IMAGE", "image_url": img, "text": txt["threads"],
                                              "topic_tag": txt["topic"] or None, "access_token": t})["id"]
        wait_ready(TH, c, t, "status")
        if not really:
            return f"（測試：容器 {c} 已建立、未發布）"
        time.sleep(5)
        return call("POST", f"{TH}/me/threads_publish", {"creation_id": c, "access_token": t})["id"]
    raise SystemExit(f"🔴 不認得的平台 {platform}")


def schedule_fb(q, post, state):
    """FB 平台排程（2026-11-08）：先上傳暫存相片（published=false、temporary=true），再用 /feed 附上相片、設定 scheduled_publish_time。"""
    done = state.setdefault(post["id"], {})
    if "fb" in done:
        print(f"⏭ {post['id']} fb 已發過或已排程（{done['fb']['id']}）")
        return True
    ts = int(datetime.fromisoformat(post["at"]).timestamp())
    now = time.time()
    if not (now + 11 * 60 <= ts <= now + 29.5 * 86400):
        print(f"⏭ {post['id']} 時間不在 10 分鐘～30 天內，不能用 FB 平台排程")
        return False
    pid, ptok = fb_page_token()
    txt, img = compose(post), image_url(q, post)
    try:
        photo = call("POST", f"{FB}/{pid}/photos", {"url": img, "published": "false", "temporary": "true",
                                                    "access_token": ptok})["id"]
        r = call("POST", f"{FB}/{pid}/feed", {"message": txt["fb"], "attached_media": json.dumps([{"media_fbid": photo}]),
                                              "published": "false", "scheduled_publish_time": str(ts),
                                              "access_token": ptok})
    except ApiError as e:
        print(f"🔴 {post['id']} fb 排程失敗：{e}")
        log(id=post["id"], platform="fb", ok=False, error="schedule: " + str(e))
        return False
    done["fb"] = {"id": r["id"], "scheduled_for": post["at"], "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save(STATE, state)
    log(id=post["id"], platform="fb", ok=True, post_id=r["id"], scheduled_for=post["at"])
    print(f"✅ {post['id']} fb 已排程 {post['at']}：{r['id']}")
    return True


def find(q, pid):
    p = next((p for p in q["posts"] if p["id"] == pid), None)
    if not p:
        sys.exit(f"🔴 清單裡沒有 {pid}")
    return p


def do_post(q, post, platform, state):
    done = state.setdefault(post["id"], {})
    if platform in done:
        print(f"⏭ {post['id']} {platform} 已發過（{done[platform]['id']}）")
        return True
    try:
        rid = publish(platform, q, post)
    except ApiError as e:
        print(f"🔴 {post['id']} {platform} 失敗：{e}")
        log(id=post["id"], platform=platform, ok=False, error=str(e))
        return False
    done[platform] = {"id": rid, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save(STATE, state)
    log(id=post["id"], platform=platform, ok=True, post_id=rid)
    print(f"✅ {post['id']} {platform} 已發布：{rid}")
    return True


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    cmd = a[0]
    q = load(QUEUE)
    state = load(STATE, {})
    if cmd == "whoami":
        checks = (("fb", lambda: call("GET", f"{FB}/{fb_page_token()[0]}", {"fields": "name",
                                                                             "access_token": fb_page_token()[1]})["name"]),
                  ("ig", lambda: call("GET", f"{FB}/{ig_account()[0]}", {"fields": "username", "access_token": ig_account()[1]})["username"]),
                  ("threads", lambda: call("GET", f"{TH}/me", {"fields": "username", "access_token": th_token()})["username"]))
        for name, fn in checks:
            try:
                print(f"✅ {name}：{fn()}")
            except ApiError as e:
                print(f"⚪ {name}：{e}")
    elif cmd == "preview":
        p = find(q, a[1])
        t = compose(p)
        print(f"圖卡：{image_url(q, p)}\n時間：{p['at']}　平台：{p['platforms']}　核可：{p['approved']}")
        for k in PLATFORMS:
            print(f"\n===== {k}（{len(t[k])} 字）=====\n{t[k]}")
        print(f"\n===== Threads 主題：{t['topic']}")
    elif cmd == "test":
        p = find(q, a[1])
        try:
            print(f"✅ {a[2]}：{publish(a[2], q, p, really=False)}")
        except ApiError as e:
            print(f"🔴 {a[2]}：{e}")
    elif cmd == "post":
        p = find(q, a[1])
        if not p.get("approved"):
            sys.exit(f"🔴 {p['id']} 還沒核可（social_queue.json 的 approved 要是 true）")
        ok = do_post(q, p, a[2], state)
        sys.exit(0 if ok else 1)
    elif cmd == "due":
        now = datetime.now(timezone.utc)
        bad = 0
        for p in q["posts"]:
            if not p.get("approved") or datetime.fromisoformat(p["at"]) > now:
                continue
            for pf in p["platforms"]:
                bad += not do_post(q, p, pf, state)
        print("完成" if not bad else f"🔴 {bad} 個失敗，見 social_post_log.jsonl")
        sys.exit(1 if bad else 0)
    elif cmd == "schedule_fb":
        bad = 0
        for p in q["posts"]:
            if p.get("approved") and "fb" in p["platforms"]:
                bad += not schedule_fb(q, p, state)
        sys.exit(1 if bad else 0)
    elif cmd == "fb_scheduled":
        pid, ptok = fb_page_token()
        r = call("GET", f"{FB}/{pid}/scheduled_posts", {"fields": "id,message,scheduled_publish_time,full_picture",
                                                        "limit": 50, "access_token": ptok})
        for x in r.get("data", []):
            t = x.get("scheduled_publish_time")
            when = datetime.fromtimestamp(int(t), timezone.utc).astimezone().isoformat(timespec="minutes") if str(t).isdigit() else t
            print(f"{x['id']}  {when}  圖{'✅' if x.get('full_picture') else '—'}  {(x.get('message') or '')[:24]}")
        print(f"共 {len(r.get('data', []))} 則")
    elif cmd == "status":
        for p in q["posts"]:
            done = state.get(p["id"], {})
            marks = "　".join(f"{pf}{'✅' if pf in done else '·'}" for pf in p["platforms"])
            print(f"{p['id']:8} {p['at']}  核可{'✅' if p.get('approved') else '—'}  {marks}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
