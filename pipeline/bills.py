"""成立しなかった法律案の一覧（data/bills.json）を、機械で作る（AIは使わない：ルールC-6・D-10）。

出どころ：衆議院「議案の一覧」（国会ごと。閣法・衆法・参法のすべてと、審議状況）と、法案ごとの「議案審議経過情報」。
- 載せる：第220回国会から後の国会に出された法律案のうち、成立していないものすべて
  （審議未了・継続審査・撤回・衆議院や参議院で否決・審議中）。
- 題名・提出者・提出会派・日付・結果は、衆議院のページの表記のまま。結果のことば（「審議未了」など）は、下の STATUS の決まりで機械的にそろえる。
- 次の国会に持ち越された法案は、いちばん新しい国会の一覧の審議状況を使う（同じ法案を2回載せない）。
- 経過情報のページは、審議状況が変わったときだけ読み直す。

update_laws.py は、ここで「否決」になった法案の記事の下書きを作る（成立したものと同じく、AIが書いて確かめる）。
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import shugiin as H  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PATH = os.path.join(DATA, "bills.json")
SLUG = {"閣法": "kakuho", "衆法": "shuho", "参法": "sanho"}
CAT_ORDER = {"閣法": 0, "衆法": 1, "参法": 2}
CRITERIA = "第220回国会（2026年1月召集）から後の国会に出された法律案のうち、成立していないものを、すべて載せる（審議未了・継続審査・撤回・否決・審議中）。"
STATUS_RULE = ("結果は衆議院「議案の一覧」の審議状況と「議案審議経過情報」から機械的に決める："
               "衆議院または参議院の審議結果が「否決」→「衆議院で否決」「参議院で否決」、"
               "「閉会中審査」→「継続審査」（次の国会に持ち越し）、「未了」→「審議未了」（採決されないまま国会が終わった）、「撤回」→「撤回」、"
               "国会が開いている間のそのほかの状況→「審議中」。")
SOURCE = {"label": "衆議院 議案の一覧・議案審議経過情報", "url": "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/menu.htm"}


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def result_of(status, k, open_session):
    if k.get("衆議院審議結果") == "否決":
        return "衆議院で否決"
    if k.get("参議院審議結果") == "否決":
        return "参議院で否決"
    if "閉会中審査" in status or k.get("衆議院審議結果") == "閉会中審査" or k.get("参議院審議結果") == "閉会中審査":
        return "継続審査"
    if status == "未了":
        return "審議未了"
    if status == "撤回":
        return "撤回"
    return "審議中" if open_session else status


def sessions_of(feed):
    """feed.json の国会（第220回から後）と、それぞれ開会中かどうか。"""
    out = []
    for s in feed.get("sessions", []):
        m = re.match(r"第(\d+)回", s.get("name", ""))
        if m and int(m.group(1)) >= 220:
            out.append((m.group(1), not s.get("end")))
    return out


def chamber_of(k, house):
    d = {"committee": k.get(f"{house}付託委員会", ""), "result": k.get(f"{house}審議結果", ""),
         "date": H.wdate(k.get(f"{house}審議終了年月日", ""))}
    return {x: y for x, y in d.items() if y}


def update():
    """data/bills.json を作り直す。変わったことの説明のリストを返す。"""
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    old = json.load(open(PATH, encoding="utf-8")) if os.path.exists(PATH) else {"items": []}
    prev = {x["id"]: x for x in old.get("items", [])}
    articles = {}
    for it in feed.get("items", []):
        if it.get("bill_id"):
            articles[it["bill_id"]] = it["id"]
    rows = {}
    ok_sessions = []
    for ses, is_open in sessions_of(feed):
        try:
            lst = H.list_bills(ses)
        except Exception as e:  # noqa: BLE001
            print(f"[bills] 第{ses}回国会の一覧を読めませんでした: {e}")
            continue
        ok_sessions.append(ses)
        for r in lst:
            rid = f"{r['session']}-{SLUG[r['cat']]}-{r['no']}"
            r["_open"] = is_open
            r.setdefault("_listed", [])
            if rid in rows:
                r["_listed"] = rows[rid]["_listed"]
            r["_listed"].append(ses)
            rows[rid] = r  # 新しい国会の一覧で上書き（持ち越しの法案）
    if not ok_sessions:
        return []
    changes, items = [], []
    for rid, r in rows.items():
        if r["status"] in ("成立",) or not re.fullmatch(r"\d+", r["no"]) or int(r["session"] or 0) < 220:
            continue
        p = prev.get(rid)
        if p and p.get("status") == r["status"] and p.get("_keika_ok"):
            item = dict(p)
        else:
            try:
                k = H.parse_keika(r["keika"]) if r["keika"] else {}
            except Exception as e:  # noqa: BLE001
                print(f"[bills] 経過情報を読めませんでした {rid}: {e}")
                if p:
                    items.append(p)
                continue
            item = {
                "id": rid, "session": f"第{r['session']}回国会", "category": r["cat"], "number": f"{r['cat']}第{r['no']}号",
                "title": r["title"], "status": r["status"],
                "submitter": "内閣" if r["cat"] == "閣法" else ("衆議院議員" if r["cat"] == "衆法" else "参議院議員"),
                "proposer": re.sub(r"\s+", "", k.get("議案提出者", "")) if r["cat"] != "閣法" else "",
                "groups": H.groups(k.get("議案提出会派", "")),
                "submitted": H.wdate(k.get("衆議院議案受理年月日") if r["cat"] != "参法" else k.get("参議院議案受理年月日", "")) or "",
                "shugiin": chamber_of(k, "衆議院"), "sangiin": chamber_of(k, "参議院"),
                "votes": {"for": H.groups(k.get("衆議院審議時賛成会派")), "against": H.groups(k.get("衆議院審議時反対会派"))} if k.get("衆議院審議時賛成会派") or k.get("衆議院審議時反対会派") else None,
                "source": {"label": f"衆議院 議案審議経過情報（第{r['session']}回国会 {r['cat']}第{r['no']}号）", "url": r["keika"]},
                "outline": H.youkou_url(r["honbun"]),
                "_keika_ok": bool(r["keika"]),
            }
            if not item["votes"]:
                del item["votes"]
            for key in ("proposer", "submitted"):
                if not item[key]:
                    del item[key]
            if p and p.get("status") != r["status"]:
                changes.append(f"{item['number']}（第{r['session']}回）：{p.get('status')} → {r['status']}")
            elif not p:
                changes.append(f"追加：{item['number']}（第{r['session']}回）{r['title'][:30]}")
        item["result"] = result_of(item["status"], {"衆議院審議結果": item.get("shugiin", {}).get("result", ""),
                                                     "参議院審議結果": item.get("sangiin", {}).get("result", "")}, r["_open"])
        item["listed"] = [f"第{x}回国会" for x in r["_listed"]]
        if rid in articles:
            item["article"] = articles[rid]
        else:
            item.pop("article", None)
        items.append(item)
    items.sort(key=lambda x: (-int(x["id"].split("-")[0]), CAT_ORDER.get(x["category"], 9), int(x["id"].split("-")[-1])))
    out = {"criteria": CRITERIA, "status_rule": STATUS_RULE,
           "order": "国会（提出された回）の新しい順。同じ国会の中は 閣法→衆法→参法、提出番号の小さい順。",
           "count": len(items), "count_by_result": {}, "checked": old.get("checked", ""), "source": SOURCE, "items": items}
    for it in items:
        out["count_by_result"][it["result"]] = out["count_by_result"].get(it["result"], 0) + 1
    same = json.dumps({**old, "checked": ""}, sort_keys=True, ensure_ascii=False) == json.dumps({**out, "checked": ""}, sort_keys=True, ensure_ascii=False)
    if not same:
        out["checked"] = today()
        json.dump(out, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if not changes:
            changes.append("成立しなかった法律案の一覧を更新")
    return changes


if __name__ == "__main__":
    for c in update():
        print("[bills] " + c)
