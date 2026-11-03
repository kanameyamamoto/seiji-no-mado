"""政府・国会・政党の「公式サイト・SNS」を、機械で集める（AIは使わない：ルールC-6・D-11）。

- 載せるのは、その組織の公式サイトのトップページと、その組織が自分で出している「SNS一覧」ページに
  リンクされているアカウントだけ。人は選ばない（偽物やファンのアカウントを入れないため）。
- SNS一覧ページは、トップページの「SNS」「ソーシャルメディア」などのリンクから1回だけたどる。
  見つからないときは data/accounts_src.json の sns_page を使う。
- 役所のサイトには、SNSへ直接リンクせず「外部サイトに移動します」という自分のページを1枚はさむところがある。
  SNS一覧ページの中で、同じ役所のサイトの「sns」「x」「twitter」などを含むページは、もう1回だけたどる（1組織40ページまで）。
- 並びは X・YouTube・Instagram・TikTok・Facebook・LINE・note・Threads（どの組織も同じ）。
- ページを開いたあとにプログラムでSNSのリンクを表示するサイトや、ふつうの読み方を断るサイトは、
  ブラウザ（Chromium、Playwright）で開いてから集める。
- 公式サイトを読めなかったときは、前に集めたものを残し、「確認できなかった日」を記録する。

使い方：python pipeline/accounts.py   （週に1回、出典の見張りのあとに動かす）
"""
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "accounts.json")
# 公式サイトの中には、ふつうのブラウザ以外を断るところがあるので、ブラウザと同じ名乗りにする（読むのはトップページなど数ページだけ）
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ORDER = ["X", "YouTube", "Instagram", "TikTok", "Facebook", "LINE", "note", "Threads"]
PAT = {
    "X": r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/(?!share|intent|home|hashtag|search|i/|privacy|tos)([A-Za-z0-9_]{1,30})(?=[/\"'?#\s<]|$)",
    "YouTube": r"https?://(?:www\.|m\.)?youtube\.com/(@[^\"'?#/\s<]+|channel/[A-Za-z0-9_-]+|c/[^\"'?#/\s<]+|user/[^\"'?#/\s<]+)",
    "Instagram": r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|explore/|accounts/)([A-Za-z0-9_.]+)",
    "TikTok": r"https?://(?:www\.)?tiktok\.com/(@[A-Za-z0-9_.]+)",
    "Facebook": r"https?://(?:www\.|ja-jp\.|m\.)?facebook\.com/(?!sharer|share|dialog|plugins|tr[/?]|2008/)([A-Za-z0-9_.-]+(?:/[0-9]{6,})?)",
    "LINE": r"https?://(lin\.ee/[A-Za-z0-9]+|line\.me/R/ti/p/[^\"'\s<&?]+|page\.line\.me/[A-Za-z0-9_-]+)",
    "note": r"https?://note\.com/([A-Za-z0-9_]+)",
    "Threads": r"https?://(?:www\.)?threads\.(?:net|com)/(@[A-Za-z0-9_.]+)",
}
BASE = {"X": "https://x.com/", "YouTube": "https://www.youtube.com/", "Instagram": "https://www.instagram.com/",
        "TikTok": "https://www.tiktok.com/", "Facebook": "https://www.facebook.com/", "LINE": "https://", "note": "https://note.com/",
        "Threads": "https://www.threads.com/"}
SNS_WORDS = re.compile(r"SNS|ソーシャルメディア|ソーシャル・メディア|公式アカウント|Social Media", re.I)


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        ct = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ct) or re.search(rb'charset=["\']?([\w-]+)', b[:2000])
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    for e in (enc, "utf-8", "cp932", "euc-jp"):
        try:
            return b.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


_browser = {}


def render(url):
    """ブラウザで開いて、表示されたあとのページの中身を返す。"""
    if "page" not in _browser:
        from playwright.sync_api import sync_playwright
        _browser["pw"] = sync_playwright().start()
        _browser["b"] = _browser["pw"].chromium.launch()
        _browser["page"] = _browser["b"].new_page(user_agent=UA, locale="ja-JP")
    pg = _browser["page"]
    pg.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        pg.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    return pg.content()


def close_browser():
    if "b" in _browser:
        _browser["b"].close()
        _browser["pw"].stop()
        _browser.clear()


def fetch(url):
    """ふつうに読む。断られたらブラウザで開く。"""
    try:
        return get(url), False
    except Exception:  # noqa: BLE001
        return render(url), True


def find(text):
    t = html.unescape(text)
    out = {}
    for k in ORDER:
        seen = []
        for m in re.finditer(PAT[k], t):
            v = m.group(1).rstrip("/")
            key = v.lower()
            if k == "Facebook" and (v.isdigit() or v in ("pages", "groups", "events", "profile.php", "watch")):
                continue
            if key not in [s.lower() for s in seen]:
                seen.append(v)
        if seen:
            out[k] = [{"id": v, "url": BASE[k] + v} for v in seen]
    return out


def sns_pages(top_url, text):
    """トップページの中の「SNS一覧」らしいリンク（同じサイトの中だけ）。"""
    host = urllib.parse.urlparse(top_url).netloc
    out = []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.S | re.I):
        lab = re.sub(r"<[^>]+>", "", label)
        if not (SNS_WORDS.search(lab) or re.search(r"/sns[/_.]|sns\.html|snspage|socialmedia|social-media|/social/", href, re.I)):
            continue
        u = urllib.parse.urljoin(top_url, html.unescape(href)).split("#")[0]
        if urllib.parse.urlparse(u).netloc == host and u not in out and u.rstrip("/") != top_url.rstrip("/"):
            out.append(u)
    return out[:3]


