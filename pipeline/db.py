"""検索API・MCP（ルール S-1〜S-3）のための「データの箱」（Cloudflare D1）を作る。

data/*.json（サイトと同じデータ）から、名寄せしたもの（entities）と、そのつながり（edges）を作り、
D1 にそのまま流し込める SQL を書き出す。AI は使わない。中身はサイト・静的API と同じで、足したり選んだりしない。

  entities … type（下の TYPES）・id・名前・よみ・要約（ふつうのレベル）・本文（検索用）・サイトのURL・データ（JSON）
  edges    … つながり（例：議員 → 所属会派、法律 → 担当の省庁、会派 → 法律への賛否）
  fts      … 検索用の索引（3文字ずつに区切る trigram。2文字以下の検索は名前・本文の部分一致で探す）
  meta     … 作った日・元データのハッシュ・件数

置き換えは「新しい表を作ってから入れ替える」ので、更新の途中でも API は前の中身を返し続ける。

使い方：python pipeline/db.py build/d1.sql   （ハッシュは build/d1.hash に書く）
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LEVELS = {"es", "hs", "std", "univ"}
# 種類と、並べる順（検索の結果で、同じくらい合っているときに使う。ルール D：機械で決まる順）
TYPES = ["law", "bill", "question", "party", "kaiha", "member", "governor", "org", "page", "term"]
TYPE_NAME = {"law": "国会で決まったこと（法律・予算・条約・否決された法律案）", "bill": "審議中・採決されなかった法律案", "question": "質問主意書と答弁書", "party": "政党",
             "kaiha": "会派（国会の中のグループ）", "member": "国会議員", "governor": "知事", "org": "政府・国会の組織",
             "page": "解説・データのページ（学ぶ・つながり・メディア）", "term": "ことば"}
MAX_BODY = 6000


def load(name):
    return json.load(open(os.path.join(DATA, f"{name}.json"), encoding="utf-8"))


def plain(s):
    return re.sub(r"\{\{([^}]+)\}\}", r"\1", str(s or ""))


def std(v):
    """読むレベルつきの文なら「ふつう」を、ただの文ならそのまま。"""
    if isinstance(v, dict) and v and set(v) <= LEVELS:
        return plain(v.get("std") or next(iter(v.values()), ""))
    return plain(v) if isinstance(v, str) else ""


SKIP_KEYS = {"url", "id", "date", "checked", "log", "type", "api", "source", "sources", "updates", "corrections", "icon", "style"}


def texts(obj, out=None):
    """検索用に、文を集める（URL・ID・日付などは除く）。"""
    out = [] if out is None else out
    if isinstance(obj, dict):
        if obj and set(obj) <= LEVELS:
            out.append(std(obj))
            return out
        for k, v in obj.items():
            if k not in SKIP_KEYS:
                texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            texts(v, out)
    elif isinstance(obj, str) and not re.match(r"^(https?:|\d{4}-\d{2})", obj):
        out.append(plain(obj))
    return out


def body_of(*objs):
    s = " ".join(t for o in objs for t in texts(o) if t)
    s = re.sub(r"\s+", " ", s)
    return s[:MAX_BODY]


class Box:
    def __init__(self, base):
        self.base = base
        self.ent, self.edges, self.keys = [], [], set()

    def add(self, typ, id_, name, kana="", summary="", body="", url="", api="", checked="", data=None):
        k = (typ, id_)
        if k in self.keys:
            return
        self.keys.add(k)
        self.ent.append({"type": typ, "id": id_, "name": name, "kana": kana or "", "summary": summary or "", "body": body or "",
                         "url": url or "", "api": api or "", "checked": checked or "", "data": data or {}})

    def link(self, st, si, rel, dt, di, note=""):
        self.edges.append((st, si, rel, dt, di, note or ""))


def kaiha_id(house, name):
    return f"{house}:{name}"


def build():
    cfg = load("config")
    base = cfg.get("site_url", "https://seijinomado.com/").rstrip("/") + "/"
    api = base + "api/v1/"
    B = Box(base)
    feed, bills, parties, members, mv, gov = load("feed"), load("bills"), load("parties"), load("members"), load("member_votes"), load("governors")
    learn, links, media, gl, acc = load("learn"), load("links"), load("media"), load("glossary"), load("accounts")

    # 組織（政府・国会）：公式サイト・SNS（D-11）
    org_by_name, org_by_page = {}, {}
    for g in acc.get("groups", []):
        if g["id"] == "party":
            continue
        for x in g["items"]:
            org_by_name[x["name"]] = x["id"]
            if x.get("page"):
                org_by_page[x["page"]] = x["id"]
            B.add("org", x["id"], x["name"], summary=g["name"], url=(base + f"links/{x['page']}/") if x.get("page") else base + "accounts/",
                  checked=x.get("checked"), data={"group": g["name"], "official_site": x.get("url"), "sns": x.get("sns", {}), "sns_counts": x.get("counts", {}),
                                                  **({"note": x["note"]} if x.get("note") else {})})
    B.add("org", "cabinet", "内閣", summary="政府", url=base + "learn/cabinet/",
          data={"group": "政府", "note": "内閣が国会に出した法律案（閣法）・予算・条約は、この「内閣」から「提出」でつながる。"})
    org_alias = {"内閣府本府": "cabinet-office", "警察庁": "npa"}

    def org_id(name):
        return org_alias.get(name) or org_by_name.get(name)

    # 政党
    acc_party = {x.get("party"): x for g in acc.get("groups", []) if g["id"] == "party" for x in g["items"]}
    kaiha_names = set()
    for i, p in enumerate(parties["national"] + parties["others"]):
        pid = p["id"] if i < len(parties["national"]) else f"o{i - len(parties['national'])}"
        pr = p.get("profile") or {}
        a = acc_party.get(pid) or {}
        m = p.get("members") or {}
        B.add("party", pid, p["name"], kana=p.get("kana", ""), summary=std(pr.get("catch")) or std(p.get("summary")) or p.get("detail", ""),
              body=body_of(pr.get("ideas"), pr.get("policies"), pr.get("history"), p.get("detail")),
              url=base + f"party/{pid}/", api=api + f"parties/{pid}.json", checked=p.get("checked") or parties.get("checked"),
              data={"short": p.get("short"), "founded": p.get("founded"), "leader": p.get("leader"), "kind": p.get("kind") or "国政政党",
                    "members": {k: m.get(k) for k in ("shu", "san", "as_of", "as_of_san") if m.get(k) not in (None, "")},
                    "official_site": p.get("url") or a.get("url"), "sns": a.get("sns", {}), "sns_counts": a.get("counts", {})})
        for house, key in (("衆議院", "kaiha_shu"), ("参議院", "kaiha_san")):
            if m.get(key):
                B.link("party", pid, "会派", "kaiha", kaiha_id(house, m[key]))
                kaiha_names.add((house, m[key]))

    # 国会議員
    vote_items = mv.get("items", [])
    feed_by_id = {it["id"]: it for it in feed["items"]}
    for x in members["members"]:
        kaiha_names.add((x["house"], x["kaiha"]))
        v = mv.get("votes", {}).get(x["id"])
        votes = []
        if v:
            for lid, c in zip(vote_items, v):
                if c in "YN" and lid in feed_by_id:
                    votes.append({"law": lid, "title": feed_by_id[lid]["title"], "vote": "賛成" if c == "Y" else "反対"})
        B.add("member", x["id"], x["name"], kana=x.get("yomi", ""), summary=f"{x['house']}議員・{x['kaiha']}・{x.get('district', '')}",
              url=base + f"member/{x['id']}/", api=api + f"members/{x['id']}.json", checked=members.get("checked"),
              data={"house": x["house"], "kaiha": x["kaiha"], "district": x.get("district"), "elected_by": x.get("elected_by"), "terms": x.get("terms"),
                    **({"votes": votes, "votes_note": "参議院の本会議の押しボタン式投票。記録のない採決（欠席など）は入れていない。"} if votes else {})})
        B.link("member", x["id"], "所属会派", "kaiha", kaiha_id(x["house"], x["kaiha"]))
        for pid in x.get("parties") or []:
            B.link("member", x["id"], "所属政党", "party", pid)

    # 国会で決まったこと
    for it in feed["items"]:
        mo = it.get("more") or {}
        v = it.get("votes") or {}
        B.add("law", it["id"], it["title"], summary=std(it.get("lead")),
              body=body_of(it.get("formal"), it.get("facts"), mo.get("reason"), mo.get("who"), mo.get("committee"), [x.get("text") for x in mo.get("views") or []]),
              url=base + f"a/{it['id']}/", api=api + f"laws/{it['id']}.json", checked=it.get("checked") or it.get("date"),
              data={"kind": it.get("kind"), "date": it.get("date"), "session": it.get("session"), "formal": it.get("formal"), "number": it.get("number"),
                    "law_no": it.get("law_no"), "promulgated": it.get("promulgated"), "submitter": it.get("submitter"), "submitter_groups": it.get("submitter_groups"),
                    "ministries": it.get("ministries"), "process": it.get("process"), "votes": v or None})
        for mname in it.get("ministries") or []:
            oid = org_id(mname)
            if oid:
                B.link("law", it["id"], "担当の省庁", "org", oid)
        if it.get("submitter") == "内閣":
            B.link("law", it["id"], "提出", "org", "cabinet")
        ch = v.get("chamber")
        for side, rel in (("for", "賛成"), ("against", "反対"), ("split", "会派の中で分かれた")):
            for gname in v.get(side) or []:
                if ch and gname != "各派に属しない議員":
                    kaiha_names.add((ch, gname))
                    B.link("kaiha", kaiha_id(ch, gname), rel, "law", it["id"], f"{ch} 本会議 {v.get('date', '')}")

    # 審議中・採決されなかった法律案
    for b in bills.get("items", []):
        B.add("bill", b["id"], b["title"], summary=f"{b['session']} {b['number']}・{b.get('status') or b.get('result', '')}",
              url=base + "bills/", api=api + "bills.json", checked=bills.get("checked"),
              data={k: b.get(k) for k in ("session", "category", "number", "status", "result", "submitter", "proposer", "groups", "submitted", "shugiin", "sangiin", "source", "article") if b.get(k)})
        for gname in b.get("groups") or []:
            kaiha_names.add(("衆議院", gname))
            B.link("bill", b["id"], "提出会派", "kaiha", kaiha_id("衆議院", gname))
        if b.get("submitter") == "内閣":
            B.link("bill", b["id"], "提出", "org", "cabinet")

    # 質問主意書と答弁書（D-12）
    qs = load("questions") if os.path.exists(os.path.join(DATA, "questions.json")) else {}
    for x in qs.get("items", []):
        B.add("question", x["id"], x["title"], summary=f"{x['house']} {x['session']} {x['number']}号・提出 {x['submitter']}・{x['status']}",
              url=base + "questions/", api=api + "questions.json", checked=qs.get("checked"),
              data={k: x.get(k) for k in ("house", "session", "number", "submitter", "kaiha", "status", "submitted", "forwarded", "answered", "withdrawn",
                                          "question_url", "answer_url", "detail_url") if x.get(k)})
        if x.get("member") and ("member", x["member"]) in B.keys:
            B.link("member", x["member"], "質問主意書を提出", "question", x["id"])
        if x.get("answered"):
            B.link("question", x["id"], "答弁", "org", "cabinet", x["answered"])

    # 会派
    for house, name in sorted(kaiha_names):
        B.add("kaiha", kaiha_id(house, name), name, summary=f"{house}の会派")

    # 知事
    for g in gov["governors"]:
        B.add("governor", g["pref"], g["name"], kana=g.get("yomi", ""), summary=f"{g['pref']}知事・{g.get('terms')}期目", url=base + "governors/",
              api=api + "governors.json", checked=gov.get("checked"), data={k: g.get(k) for k in ("pref", "terms", "election", "term_end", "first_took_office")})

    # 解説・データのページ
    for name, d in (("learn", learn), ("links", links), ("media", media)):
        for s in d.get("sections", []):
            for p in s.get("pages", []):
                pid = f"{name}/{p['id']}"
                B.add("page", pid, p["title"], summary=std(p.get("summary")), body=body_of(p.get("blocks")),
                      url=base + f"{name}/{p['id']}/", api=(api + f"{name}/{p['id']}.json") if name != "media" else "", checked=p.get("checked"),
                      data={"tab": {"learn": "学ぶ", "links": "つながり", "media": "メディア"}[name], "section": s.get("name")})
                if name == "links" and p["id"] in org_by_page:
                    B.link("org", org_by_page[p["id"]], "くわしいページ", "page", pid)

    # ことば
    for k, v in gl.items():
        t = std(v.get("text")) if isinstance(v, dict) else std(v)
        B.add("term", k, k, summary=t[:300], body=t, url=base + "terms/", api=api + "glossary.json")

    return B


def q(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


SCHEMA = """CREATE TABLE {p}entities (type TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, kana TEXT, summary TEXT, body TEXT, url TEXT, api TEXT, checked TEXT, data TEXT, ord INTEGER, PRIMARY KEY (type, id));
CREATE TABLE {p}edges (src_type TEXT, src_id TEXT, rel TEXT, dst_type TEXT, dst_id TEXT, note TEXT);
CREATE INDEX edges_src_{v} ON {p}edges (src_type, src_id);
CREATE INDEX edges_dst_{v} ON {p}edges (dst_type, dst_id);
CREATE VIRTUAL TABLE {p}fts USING fts5(type UNINDEXED, id UNINDEXED, name, kana, summary, body, tokenize='trigram');
CREATE TABLE {p}meta (key TEXT PRIMARY KEY, value TEXT);"""
TABLES = ["entities", "edges", "fts", "meta"]


def to_sql(B, digest, today):
    out = [f"DROP TABLE IF EXISTS new_{t};" for t in TABLES] + [f"DROP INDEX IF EXISTS edges_{k}_{digest};" for k in ("src", "dst")]
    # 索引の名前は版ごとに変える（表の名前を入れ替えても、索引の名前は変わらないため）
    out += SCHEMA.format(p="new_", v=digest).split("\n")
    for i, e in enumerate(B.ent):
        out.append("INSERT INTO new_entities VALUES (" + ",".join(q(x) for x in (e["type"], e["id"], e["name"], e["kana"], e["summary"], e["body"], e["url"], e["api"], e["checked"],
                                                                                 json.dumps(e["data"], ensure_ascii=False, separators=(",", ":")), i)) + ");")
        out.append("INSERT INTO new_fts (type, id, name, kana, summary, body) VALUES (" + ",".join(q(x) for x in (e["type"], e["id"], e["name"], e["kana"], e["summary"], e["body"])) + ");")
    for ed in B.edges:
        out.append("INSERT INTO new_edges VALUES (" + ",".join(q(x) for x in ed) + ");")
    counts = {t: sum(1 for e in B.ent if e["type"] == t) for t in TYPES}
    for k, v in (("hash", digest), ("built", today), ("counts", json.dumps(counts, ensure_ascii=False)), ("edges", str(len(B.edges))),
                 ("types", json.dumps(TYPE_NAME, ensure_ascii=False)), ("site", B.base)):
        out.append(f"INSERT INTO new_meta VALUES ({q(k)},{q(v)});")
    # 入れ替え（ここまでは前の表のまま）
    out += [f"DROP TABLE IF EXISTS {t};" for t in TABLES]
    out += [f"ALTER TABLE new_{t} RENAME TO {t};" for t in TABLES]
    return "\n".join(out) + "\n"


def main():
    import datetime
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "d1.sql")
    B = build()
    ids = {(e["type"], e["id"]) for e in B.ent}
    bad = [ed for ed in B.edges if (ed[0], ed[1]) not in ids or (ed[3], ed[4]) not in ids]
    if bad:
        raise SystemExit(f"つながりの先が見つかりません：{bad[:5]}")
    body = json.dumps([B.ent, B.edges], ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256((body + open(__file__, encoding="utf-8").read()).encode()).hexdigest()[:16]
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(to_sql(B, digest, today))
    open(os.path.splitext(path)[0] + ".hash", "w").write(digest + "\n")
    c = {t: sum(1 for e in B.ent if e["type"] == t) for t in TYPES}
    print(f"データの箱：{len(B.ent)} 件（{c}）・つながり {len(B.edges)} 本・ハッシュ {digest}")


if __name__ == "__main__":
    main()
