"""チェックに通った下書き（status=passed）を feed.json に入れる。

並び順は feed.json の criteria_detail.order のとおり：
成立日（否決されたものは否決された日）の新しい順。同じ日の中は 法律→予算→条約→否決された法律案、閣法→衆法→参法、提出番号の小さい順。
"""
import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PENDING = os.path.join(DATA, "pending")
JST = datetime.timezone(datetime.timedelta(hours=9))
KIND_ORDER = {"法律が成立": 0, "予算が成立": 1, "条約を承認": 2, "法律案が否決": 3}
CAT_ORDER = {"閣法": 0, "衆法": 1, "参法": 2}


def sort_key(it):
    m = re.match(r"(閣法|衆法|参法)第(\d+)号", it.get("number", ""))
    n = re.search(r"(\d+)", it.get("number", ""))
    return (
        "".join(chr(0x10FFFF - ord(c)) for c in it.get("date", "")),  # 新しい順
        KIND_ORDER.get(it.get("kind"), 9),
        CAT_ORDER.get(m.group(1), 9) if m else 9,
        int(n.group(1)) if n else 0,
    )


def main():
    feed_path = os.path.join(DATA, "feed.json")
    feed = json.load(open(feed_path, encoding="utf-8"))
    ids = {it["id"] for it in feed["items"]}
    added = []
    for fn in sorted(os.listdir(PENDING)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(PENDING, fn)
        raw = json.load(open(p, encoding="utf-8"))
        if raw.get("status") != "passed":
            continue
        item = raw["item"]
        if item["id"] in ids:
            print(f"[skip] {item['id']} はもう載っています")
        else:
            feed["items"].append(item)
            ids.add(item["id"])
            added.append(item)
        os.remove(p)
    if not added:
        print("載せる記事はありません")
        return
    feed["items"].sort(key=sort_key)
    feed["count"] = len(feed["items"])
    by = {}
    for it in feed["items"]:
        by[it["kind"]] = by.get(it["kind"], 0) + 1
    feed["count_by_kind"] = by
    feed["checked"] = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    json.dump(feed, open(feed_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for it in added:
        print(f"[publish] {it['id']} {it['title']}")
    print(f"載せた件数: {len(added)}")
    mark_amended(added)


def amended_laws(formal):
    """「AとBの一部を改正する法律」→ [A, B]。新しい法律は []。"""
    m = re.match(r"(.+?)(の一部を改正する法律|を改正する法律|の一部を改正する等の法律)$", formal or "")
    if not m:
        return []
    whole = m.group(1)
    # 「及び」でつながった複数の法律に分ける。「廃棄物の処理及び清掃に関する法律」のように
    # 名前の中に「及び」がある法律は、「法」で終わるまでつなげ直す
    out, buf = [], ""
    for tok in re.split(r"(及び|並びに|、)", whole):
        buf += tok
        if re.search(r"(法|法律)$", buf):
            out.append(re.sub(r"^(及び|並びに|、)", "", buf))
            buf = ""
    return out if not buf else [whole]


def mark_amended(added):
    """成立した法律が改正する法律を出典にしているページを、見直し待ちにする。"""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import units as U
    import watch_sources as W
    us = U.units(U.load())
    watch = W.load_watch()
    n = 0
    for it in added:
        if it.get("kind") == "法律案が否決":  # 成立していないので、ほかの法律は変わらない
            continue
        for law in amended_laws(it.get("formal", "")):
            for u in us:
                if any(s["label"].startswith(law) or f"「{law}」" in s["label"] for s in u.sources):
                    W.add_due(watch, u.id, f"出典の「{law}」を改正する法律が成立した（{it['title']}、{it['date']}）", W.today())
                    n += 1
    if n:
        W.save_watch(watch)
        print(f"改正された法律を出典にしているページ {n} 件を、見直し待ちにしました")


if __name__ == "__main__":
    main()
