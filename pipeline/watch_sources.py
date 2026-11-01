"""出典の見張り（AIは使わない）。

すべてのページの出典を取りに行き、前回と比べる。
- 法令：e-Gov の改正履歴に、新しい改正が加わったか
- そのほか：本文が変わったか（日付・更新日などの表示は無視）
- 開けなくなった出典（404など）が3回続いたか

変わった出典を使っているページを、data/watch.json の due（見直し待ち）に入れる。
実際に中身を直すかどうかは、見直し（recheck.py）で出典と照らして決める。

使い方：python pipeline/watch_sources.py [--limit N] [--only egov]
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
import sources as SRC  # noqa: E402
import units as U  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(ROOT, "data", "watch.json")
JST = datetime.timezone(datetime.timedelta(hours=9))


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def load_watch():
    if os.path.exists(WATCH):
        return json.load(open(WATCH, encoding="utf-8"))
    return {"note": "出典の見張りの記録（pipeline/watch_sources.py）と、見直し待ちのページ（pipeline/recheck.py が使う）。",
            "sources": {}, "due": {}}


def save_watch(w):
    json.dump(w, open(WATCH, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


def add_due(w, uid, reason, day):
    lst = w["due"].setdefault(uid, [])
    if not any(r["reason"] == reason for r in lst):
        lst.append({"date": day, "reason": reason})


def check_host(urls):
    """同じサイトには1秒あけて順番に。"""
    out = {}
    if urllib.parse.urlparse(urls[0]).netloc in SRC.STATIC_HOSTS:
        return {u: ("static", "", "", "") for u in urls}
    for u in urls:
        try:
            out[u] = ("ok",) + SRC.fingerprint(u)
        except urllib.error.HTTPError as e:
            out[u] = ("error", "", "", f"HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            out[u] = ("error", "", "", type(e).__name__ + ": " + str(e)[:120])
        time.sleep(1)
    # サイト全体が 403 を返すときは、そのサイトがこのプログラムからのアクセスを断っている（リンク切れではない）
    if len(urls) >= 2 and all(v[0] == "error" and v[3] == "HTTP 403" for v in out.values()):
        out = {u: ("blocked", "", "", "HTTP 403（サイトがアクセスを断っている）") for u in urls}
    return out


def check_links(w, data, day):
    """メディアの一覧のリンク（各社のトップページ）が開けるかだけを見る。3回続けて開けなければ人へ知らせる。"""
    links = {}
    for sec in data["media"]["sections"]:
        for d in sec.get("directory", []):
            if d.get("url", "").startswith("http"):
                links[d["url"]] = f"{sec['name']}：{d.get('name', '')}"
    hosts = {}
    for u in links:
        hosts.setdefault(urllib.parse.urlparse(u).netloc, []).append(u)

    def alive(urls):
        out = {}
        for u in urls:
            try:
                SRC.get(u, retries=1)
                out[u] = None
            except Exception as e:  # noqa: BLE001
                out[u] = type(e).__name__ + ": " + str(e)[:80]
            time.sleep(1)
        return out
    res = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(alive, hosts.values()):
            res.update(r)
    st = w.setdefault("links", {})
    for u, err in res.items():
        if err is None:
            st.pop(u, None)
            continue
        e = st.setdefault(u, {"fails": 0})
        e["fails"] += 1
        e["last_error"] = err
        if e["fails"] == 3:
            w.setdefault("alerts", []).append({"date": day, "title": f"リンク切れ：{links[u]}",
                                                "body": f"{u} が3回続けて開けません（{err}）。data/media.json を直してください。"})
    for u in list(st):
        if u not in links:
            del st[u]
    print(f"メディアのリンク {len(res)} 件：開けない {sum(1 for v in res.values() if v)} 件")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="試しに見る出典の数")
    ap.add_argument("--only", choices=["egov", "other"], help="法令だけ／法令以外だけ")
    a = ap.parse_args()
    data = U.load()
    us = U.units(data)
    m = U.by_url(us)
    labels = {s["url"]: s["label"] for u in us for s in u.sources}
    w = load_watch()
    day = today()
    urls = sorted(m)
    if a.only == "egov":
        urls = [u for u in urls if SRC.egov_id(u)]
    elif a.only == "other":
        urls = [u for u in urls if not SRC.egov_id(u)]
    if a.limit:
        urls = urls[: a.limit]
    hosts = {}
    for u in urls:
        hosts.setdefault(urllib.parse.urlparse(u).netloc, []).append(u)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(check_host, hosts.values()):
            results.update(r)

    changed = broken = new = 0
    blocked = sorted({urllib.parse.urlparse(u).netloc for u, r in results.items() if r[0] == "blocked"})
    for u, (status, kind, fp, info) in results.items():
        st = w["sources"].get(u)
        label = labels.get(u, u)
        if status in ("static", "blocked"):
            w["sources"][u] = {**(st or {}), "kind": status, "checked": day}
            continue
        if status == "error":
            st = st or {}
            st["fails"] = st.get("fails", 0) + 1
            st["last_error"] = info
            st["checked"] = day
            w["sources"][u] = st
            if st["fails"] == 3:
                broken += 1
                for uid in m[u]:
                    add_due(w, uid, f"出典「{label}」が3回続けて開けない（{info}）", day)
            continue
        if not st or not st.get("fp"):
            w["sources"][u] = {"kind": kind, "fp": fp, "since": day, "checked": day}
            new += 1
            continue
        if st["fp"] != fp:
            changed += 1
            why = f"法令が改正された：{info}" if kind == "egov" else "内容が変わった"
            for uid in m[u]:
                add_due(w, uid, f"出典「{label}」の{why}", day)
            st.update({"fp": fp, "since": day})
        st.update({"kind": kind, "checked": day, "fails": 0})
        st.pop("last_error", None)
        w["sources"][u] = st
    if not a.only and not a.limit:
        check_links(w, data, day)
    # 使われなくなった出典は消す
    for u in list(w["sources"]):
        if u not in m:
            del w["sources"][u]
    save_watch(w)
    print(f"見た出典 {len(results)} 件：初めて {new}、変わった {changed}、開けない(3回目) {broken}、"
          f"エラー {sum(1 for r in results.values() if r[0] == 'error')}")
    if blocked:
        print("アクセスを断られたサイト（見張れない）：" + "、".join(blocked))
    print(f"見直し待ちのページ：{len(w['due'])} 件")


if __name__ == "__main__":
    main()
