"""トップページに出す数（機械で数える。AIは使わない）。

使い方：python pipeline/stats.py [書き出す先]（ふだんは data/stats.json。build_site.sh は _site/data/stats.json に書く）
"""
import collections
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def load(name):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def main(out):
    feed = load("feed.json").get("items", [])
    kinds = collections.Counter(x.get("kind") for x in feed)
    q = load("questions.json").get("items", [])
    rs = load(os.path.join("rs", "projects.json")).get("items", [])
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
    pub = [x for x in load("pubcom.json").get("items", []) if x.get("status") == "意見募集中" and x.get("deadline", "") >= today]
    s = {
        "checked": today,
        "articles": len(feed),
        "laws": kinds.get("法律が成立", 0),
        "questions": len(q),
        "questions_since": min((x["submitted"] for x in q if x.get("submitted")), default=""),
        "members": len(load("members.json").get("members", [])),
        "bills": len(load("bills.json").get("items", [])),
        "councils": len(load("councils.json").get("items", [])),
        "budget_projects": len(rs),
        "budget_year": (rs[0].get("budget") or {}).get("year") if rs else None,
        "pubcom_open": len(pub),
        "kakugi": len(load("kakugi.json").get("meetings", [])),
        "learn": sum(len(x.get("pages", [])) for x in load("learn.json").get("sections", [])),
        "rules": sum(len(g.get("rules", [])) for g in load("rules.json").get("groups", [])),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(s, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "stats.json"))
