"""参議院の本会議で否決された法律案を、機械で集める（AIは使わない：ルールC-6）。

「投票記録とくらべる」（ルールD-9）で、成立したものと同じように使うため。
- 対象：第220回国会から後の国会で、参議院の本会議で押しボタン式で採決され、否決された法律案（閣法・衆法・参法）。
- 予算・条約は、参議院で否決されても衆議院の議決で成立することがあるので、ここには入れない。
- 衆議院で否決された法律案は、参議院では採決されない（参議院の投票記録がない）ので入らない。
- 文章は書かない。題名・議案要旨・経過・会派ごとの票数は、参議院の議案情報と本会議投票結果の表記のまま。

update_laws.py が、議案を1件ずつ読むついでに consider() を呼ぶ。
"""
import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "rejected.json")
CAT = {"法律案（内閣提出）": ("閣法", "kakuho"), "法律案（衆法）": ("衆法", "shuho"), "法律案（参法）": ("参法", "sanho")}
CRITERIA = ("第220回国会（2026年1月召集）から後の国会で、参議院の本会議で押しボタン式で採決され、否決された法律案を、すべて載せる。"
            "予算・条約（衆議院の議決で成立することがある）と、衆議院で否決された法律案（参議院の投票記録がない）は入らない。")


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def load():
    if os.path.exists(PATH):
        d = json.load(open(PATH, encoding="utf-8"))
    else:
        d = {}
    d.setdefault("criteria", CRITERIA)
    d.setdefault("items", [])
    d.setdefault("scanned", {})
    d["_changed"] = False
    return d


def save(d):
    changed = d.pop("_changed", False)
    if not changed and os.path.exists(PATH):
        return False
    d["criteria"] = CRITERIA
    d["items"].sort(key=lambda x: (x.get("date", ""), x["id"]), reverse=True)
    d["count"] = len(d["items"])
    out = {k: d[k] for k in ("criteria", "count", "checked", "scanned", "items") if k in d}
    json.dump(out, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


def feed_sessions(feed):
    """feed.json に載っている国会の回次（第220回から後）。"""
    out = []
    for s in feed.get("sessions", []):
        m = re.match(r"第(\d+)回", s.get("name", ""))
        if m and int(m.group(1)) >= 220:
            out.append(m.group(1))
    return out


def tidy_yoshi(t):
    """議案要旨の見出し（付託委員会の名前と「…要旨」の行）を外し、空行をつめる。本文の文字は変えない。"""
    lines = [x.strip() for x in (t or "").replace("\r", "").split("\n")]
    lines = [x for x in lines if x]
    for i, x in enumerate(lines[:4]):
        if x.endswith("要旨"):
            lines = lines[i + 1:]
            break
    return "\n".join(lines)


def consider(d, r, ses, feed, url, S, chamber, votes_of, session_label):
    """成立しなかった議案 r（sangiin.parse_bill の結果）が、載せる範囲なら d に入れる。"""
    typ = r.get("議案審議一覧/種別", "")
    if typ not in CAT or r.get("参議院本会議経過/議決", "") != "否決" or not r.get("参議院本会議経過/vote"):
        return
    cat, slug = CAT[typ]
    no = r.get("議案審議一覧/提出番号", "")
    rid = f"rej-{ses}-{slug}-{no}"
    if any(x["id"] == rid for x in d["items"]):
        return
    v = votes_of(S.parse_vote(r["参議院本会議経過/vote"]))
    if not v:
        return
    item = {
        "id": rid,
        "kind": "法律案が否決",
        "date": S.wdate(r.get("参議院本会議経過/議決日", "")) or v.get("date", ""),
        "session": session_label(feed, ses),
        "title": r.get("議案審議一覧/件名", ""),
        "number": f"{cat}第{no}号",
        "submitter": "内閣" if cat == "閣法" else "議員",
        "proposer": r.get("議案審議一覧/発議者", "") if cat != "閣法" else "",
        "yoshi": tidy_yoshi(r.get("yoshi", "")),
        "process": {"shugiin": chamber(r, "衆議院"), "sangiin": chamber(r, "参議院")},
        "votes": v,
        "sources": [{"label": f"参議院 議案情報（第{ses}回国会 {cat}第{no}号）", "url": url}],
        "checked": today(),
    }
    for k in ("shugiin", "sangiin"):
        if not item["process"][k].get("result"):
            del item["process"][k]
    d["items"].append(item)
    d["_changed"] = True
    print(f"[否決] {rid} {item['title']}")


def mark_scanned(d, ses):
    if ses not in d["scanned"]:
        d["scanned"][ses] = today()
        d["_changed"] = True
