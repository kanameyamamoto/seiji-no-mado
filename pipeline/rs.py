"""行政事業レビュー（国の予算事業と、その支出先）を、機械で整える（AIは使わない：ルールC-6・D-13）。

出どころ：行政事業レビュー見える化サイト（RSシステム、https://rssystem.go.jp/）の CSV（ZIP）。
  1-2 基本情報_事業概要等 … 事業名・府省庁・目的・事業区分・開始年度
  2-1 予算・執行_サマリ   … 年度ごとの当初予算・補正予算・歳出予算現額・執行額・翌年度要求額
  5-1 支出先_支出情報     … 支出先ブロックごとの支出先名・法人番号・支出額
利用条件：RSシステムの利用規約（公共データ利用規約 PDL1.0 に準拠）。出典を示し、加工したことを書く。

整え方（人は選ばない）：
- レビューシートのある事業を、すべて載せる。並びは、府省庁の建制順 → 予算事業IDの順。
- 予算は、その年度のシートにある「予算年度＝シートの年度」の行（当初・補正・歳出予算現額・翌年度要求額）と、前の年度の執行額。
- 支出先は、正しい法人番号のあるものだけを名前つきで載せる（個人・「その他」・9999999999999 のような仮の番号は名前を出さず、支出先ブロックごとの合計だけ）。
  同じ法人番号に、府省庁の出先機関などいくつもの名前が書かれていることがある。名前は、いちばん多く書かれたものを使う。
  支出先ブロック（A・B・…）ごとに、そのブロックの役割と、支出先ごとの「支出先の合計支出額」をそのまま載せる。
  間接の支出先（国からの支出先を経由したもの）も含むので、ブロックをまたいで足すと二重に数えることがある（ページにもそう書く）。
- 作成責任者など、職員個人の名前は載せない。

書き出すもの（data/rs/）：
  projects.json        … 事業の一覧（名前・府省庁・予算・支出先の数）
  detail-mNN.json     … 府省庁ごとの、事業のくわしい中身（目的・担当・支出先。NN は projects.json の ministries の slug）
  corps.json           … 法人番号ごとの、名前と、支出を受けた事業
使い方：python pipeline/rs.py [年度]   （年度を省くと config の rs.year）
"""
import csv
import datetime
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "rs")
BASE = "https://rssystem.go.jp/files/{y}/rs/"
FILES = {"summary": "1-2_RS_{y}_基本情報_事業概要等", "budget": "2-1_RS_{y}_予算・執行_サマリ", "payee": "5-1_RS_{y}_支出先_支出情報"}
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
SOURCE = {"label": "行政事業レビュー見える化サイト（RSシステム）CSV", "url": "https://rssystem.go.jp/download-csv"}
TERMS = {"label": "RSシステム 利用規約（公共データ利用規約 PDL1.0 に準拠）", "url": "https://rssystem.go.jp/terms"}
CRITERIA = "{y}年度の行政事業レビューシートがある国の事業を、すべて載せる（{n}事業）。"
RULE = ("事業名・府省庁・予算・支出先は、RSシステムの CSV の表記と金額のまま（せいじのまどが整えて載せている）。"
        "支出先は法人番号のあるものだけ名前を載せ、ほかは支出先ブロックごとの合計だけにする。"
        "間接の支出先も含むため、ブロックをまたいで金額を足すと二重に数えることがある。並びは府省庁の建制順、その中は予算事業IDの順。")


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def get_csv(name, y, cache=None):
    fn = name.format(y=y)
    if cache and os.path.exists(os.path.join(cache, fn + ".zip")):
        raw = open(os.path.join(cache, fn + ".zip"), "rb").read()
    else:
        url = BASE.format(y=y) + urllib.parse.quote(fn + ".zip")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=300) as r:
            raw = r.read()
    z = zipfile.ZipFile(io.BytesIO(raw))
    member = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    return list(csv.DictReader(io.StringIO(z.read(member).decode("utf-8-sig"))))


def num(s):
    s = (s or "").strip().replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v == int(v) else v


def valid_corp(n):
    """法人番号として正しいか（13桁で、先頭のチェック用の数字が合う）。9999999999999 などの仮の番号は外す。"""
    if not re.fullmatch(r"\d{13}", n or "") or len(set(n)) == 1:
        return False
    d = [int(x) for x in n[1:]]
    return int(n[0]) == 9 - sum(v * (1 if i % 2 == 0 else 2) for i, v in enumerate(reversed(d))) % 9


def slug(name):
    return re.sub(r"[^\w]", "", name)


