"""法律や政令ができる前の手続き：パブリックコメントと閣議の案件を、機械で集める（AIは使わない：ルールC-6・D-14・D-15）。

1. パブリックコメント（意見募集）… e-Gov パブリック・コメントの RSS（意見募集中の一覧・結果公示の一覧）
   RSS には最近のものしか出ないので、毎日読んで data/pubcom.json に足していく（一度載せたものは消さない）。
   載せる：案件名・案の公示日・受付締切・カテゴリー・所管（部局名まで。電話番号は載せない）・結果の公示日・提出意見数。
   状況は日付から決める：締切前＝「意見募集中」、締切後で結果がまだ＝「結果待ち」、結果の公示があれば「結果公示」。
2. 閣議の案件 … 首相官邸「閣議」の年ごとの一覧と、閣議ごとのページ
   載せる：閣議の日・種類（定例・臨時・持ち回り）・案件の区分（一般案件・国会提出案件・法律案・政令など）・案件名・担当の府省。
   「人事」の区分（叙勲・任命などで個人の名前が並ぶ）は載せない。一度読んだ閣議のページは読み直さない。
"""
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
PC_LIST = "https://public-comment.e-gov.go.jp/rss/pcm_list.xml"
PC_RESULT = "https://public-comment.e-gov.go.jp/rss/pcm_result.xml"
KANTEI = "https://www.kantei.go.jp"
SKIP_SECTIONS = ("人事",)


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def fetch(url):
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def jload(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def jsave(name, obj, old):
    if {k: v for k, v in obj.items() if k != "checked"} != {k: v for k, v in old.items() if k != "checked"}:
        json.dump(obj, open(os.path.join(DATA, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return True
    return False


# ---------- パブリックコメント ----------
def rss_items(url):
    t = fetch(url)
    out = []
    for about, body in re.findall(r'<item rdf:about="([^"]+)">(.*?)</item>', t, flags=re.S):
        title = clean(re.search(r"<title>(.*?)</title>", body, flags=re.S).group(1))
        link = html.unescape(re.search(r"<link>(.*?)</link>", body, flags=re.S).group(1)).strip()
        desc = html.unescape(re.search(r"<description>(.*?)</description>", body, flags=re.S).group(1))
        f = {}
        for part in re.split(r"<br\s*/?>", desc):
            if "：" in part:
                k, v = part.split("：", 1)
                f[k.strip()] = v.strip()
        m = re.search(r"[?&]id=(\d+)", link)
        out.append({"id": m.group(1) if m else link, "title": title, "url": link, "f": f})
    return out


def ymd(s):
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def office_of(s):
    """問合せ先から、部局名だけを取る（電話番号・内線は載せない）。"""
    s = (s or "").split("\n")[0]
    s = re.split(r"\s*(?:電話|TEL|ＴＥＬ|Tel|\d{2,4}-\d{2,4}-\d{3,4}|（内線)", s)[0]
    return s.strip(" 　、")


def pubcom():
    old = jload("pubcom.json", {"items": []})
    items = {x["id"]: dict(x) for x in old.get("items", [])}
    changes = []
    for it in rss_items(PC_LIST):
        f = it["f"]
        x = items.get(it["id"], {})
        new = {**x, "id": it["id"], "title": it["title"], "url": it["url"], "published": ymd(f.get("案の公示日", "")),
               "deadline": ymd(f.get("受付締切日時", "")), "category": f.get("カテゴリー", ""), "office": office_of(f.get("問合せ先（所管省庁・部局名等）", ""))}
        if not x:
            changes.append(f"[pubcom] 意見募集：{it['title'][:60]}")
        items[it["id"]] = new
    for it in rss_items(PC_RESULT):
        f = it["f"]
        x = items.get(it["id"], {})
        new = {**x, "id": it["id"], "title": x.get("title") or re.sub(r"(に関する|に対する)?意見募集の結果について$", "", it["title"]),
               "result_title": it["title"], "result_url": it["url"], "result_date": ymd(f.get("結果の公示日", "")),
               "published": x.get("published") or ymd(f.get("案の公示日", "")), "deadline": x.get("deadline") or ymd(f.get("受付締切日時", "")),
               "category": x.get("category") or f.get("カテゴリー", ""), "office": x.get("office") or office_of(f.get("問合せ先（所管省庁・部局名等）", ""))}
        if f.get("提出意見数", "").isdigit():
            new["opinions"] = int(f["提出意見数"])
        if f.get("命令等の公布日"):
            new["promulgated"] = ymd(f["命令等の公布日"])
        if not x.get("result_url"):
            changes.append(f"[pubcom] 結果公示：{new['title'][:60]}")
        items[it["id"]] = new
    t = today()
    for x in items.values():
        x["status"] = "結果公示" if x.get("result_url") else ("意見募集中" if x.get("deadline", "") >= t else "結果待ち")
    lst = sorted(items.values(), key=lambda x: (x.get("published", ""), x["id"]), reverse=True)
    out = {"criteria": "e-Gov パブリック・コメントに出た意見募集（政令・省令などの案）と、その結果の公示を、2026年11月から毎日すべて載せる（RSS に出たものから足していく）。",
           "rule": "案件名・日付・カテゴリー・所管は e-Gov の表記のまま（所管は部局名まで。電話番号は載せない）。状況は日付から機械的に決める：締切前は「意見募集中」、締切後で結果がまだのものは「結果待ち」、結果が公示されたら「結果公示」。並びは案の公示日の新しい順。",
           "sources": [{"label": "e-Gov パブリック・コメント（意見募集中の案件）", "url": "https://public-comment.e-gov.go.jp/servlet/Public?CLASSNAME=PCMMSTLIST&Mode=0"},
                       {"label": "e-Gov パブリック・コメント（結果公示の案件）", "url": "https://public-comment.e-gov.go.jp/servlet/Public?CLASSNAME=PCMMSTLIST&Mode=1"}],
           "count_by_status": {s: sum(1 for x in lst if x["status"] == s) for s in ("意見募集中", "結果待ち", "結果公示")},
           "checked": t, "items": lst}
    jsave("pubcom.json", out, old)
    return changes


# ---------- 閣議 ----------
def meeting_links(year):
    t = fetch(f"{KANTEI}/jp/kakugi/{year}/index.html" if year != "now" else f"{KANTEI}/jp/kakugi/index.html")
    return sorted(set(re.findall(r'href="(/jp/kakugi/\d{4}/kakugi-\d+\.html)"', t)))


def parse_meeting(path):
    t = fetch(KANTEI + path)
    title = clean((re.search(r"<h1[^>]*>(.*?)</h1>", t, flags=re.S) or [None, ""])[1])
    m = re.search(r"kakugi-(\d{4})(\d{2})(\d{2})", path)
    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    kind = "持ち回り" if "持ち回り" in title else ("臨時" if "臨時" in title else "定例")
    i = t.find('<div class="block-center">')
    if i < 0:
        i = t.find('article-content')
    body = t[i:] if i >= 0 else t
    for stop in ("用語解説", "閣議一覧ページに戻る"):
        k = body.find(stop)
        if k >= 0:
            body = body[:k]
    j = body.find("<h2>議事録</h2>")
    minutes = ""
    if j >= 0:
        mm = re.search(r'href="([^"]+gijiroku[^"]*\.pdf)"', body[j:])
        minutes = KANTEI + mm.group(1) if mm and mm.group(1).startswith("/") else (mm.group(1) if mm else "")
        body = body[:j]
    items, section, last_office = [], "", ""
    for tag, attrs, inner in re.findall(r"<(h2|p)([^>]*)>(.*?)</\1>", body, flags=re.S):
        txt = clean(inner)
        if tag == "h2":
            section = txt.replace(" ", "")
            continue
        if not txt or not section or section in SKIP_SECTIONS or section == "議事録":
            continue
        if "aly_tx_right" in attrs or re.fullmatch(r"[（(][^（）()]+[）)]", txt):
            if items and items[-1]["section"] == section and not items[-1].get("offices"):
                o = txt.strip("（）()")
                o = last_office if o == "同上" else o
                items[-1]["offices"] = o
                last_office = o
            continue
        dm = re.search(r"（(決定|了解|報告|請議|閣議決定)）$", txt)
        items.append({"section": section, "title": re.sub(r"（(決定|了解|報告|請議|閣議決定)）$", "", txt).strip(), **({"decision": dm.group(1)} if dm else {})})
    return {"id": path.rsplit("-", 1)[1].split(".")[0], "date": date, "kind": kind, "title": title, "url": KANTEI + path,
            **({"minutes": minutes} if minutes else {}), "items": items}


def kakugi(from_year, limit=60):
    old = jload("kakugi.json", {"meetings": []})
    have = {m["id"]: m for m in old.get("meetings", [])}
    changes, read = [], 0
    years = ["now"] + [str(y) for y in range(datetime.date.today().year - 1, from_year - 1, -1)]
    for y in years:
        try:
            links = meeting_links(y)
        except Exception as e:  # noqa: BLE001
            print(f"[kakugi] {y} の一覧を読めませんでした: {e}")
            continue
        for path in links:
            mid = path.rsplit("-", 1)[1].split(".")[0]
            if mid in have or read >= limit:
                continue
            try:
                time.sleep(1)
                have[mid] = parse_meeting(path)
                read += 1
                changes.append(f"[kakugi] {have[mid]['date']} {have[mid]['kind']}閣議（{len(have[mid]['items'])}件）")
            except Exception as e:  # noqa: BLE001
                print(f"[kakugi] 読めませんでした {path}: {e}")
    meetings = sorted(have.values(), key=lambda m: m["id"], reverse=True)
    out = {"criteria": f"{from_year}年からの閣議（定例・臨時・持ち回り）の案件を、首相官邸の「閣議」のページから、すべて載せる（「人事」の区分は載せない）。",
           "rule": "案件の区分・案件名・担当の府省は、首相官邸のページの表記のまま。「同上」は、すぐ上の案件と同じ府省として書く。並びは閣議の新しい順、閣議の中はページの順。",
           "sources": [{"label": "首相官邸 閣議", "url": f"{KANTEI}/jp/kakugi/index.html"}],
           "count": {"meetings": len(meetings), "items": sum(len(m["items"]) for m in meetings)}, "checked": today(), "meetings": meetings}
    jsave("kakugi.json", out, old)
    return changes


def update():
    cfg = jload("config.json", {}).get("steps", {})
    ch = []
    try:
        ch += pubcom()
    except Exception as e:  # noqa: BLE001
        print(f"[pubcom] 読めませんでした: {e}")
    try:
        ch += kakugi(int(cfg.get("kakugi_from_year", 2025)), int(cfg.get("kakugi_limit", 60)))
    except Exception as e:  # noqa: BLE001
        print(f"[kakugi] 読めませんでした: {e}")
    return ch


if __name__ == "__main__":
    for c in update()[:40]:
        print(c)