HOP = re.compile(r"sns|twitter|/x[0-9]?\.html|_x\.html|tox\.html|facebook|youtube|instagram|tiktok|line|note|threads|gaibu|olink|link", re.I)


def gov_host(u, top_url):
    """同じ役所のサイト（www8.cao.go.jp なども同じとみなす）。"""
    a, b = urllib.parse.urlparse(u).netloc, urllib.parse.urlparse(top_url).netloc
    root = ".".join(b.split(".")[-3:]) if b.endswith(".go.jp") else b
    return a == b or (root and a.endswith(root))


def hop_links(page_url, text, top_url):
    out = []
    for href in re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', text, flags=re.I):
        u = urllib.parse.urljoin(page_url, html.unescape(href)).split("#")[0]
        if gov_host(u, top_url) and HOP.search(urllib.parse.urlparse(u).path) and u != page_url and u not in out:
            out.append(u)
    return out[:40]


def merge(a, b):
    for k, v in b.items():
        cur = a.setdefault(k, [])
        for x in v:
            if x["id"].lower() not in [c["id"].lower() for c in cur]:
                cur.append(x)
    return {k: a[k] for k in ORDER if k in a}


def collect(item):
    """1つの組織の公式サイトから集める。読めなければ例外。"""
    top, rendered = fetch(item["url"])
    found = find(top)
    if not found and not rendered:  # 表示のあとにリンクが出るサイト
        try:
            top = render(item["url"])
            found = find(top)
        except Exception as e:  # noqa: BLE001
            print(f"[accounts] ブラウザで開けませんでした {item['url']}: {e}")
    pages = sns_pages(item["url"], top)
    if item.get("sns_page") and item["sns_page"] not in pages:
        pages.insert(0, item["sns_page"])
    used = [item["url"]]
    for p in pages[:3]:
        try:
            time.sleep(1)
            t, rendered = fetch(p)
            f = find(t)
            if not f and not rendered:
                t = render(p)
                f = find(t)
            found = merge(found, f)
            used.append(p)
            if item["url"].find(".go.jp") >= 0:  # 役所：はさまれたページをもう1回たどる
                for q in hop_links(p, t, item["url"]):
                    try:
                        time.sleep(0.5)
                        found = merge(found, find(get(q)))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001
            print(f"[accounts] SNS一覧ページを読めませんでした {p}: {e}")
    return found, used


def items():
    src = json.load(open(os.path.join(DATA, "accounts_src.json"), encoding="utf-8"))
    groups = [dict(g) for g in src["groups"]]
    P = json.load(open(os.path.join(DATA, "parties.json"), encoding="utf-8"))
    party = {"id": "party", "name": "政党", "items": []}
    for p in P.get("national", []):
        party["items"].append({"id": p["id"], "name": p["name"], "url": p.get("url") or "", "party": p["id"]})
    for i, p in enumerate(P.get("others", [])):
        party["items"].append({"id": f"o{i}", "name": p["name"], "url": p.get("url") or "", "party": f"o{i}"})
    return groups + [party]


def update():
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    prev = {(g["id"], x["id"]): x for g in old.get("groups", []) for x in g.get("items", [])}
    out_groups, changes = [], []
    for g in items():
        rows = []
        for it in g["items"]:
            row = {k: it[k] for k in ("id", "name", "url", "page", "party") if it.get(k)}
            p = prev.get((g["id"], it["id"]), {})
            if not it.get("url"):
                row["sns"] = {}
                row["note"] = "公式サイトの住所がまだ登録されていない"
                rows.append(row)
                continue
            try:
                sns, used = collect(it)
                # 画面には各SNSの最初の1つを出し、残りは「ほかN件」にたたむ。データは各SNS50件まで（全部の数は counts）
                row["counts"] = {k: len(v) for k, v in sns.items()}
                sns = {k: v[:50] for k, v in sns.items()}
                row["sns"], row["sources"], row["checked"] = sns, used, today()
                if p.get("sns") != sns:
                    changes.append(f"{g['name']}・{it['name']}：" + "、".join(f"{k} {len(v)}" for k, v in sns.items()) or "なし")
            except Exception as e:  # noqa: BLE001
                print(f"[accounts] 公式サイトを読めませんでした {it['name']} {it['url']}: {e}")
                row["sns"] = p.get("sns", {})
                row["sources"] = p.get("sources", [it["url"]])
                row["checked"] = p.get("checked", "")
                row["unreachable"] = today()
            rows.append(row)
            time.sleep(1)
        out_groups.append({"id": g["id"], "name": g["name"], "items": rows})
    out = {"criteria": "政府（首相官邸・内閣官房・内閣府と各省庁）、国会（衆議院・参議院・国立国会図書館）、政党（このサイトの政党の一覧のすべて）の公式サイトと、公式サイトのトップページ・SNS一覧ページにリンクされているSNSアカウントを、すべて載せる。",
           "rule": "アカウントは機械で集め、人は選ばない。並びは X・YouTube・Instagram・TikTok・Facebook・LINE・note・Threads。各SNSの中は、トップページに載っている順、次にSNS一覧ページに載っている順。画面には各SNSの最初の1つを出し、ほかは「ほかN件」にたたむ。",
           "order": ORDER, "groups": out_groups}
    same = json.dumps({**old, "groups": [{**g, "items": [{k: v for k, v in x.items() if k != "checked"} for x in g["items"]]} for g in old.get("groups", [])]}, sort_keys=True, ensure_ascii=False) == \
        json.dumps({**out, "groups": [{**g, "items": [{k: v for k, v in x.items() if k != "checked"} for x in g["items"]]} for g in out_groups]}, sort_keys=True, ensure_ascii=False)
    if not same:
        json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changes


if __name__ == "__main__":
    try:
        for c in update():
            print("[accounts] " + c)
    finally:
        close_browser()