def build(y, cache=None):
    S = get_csv(FILES["summary"], y, cache)
    B = get_csv(FILES["budget"], y, cache)
    P = get_csv(FILES["payee"], y, cache)
    budget = {}
    for r in B:
        if r["会計区分"]:  # 会計ごとの内訳の行は使わない（合計の行だけ）
            continue
        budget.setdefault(r["予算事業ID"], {})[r["予算年度"]] = r
    projects, seen = [], set()
    for r in S:
        pid = r["予算事業ID"]
        if pid in seen:
            continue
        seen.add(pid)
        b = budget.get(pid, {})
        cur, prev = b.get(str(y), {}), b.get(str(int(y) - 1), {})
        it = {"id": pid, "name": r["事業名"], "ministry": r["政策所管府省庁"] or r["府省庁"], "office": r["府省庁"],
              "bureau": "・".join(x for x in (r["局・庁"], r["部"], r["課"]) if x), "order": int(r["府省庁の建制順"] or 999),
              "purpose": (r["事業の目的"] or "").replace("／", "\n").strip()[:400], "category": r["事業区分"],
              "start": r["事業開始年度"], "end": r["事業終了（予定）年度"], "url": r["事業概要URL"],
              "budget": {k: v for k, v in {"year": int(y), "initial": num(cur.get("当初予算（合計）")), "supplementary": num(cur.get("補正予算（合計）")),
                                            "current": num(cur.get("計（歳出予算現額合計）")), "request_next": num(cur.get("翌年度要求額（合計）"))}.items() if v is not None},
              "executed_prev": {k: v for k, v in {"year": int(y) - 1, "executed": num(prev.get("執行額（合計）")), "current": num(prev.get("計（歳出予算現額合計）"))}.items() if v is not None}}
        if len(it["executed_prev"]) == 1:
            del it["executed_prev"]
        projects.append(it)
    projects.sort(key=lambda x: (x["order"], int(x["id"]) if x["id"].isdigit() else 0))
    # 支出先
    pay, corps, blocks = {}, {}, {}
    for r in P:
        r = {k: (v or "").strip() for k, v in r.items()}
        pid, blk = r["予算事業ID"], r["支出先ブロック番号"]
        bk = (pid, blk)
        if bk not in blocks:
            blocks[bk] = {"block": blk, "role": r["事業を行う上での役割"] or r["支出先ブロック名"], "total": num(r["ブロックの合計支出額"]),
                          "count": num(r["支出先の数"]), "payees": {}, "other": 0}
        if not r["支出先名"]:
            continue
        amt = num(r["支出先の合計支出額"])
        if valid_corp(r["法人番号"]):
            p = blocks[bk]["payees"].setdefault(r["法人番号"], {"n": r["支出先名"], "c": r["法人番号"], "a": amt})
            if p["a"] is None:
                p["a"] = amt
        else:
            key = ("other", r["支出先名"])
            if key not in blocks[bk]:
                blocks[bk][key] = True
                blocks[bk]["other"] += amt or 0
    by_min = {}
    pmeta = {p["id"]: p for p in projects}
    for (pid, blk), v in sorted(blocks.items()):
        if pid not in pmeta:
            continue
        entry = {"block": v["block"], "role": v["role"], "total": v["total"], "count": v["count"],
                 "payees": sorted(v["payees"].values(), key=lambda x: -(x["a"] or 0))}
        if v["other"]:
            entry["other"] = v["other"]
        by_min.setdefault(pmeta[pid]["ministry"], {}).setdefault(pid, []).append(entry)
        for p in v["payees"].values():
            c = corps.setdefault(p["c"], {"names": {}, "projects": {}})
            c["names"][p["n"]] = c["names"].get(p["n"], 0) + 1
            c["projects"][pid] = (c["projects"].get(pid) or 0) + (p["a"] or 0)
    for p in projects:
        bl = by_min.get(p["ministry"], {}).get(p["id"], [])
        p["payee_count"] = sum(len(b["payees"]) for b in bl)
    return projects, by_min, corps


def write(y, cache=None):
    import hashlib
    projects, by_min, corps = build(y, cache)
    digest = hashlib.sha256(json.dumps([projects, by_min, sorted(corps)], ensure_ascii=False, sort_keys=True, default=list).encode()).hexdigest()[:16]
    old = os.path.join(OUT, "projects.json")
    if os.path.exists(old) and json.load(open(old, encoding="utf-8")).get("hash") == digest:
        print(f"[rs] {y}年度：変わっていない")
        return False
    os.makedirs(OUT, exist_ok=True)
    mins = []
    for p in projects:
        if not mins or mins[-1]["name"] != p["ministry"]:
            if p["ministry"] not in [m["name"] for m in mins]:
                mins.append({"name": p["ministry"], "slug": f"m{len(mins) + 1:02d}", "count": 0, "initial": 0})
        m = [m for m in mins if m["name"] == p["ministry"]][0]
        m["count"] += 1
        m["initial"] += p["budget"].get("initial") or 0
    meta = {"year": int(y), "hash": digest, "criteria": CRITERIA.format(y=y, n=len(projects)), "rule": RULE, "sources": [SOURCE, TERMS], "checked": today()}
    for f in os.listdir(OUT):
        if f.startswith(("payees-", "detail-")):
            os.remove(os.path.join(OUT, f))
    DETAIL = ("bureau", "purpose", "start", "end", "url", "office")
    for m in mins:
        d = {p["id"]: {**{k: p[k] for k in DETAIL if p.get(k)}, "blocks": by_min.get(m["name"], {}).get(p["id"], [])} for p in projects if p["ministry"] == m["name"]}
        json.dump({**meta, "ministry": m["name"], "items": d}, open(os.path.join(OUT, f"detail-{m['slug']}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for p in projects:
        for k in DETAIL:
            p.pop(k, None)
        p.pop("order", None)
    json.dump({**meta, "ministries": mins, "items": projects}, open(os.path.join(OUT, "projects.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    cl = {k: {"n": max(v["names"].items(), key=lambda x: (x[1], -len(x[0])))[0], **({"aka": sorted(v["names"])[:5]} if len(v["names"]) > 1 else {}), "p": sorted(v["projects"].items(), key=lambda x: -x[1])} for k, v in sorted(corps.items())}
    json.dump({**meta, "note": "法人番号ごとの支出先。p＝[予算事業ID, その事業での支出先の合計支出額（ブロックをまたいで足したもの）]。", "items": cl},
              open(os.path.join(OUT, "corps.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[rs] {y}年度：{len(projects)}事業・府省庁 {len(mins)}・名前つきの支出先 {len(corps)}法人")


if __name__ == "__main__":
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")).get("rs", {})
    y = sys.argv[1] if len(sys.argv) > 1 else str(cfg.get("year", 2025))
    write(y, os.environ.get("RS_CACHE"))
