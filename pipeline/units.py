"""サイトの中身を「見直しの単位（ユニット）」に分ける。

ユニット＝AIがまとめて見直す1かたまり（学ぶの1ページ、つながりの1ページ、1つの政党、選挙結果、メディアの1分類）。
それぞれに、出典の一覧・確認日・見直しの間隔がある。

「国会で決まったこと」の記事は、成立した時点の事実なので、AIの見直しはしない
（公布日・法律番号の後追いは mechanical.py が機械で行う）。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FILES = ["learn", "links", "parties", "media"]

# 機械で更新する項目は、AIの見直しでは触らない（AIには見せず、あとで元に戻す）
LOCKED = {"party": ["members", "id", "url", "platform_url"]}


def load():
    return {f: json.load(open(os.path.join(DATA, f + ".json"), encoding="utf-8")) for f in FILES}


def save(data, names=None):
    for f in names or FILES:
        json.dump(data[f], open(os.path.join(DATA, f + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def urls_in(obj, skip_keys=()):
    """オブジェクトの中の出典（url を持つ辞書）を、順番どおり重複なしで集める。"""
    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            u = o.get("url")
            if isinstance(u, str) and u.startswith("http") and u not in seen:
                seen.add(u)
                out.append({"label": o.get("label") or o.get("name") or "", "url": u})
            for k, v in o.items():
                if k not in skip_keys:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return out


class Unit:
    def __init__(self, uid, kind, file, title, holder, key, sources, checked_get, checked_set):
        self.id, self.kind, self.file, self.title = uid, kind, file, title
        self._holder, self._key = holder, key
        self.sources = sources
        self._cg, self._cs = checked_get, checked_set

    def get(self):
        return self._holder[self._key]

    def set(self, value):
        self._holder[self._key] = value

    @property
    def checked(self):
        return self._cg() or ""

    def mark_checked(self, day):
        self._cs(day)


def units(data):
    """すべてのユニットを返す。"""
    out = []
    for f, kind in (("learn", "learn"), ("links", "links")):
        for s in data[f]["sections"]:
            for i, p in enumerate(s["pages"]):
                out.append(Unit(f"{kind}:{p['id']}", kind, f, p["title"], s["pages"], i, urls_in(p),
                                (lambda p=p: p.get("checked")), (lambda d, p=p: p.__setitem__("checked", d))))
    P = data["parties"]
    for i, p in enumerate(P["national"]):
        out.append(Unit(f"party:{p['id']}", "party", "parties", p["name"], P["national"], i,
                        urls_in(p, skip_keys=("members",)),
                        (lambda p=p: p.get("checked") or P.get("checked")), (lambda d, p=p: p.__setitem__("checked", d))))
    for i, p in enumerate(P["others"]):
        out.append(Unit(f"party:o{i}", "party", "parties", p["name"], P["others"], i, urls_in(p),
                        (lambda p=p: p.get("checked") or P.get("checked")), (lambda d, p=p: p.__setitem__("checked", d))))
    E = P["elections"]
    out.append(Unit("elections", "elections", "parties", "選挙の結果", P, "elections", urls_in(E),
                    (lambda: E.get("checked") or P.get("checked")), (lambda d: P["elections"].__setitem__("checked", d))))
    M = data["media"]
    for i, s in enumerate(M["sections"]):
        # メディアの一覧は各社のトップページへのリンクなので、出典の見張りの対象にはしない（毎日変わるため）
        src = urls_in(s.get("pages", []))
        out.append(Unit(f"media:{s['id']}", "media", "media", s["name"], M["sections"], i, src,
                        (lambda s=s: s.get("checked") or M.get("checked")), (lambda d, s=s: s.__setitem__("checked", d))))
    return out


def by_url(us):
    """出典URL → それを使っているユニットのID一覧。"""
    m = {}
    for u in us:
        for s in u.sources:
            m.setdefault(s["url"], []).append(u.id)
    return m
