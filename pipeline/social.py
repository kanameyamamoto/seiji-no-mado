"""このサイトの SNS（Bluesky・X）に、新しく公開した記事を、決まった形で機械が投稿する（ルール D-17）。

人は選ばない：
- 投稿するのは「国会で決まったこと」の記事のうち、成立（承認・否決）した日から RECENT_DAYS 日以内のもの、すべて。
  さかのぼって足した古い記事は投稿しない（一度に大量になるため）。
- 並びは、成立した日の古い順 → 記事IDの順。1回に出すのは、1つのSNSにつき MAX_PER_RUN 件まで（残りは次の回）。
- 文は、記事のページにある文だけを使う（種類・題名・日付・要約〈ふつう〉・リンク）。新しい文は書かない。AIも使わない。
- 返信・引用・いいね・フォロー・リポストはしない。
- --facts のときは、記事のかわりに「データの数字のポスト」（pipeline/social_facts.py。決まった型にデータの数字を入れた文とグラフ）を、
  1つのSNSにつき1本出す。毎日 12:03 と 21:03（日本時間）に動く。

鍵（GitHub の Secrets）がないSNSには、何もしない：
  Bluesky … BLUESKY_HANDLE, BLUESKY_APP_PASSWORD（アプリパスワード）
  X       … X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET（OAuth 1.0a・ユーザーのトークン）
記録：work/social.json（どの記事を、いつ、どのSNSに出したか）
使い方：python pipeline/social.py [--facts] [--dry-run]
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(ROOT, "work", "social.json")
RECENT_DAYS = 30
MAX_PER_RUN = 10
UA = "seiji-no-mado-bot/1.0 (+https://github.com/kanameyamamoto/seiji-no-mado)"
JST = datetime.timezone(datetime.timedelta(hours=9))


def today():
    return datetime.datetime.now(JST).date()


def jp_date(s):
    y, m, d = s.split("-")
    return f"{int(y)}年{int(m)}月{int(d)}日"


# ---------- 投稿する記事と、文 ----------
def candidates(feed, site):
    since = (today() - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    items = [it for it in feed.get("items", []) if it.get("date", "") >= since]
    items.sort(key=lambda it: (it["date"], it["id"]))
    return [{"id": it["id"], "url": f"{site}a/{it['id']}/", "head": f"【{it['kind']}】{it['title']}（{jp_date(it['date'])}）",
             "lead": (it.get("lead") or {}).get("std", ""), "title": it["title"]} for it in items]


def x_weight(s):
    """X の文字数の数え方（だいたい）：日本語などは2、英数字は1。"""
    return sum(1 if ord(c) < 0x1100 else 2 for c in s)


def compose(c, limit, weight, url_cost):
    """種類・題名・日付 → 要約 → リンク。上限を超えるときは要約の終わりを「…」で切る。
    データの数字のポスト（c["fact"]）は、1文目 → 数え方 → リンク。入らないときは数え方を外す（画像には入っている）。"""
    if c.get("fact"):
        t = c["text"] + "\n" + c["how"]
        if weight(t) + 1 + url_cost > limit:
            t = c["text"]
        return t + "\n" + c["url"]
    head, lead = c["head"], c["lead"]
    budget = limit - url_cost - weight(head) - 2  # 改行2つ
    if lead and weight(lead) + 1 > budget:
        while lead and weight(lead) + 2 > budget:
            lead = lead[:-1]
        lead = lead + "…" if lead else ""
    return head + ("\n" + lead if lead else "") + "\n" + c["url"]


# ---------- Bluesky ----------
def http_json(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


class Bluesky:
    name = "bluesky"

    def __init__(self, handle, password):
        s = http_json("https://bsky.social/xrpc/com.atproto.server.createSession", {"identifier": handle, "password": password})
        self.did, self.jwt = s["did"], s["accessJwt"]

    @staticmethod
    def text(c):
        return compose(c, 300, len, len(c["url"]))

    def post(self, c):
        text = self.text(c)
        b = text.encode("utf-8")
        u = c["url"].encode("utf-8")
        start = b.rfind(u)
        rec = {"$type": "app.bsky.feed.post", "text": text, "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
               "langs": ["ja"],
               "facets": [{"index": {"byteStart": start, "byteEnd": start + len(u)}, "features": [{"$type": "app.bsky.richtext.facet#link", "uri": c["url"]}]}],
               "embed": {"$type": "app.bsky.embed.external", "external": {"uri": c["url"], "title": c["title"] + "｜せいじのまど", "description": c.get("lead", "")}}}
        if c.get("image"):
            try:
                rec["embed"] = {"$type": "app.bsky.embed.images", "images": [{"alt": c["alt"], "image": self.upload(c["image"]),
                                                                               "aspectRatio": {"width": 1200, "height": 675}}]}
            except Exception as e:  # noqa: BLE001
                print(f"[social] bluesky に画像を上げられなかったので、画像なしで出します: {e}")
        r = http_json("https://bsky.social/xrpc/com.atproto.repo.createRecord", {"repo": self.did, "collection": "app.bsky.feed.post", "record": rec},
                      {"Authorization": f"Bearer {self.jwt}"})
        rkey = r["uri"].rsplit("/", 1)[1]
        return f"https://bsky.app/profile/{self.did}/post/{rkey}"

    def upload(self, path):
        req = urllib.request.Request("https://bsky.social/xrpc/com.atproto.repo.uploadBlob", data=open(path, "rb").read(),
                                     headers={"Content-Type": "image/png", "Authorization": f"Bearer {self.jwt}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["blob"]


# ---------- X ----------
def pct(s):
    return urllib.parse.quote(str(s), safe="~-._")


class X:
    name = "x"
    URL = "https://api.x.com/2/tweets"

    def __init__(self, key, secret, token, token_secret):
        self.k, self.s, self.t, self.ts = key, secret, token, token_secret

    @staticmethod
    def text(c):
        return compose(c, 280, x_weight, 23)  # リンクは長さにかかわらず23文字として数えられる

    def auth(self, method, url):
        o = {"oauth_consumer_key": self.k, "oauth_nonce": secrets.token_hex(16), "oauth_signature_method": "HMAC-SHA1",
             "oauth_timestamp": str(int(time.time())), "oauth_token": self.t, "oauth_version": "1.0"}
        params = "&".join(f"{pct(k)}={pct(v)}" for k, v in sorted(o.items()))
        base = "&".join([method, pct(url), pct(params)])
        key = f"{pct(self.s)}&{pct(self.ts)}".encode()
        o["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
        return "OAuth " + ", ".join(f'{pct(k)}="{pct(v)}"' for k, v in sorted(o.items()))

    MEDIA = "https://api.x.com/2/media/upload"

    def post(self, c):
        body = {"text": self.text(c)}
        if c.get("image"):
            try:
                body["media"] = {"media_ids": [self.upload(c["image"])]}
            except Exception as e:  # noqa: BLE001
                print(f"[social] x に画像を上げられなかったので、画像なしで出します: {e}")
        r = http_json(self.URL, body, {"Authorization": self.auth("POST", self.URL)})
        return f"https://x.com/i/web/status/{r['data']['id']}"

    def upload(self, path):
        """画像を上げて media_id を返す（multipart の中身は署名に入れない）。"""
        bnd = "----seijinomado" + secrets.token_hex(8)
        data = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"media_category\"\r\n\r\ntweet_image\r\n"
                f"--{bnd}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
        data += open(path, "rb").read() + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(self.MEDIA, data=data, headers={"Content-Type": f"multipart/form-data; boundary={bnd}", "User-Agent": UA,
                                                                    "Authorization": self.auth("POST", self.MEDIA)})
        with urllib.request.urlopen(req, timeout=120) as r:
            j = json.loads(r.read())
        return (j.get("data") or {}).get("id") or j.get("media_id_string")


# ---------- まとめ ----------
def clients():
    env = os.environ.get
    out = []
    if env("BLUESKY_HANDLE") and env("BLUESKY_APP_PASSWORD"):
        out.append(("bluesky", lambda: Bluesky(env("BLUESKY_HANDLE"), env("BLUESKY_APP_PASSWORD"))))
    if all(env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")):
        out.append(("x", lambda: X(env("X_API_KEY"), env("X_API_SECRET"), env("X_ACCESS_TOKEN"), env("X_ACCESS_SECRET"))))
    return out


def is_live(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA}), timeout=30) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def fact_post(f, site):
    import tempfile
    import social_facts as SF
    img = SF.chart_png(f, os.path.join(tempfile.mkdtemp(), "chart.png"))
    alt = f["chart"]["title"] + "。" + "、".join(f"{l}：{SF.yen(v) if f['chart'].get('money') else format(v, ',.0f') + f['chart']['unit']}"
                                               for l, v in zip(f["chart"]["labels"], f["chart"]["values"]))
    return {"fact": True, "id": f["id"], "key": f["key"], "text": f["text"], "how": f["how"], "url": site + f["path"], "title": f["title"],
            "image": img, "alt": alt[:900], "head": f["text"][:40]}


def draft_x():
    """X の鍵がない間：次に出すデータのポストの文と画像を out/social/draft-x.png に作り、持ち主が手で出す下書きにする。
    出す順番を進めるため、記録（work/social.json）に post="draft" で残す。"""
    import shutil
    import social_facts as SF
    site = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")).get("site_url", "https://seijinomado.com/")
    log = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {"posts": []}
    f = SF.pick(log["posts"], "x")
    if not f:
        print("[social] 出せるデータのポストがありません")
        return
    c = fact_post(f, site)
    out = os.path.join(ROOT, "out", "social", "draft-x.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy(c["image"], out)
    log["posts"].append({"platform": "x", "id": f["id"], "key": f["key"], "date": datetime.datetime.now(JST).isoformat(timespec="seconds"), "post": "draft"})
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=== 文 ===")
    print(X.text(c))
    print("=== 画像 ===")
    print(out)
    print("=== 画像の説明（代わりの文） ===")
    print(c["alt"])


def recent_fact(posts, platform, hours):
    """同じSNSに、決めた時間の中でデータのポストを出していれば True（二重に出さないため）。"""
    now = datetime.datetime.now(JST)
    for p in posts:
        if p.get("platform") == platform and str(p.get("id", "")).startswith("fact:") and p.get("post") != "draft":
            try:
                if now - datetime.datetime.fromisoformat(p["date"]) < datetime.timedelta(hours=hours):
                    return True
            except (KeyError, ValueError):
                pass
    return False


def main_facts(dry=False):
    import social_facts as SF
    site = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")).get("site_url", "https://seijinomado.com/")
    log = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {"posts": []}
    if dry:
        for name in ("bluesky", "x"):
            f = SF.pick(log["posts"], name)
            if f:
                c = fact_post(f, site)
                print(f"--- {name}: {f['id']}  x {x_weight(X.text(c).replace(c['url'], 'x' * 23))}\n{X.text(c)}\n画像：{c['image']}\n代わりの文：{c['alt']}")
        return
    cl = clients()
    if not cl:
        print("[social] SNS の鍵がないので、投稿しません")
        return
    changed = False
    for name, make in cl:
        if recent_fact(log["posts"], name, hours=6):
            print(f"[social] {name}：6時間以内にデータのポストを出したので、今回は出しません")
            continue
        f = SF.pick(log["posts"], name)
        if not f:
            print(f"[social] {name}：出せるデータのポストがありません")
            continue
        try:
            api = make()
            link = api.post(fact_post(f, site))
        except urllib.error.HTTPError as e:
            print(f"[social] {name} に投稿できませんでした {f['id']}: {e.code} {e.read()[:300]!r}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[social] {name} に投稿できませんでした {f['id']}: {e}")
            continue
        log["posts"].append({"platform": name, "id": f["id"], "key": f["key"], "date": datetime.datetime.now(JST).isoformat(timespec="seconds"), "post": link})
        changed = True
        print(f"[social] {name}：{f['text']}")
    if changed:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main(dry=False):
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    site = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")).get("site_url", "https://seijinomado.com/")
    log = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {"posts": []}
    done = {(p["platform"], p["id"]) for p in log["posts"]}
    cands = candidates(feed, site)
    if dry:
        for c in cands:
            print("--- bluesky", len(Bluesky.text(c)), "/ x", x_weight(X.text(c).replace(c["url"], "x" * 23)))
            print(X.text(c))
        return
    cl = clients()
    if not cl:
        print("[social] SNS の鍵がないので、投稿しません")
        return
    changed = False
    for name, make in cl:
        todo = [c for c in cands if (name, c["id"]) not in done][:MAX_PER_RUN]
        if not todo:
            continue
        try:
            api = make()
        except Exception as e:  # noqa: BLE001
            print(f"[social] {name} にログインできませんでした: {e}")
            continue
        for c in todo:
            if not is_live(c["url"]):
                print(f"[social] まだページが公開されていないので、次の回に：{c['url']}")
                continue
            try:
                link = api.post(c)
            except urllib.error.HTTPError as e:
                print(f"[social] {name} に投稿できませんでした {c['id']}: {e.code} {e.read()[:300]!r}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"[social] {name} に投稿できませんでした {c['id']}: {e}")
                break
            log["posts"].append({"platform": name, "id": c["id"], "date": datetime.datetime.now(JST).isoformat(timespec="seconds"), "post": link})
            changed = True
            print(f"[social] {name}：{c['head']}")
            time.sleep(5)
    if changed:
        log["rule"] = "ルール D-17。成立から30日以内の「国会で決まったこと」の記事を、すべて同じ形で、成立した日の古い順に投稿する。"
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if "--draft-x" in sys.argv:
        draft_x()
    elif "--facts" in sys.argv:
        main_facts("--dry-run" in sys.argv)
    else:
        main("--dry-run" in sys.argv)
