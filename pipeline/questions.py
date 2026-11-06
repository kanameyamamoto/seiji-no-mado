"""「質問主意書と答弁書」の一覧（data/questions.json）を、機械で作る（AIは使わない：ルールC-6・D-12）。

質問主意書：国会議員が、内閣に文書で質問するもの。内閣は閣議で決めた答弁書で答える（ふつう7日以内）。
出どころ：
- 衆議院「質問の一覧」（国会ごと）と、質問ごとの「経過情報」（提出日・内閣転送日・答弁書受領日・会派）
- 参議院「質問主意書・答弁書一覧」（国会ごと）と、質問ごとの「明細」（提出日・転送日・答弁書受領日）
載せる：config の questions.from_session（なければ第220回）から後の国会に出された質問主意書を、すべて。
明細は1回に questions.detail_limit 件まで読む。まだ明細を読めていないものは、一覧の答弁書へのリンクの有無で状況を決め、日付は空のまま（次の回に読む）。
件名・提出者・日付は、両院のページの表記のまま（日付は西暦にそろえる）。並びは、国会の新しい順 → 衆議院・参議院 → 提出番号の順。
状況は、答弁書受領日があれば「答弁あり」、撤回の日があれば「撤回」、それ以外は「答弁待ち」と、機械的に決める。
明細のページは、まだ答弁が出ていないものだけ読み直す（答弁が出たもの・撤回されたものは、それ以上変わらない）。
提出者は、今の国会議員の一覧（members.json）と名前で照らし合わせ、同じ院に同じ名前の議員がいればつなぐ。
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
PATH = os.path.join(DATA, "questions.json")
UA = "Mozilla/5.0 (compatible; seiji-no-mado-bot/1.0; +https://github.com/kanameyamamoto/seiji-no-mado)"
SHU = "https://www.shugiin.go.jp/internet/itdb_shitsumon.nsf/html/shitsumon/"
SAN = "https://www.sangiin.go.jp/japanese/joho1/kousei/syuisyo/"
CRITERIA = "第{frm}回国会から後の国会に、衆議院・参議院の議員が出した質問主意書を、すべて載せる。"
RULE = ("件名・提出者・日付は、衆議院「質問の一覧」と経過情報、参議院「質問主意書・答弁書一覧」と明細の表記のまま（日付は西暦にそろえる）。"
        "状況は、答弁書を受け取った日があれば「答弁あり」、撤回された日があれば「撤回」、それ以外は「答弁待ち」と機械的に決める（日付をまだ読めていないものは、答弁書が出ていれば「答弁あり」）。"
        "並びは、国会の新しい順、衆議院・参議院の順、提出番号の順。人は選ばない。")
SOURCES = [{"label": "衆議院 質問主意書・答弁書", "url": SHU + "menu_m.htm"},
           {"label": "参議院 質問主意書・答弁書", "url": SAN + "222/syuisyo.htm"}]
FINAL = ("答弁あり", "撤回")


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")


def fetch(url, enc, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode(enc, errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def text(s):
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("　", " ")
    return re.sub(r"\s+", " ", s).strip()


def wdate(s):
    """「令和 8年10月 5日」「令和8年10月5日」→「2026-10-05」。"""
    m = re.search(r"令和\s*(\d+|元)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s or "")
    if not m:
        return ""
    y = 1 if m.group(1) == "元" else int(m.group(1))
    return "%d-%02d-%02d" % (2018 + y, int(m.group(2)), int(m.group(3)))


def person(s):
    """「田村智子君外三名」→ 名前「田村智子」と、ほかの提出者「外三名」。"""
    t = text(s)
    m = re.match(r"(.+?)君?\s*(外\S+名)$", t)
    if m:
        return m.group(1).strip(), m.group(2)
    return re.sub(r"君$", "", t).strip(), ""


def key(name):
    return re.sub(r"[\s　]", "", name or "")


# ---------- 衆議院 ----------
def shu_list(ses):
    t = fetch(f"{SHU}kaiji{ses}_l.htm", "cp932")
    rows = []
    for tr in re.findall(r"<TR VALIGN=top>(.*?)</TR>", t, flags=re.S | re.I):
        cell = {h: v for h, v in re.findall(r'headers="SHITSUMON\.(\w+)"[^>]*>(.*?)</TD>', tr, flags=re.S | re.I)}
        if "NUMBER" not in cell:
            continue
        a = lambda k: (re.search(r'HREF="([^"]+)"', cell.get(k, ""), flags=re.I) or [None, None])[1]  # noqa: E731
        rows.append({"number": int(text(cell["NUMBER"])), "title": text(cell.get("KENMEI", "")), "submitter": person(cell.get("TEISHUTSUSHA", "")),
                     "status_text": text(cell.get("STATUS", "")), "detail": SHU + a("KLINK") if a("KLINK") else "",
                     "question_url": SHU + a("SLINK") if a("SLINK") else "", "answer_url": SHU + a("TLINK") if a("TLINK") else ""})
    return rows


def shu_detail(url):
    t = text(fetch(url, "cp932"))
    g = lambda label: (re.search(label + r"\s*(令和\s*\S+?年\s*\d+月\s*\d+日)?", t) or [None, None])[1]  # noqa: E731
    m = re.search(r"会派名\s*(\S+)", t)
    return {"kaiha": m.group(1) if m and "質問主意書" not in m.group(1) else "", "submitted": wdate(g("質問主意書提出年月日")),
            "forwarded": wdate(g("内閣転送年月日")), "answered": wdate(g("答弁書受領年月日")), "withdrawn": wdate(g("撤回年月日")),
            "delayed_until": wdate(g("答弁延期期限年月日"))}


# ---------- 参議院 ----------
def san_list(ses):
    base = f"{SAN}{ses}/"
    t = fetch(base + "syuisyo.htm", "utf-8")
    rows = []
    for m in re.finditer(r'<a href="(meisai/m\d+\.htm)"[^>]*>(.*?)</a>.*?<td headers="t\d+"[^>]*>\s*(\d+)\s*</td>.*?提出者</th>\s*<td[^>]*>(.*?)</td>(.*?)(?=<th scope="col"|</table>)',
                         t, flags=re.S):
        rest = m.group(5)
        q = re.search(r'href="(syuh/[^"]+)"', rest)
        ans = re.search(r'href="(touh/[^"]+)"', rest)
        rows.append({"number": int(m.group(3)), "title": text(m.group(2)), "submitter": person(m.group(4)), "status_text": "",
                     "detail": base + m.group(1), "question_url": base + q.group(1) if q else "", "answer_url": base + ans.group(1) if ans else ""})
    return rows


def san_detail(url):
    t = text(fetch(url, "utf-8"))
    g = lambda label: (re.search(label + r"\s*(令和\s*\S+?年\s*\d+月\s*\d+日)?", t) or [None, None])[1]  # noqa: E731
    return {"kaiha": "", "submitted": wdate(g("提出日")), "forwarded": wdate(g("転送日")), "answered": wdate(g("答弁書受領日")),
            "withdrawn": wdate(g("撤回日")), "delayed_until": wdate(g("答弁延期期限"))}


def status_of(d, r=None):
    """日付から決める。明細をまだ読めていないときは、一覧の答弁書へのリンク・経過の表記で決める。"""
    if d.get("answered"):
        return "答弁あり"
    if d.get("withdrawn"):
        return "撤回"
    if r and not d.get("submitted"):
        if r.get("answer_url"):
            return "答弁あり"
        if "撤回" in (r.get("status_text") or ""):
            return "撤回"
    return "答弁待ち"


def sessions():
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")).get("questions", {})
    frm = int(cfg.get("from_session", 220))
    have = [int(m.group(1)) for s in feed.get("sessions", []) for m in [re.match(r"第(\d+)回", s.get("name", ""))] if m]
    last = max(have) if have else frm
    return frm, list(range(last, frm - 1, -1))


def update(limit=None):
    """一覧を作り直す。変わったことを返す。limit：明細を読む数の上限（1回の実行で読みすぎない）。"""
    old = json.load(open(PATH, encoding="utf-8")) if os.path.exists(PATH) else {}
    prev = {x["id"]: x for x in old.get("items", [])}
    members = json.load(open(os.path.join(DATA, "members.json"), encoding="utf-8"))["members"]
    by_name = {}
    for m in members:
        by_name.setdefault((m["house"], key(m["name"])), []).append(m["id"])
    frm, sess = sessions()
    items, changes, read = [], [], 0
    for ses in sess:
        for house, lister, detailer, slug in (("衆議院", shu_list, shu_detail, "shu"), ("参議院", san_list, san_detail, "san")):
            try:
                rows = lister(ses)
            except Exception as e:  # noqa: BLE001
                print(f"[questions] {house} 第{ses}回の一覧を読めませんでした: {e}")
                rows = [x for x in prev.values() if x["house"] == house and x["session"] == f"第{ses}回国会"]
                items += rows
                continue
            for r in rows:
                qid = f"{slug}-{ses}-{r['number']}"
                p = prev.get(qid, {})
                d = {k: p.get(k, "") for k in ("kaiha", "submitted", "forwarded", "answered", "withdrawn", "delayed_until")}
                need = not p or p.get("status") not in FINAL or not p.get("submitted") or (r.get("status_text") and r["status_text"] != p.get("status_text"))
                if need and r["detail"] and (limit is None or read < limit):
                    try:
                        time.sleep(0.5)
                        d = detailer(r["detail"])
                        read += 1
                    except Exception as e:  # noqa: BLE001
                        print(f"[questions] 明細を読めませんでした {r['detail']}: {e}")
                name, others = r["submitter"]
                ids = by_name.get((house, key(name)), [])
                it = {"id": qid, "house": house, "session": f"第{ses}回国会", "number": r["number"], "title": r["title"], "submitter": name, **({"co_submitters": others} if others else {}),
                      **({"member": ids[0]} if len(ids) == 1 else {}), **({"kaiha": d["kaiha"]} if d.get("kaiha") else {}),
                      "status": status_of(d, r), **({"status_text": r["status_text"]} if r.get("status_text") else {}),
                      **{k: d[k] for k in ("submitted", "forwarded", "answered", "withdrawn", "delayed_until") if d.get(k)},
                      "question_url": r["question_url"], **({"answer_url": r["answer_url"]} if r.get("answer_url") and status_of(d, r) == "答弁あり" else {}),
                      "detail_url": r["detail"]}
                if not p:
                    changes.append(f"新しい質問主意書：{house} 第{ses}回 {r['number']}号 {r['title']}（{name}）")
                elif p.get("status") != it["status"]:
                    changes.append(f"{house} 第{ses}回 {r['number']}号：{p.get('status')} → {it['status']}")
                items.append(it)
        time.sleep(1)
    count = {h: sum(1 for x in items if x["house"] == h) for h in ("衆議院", "参議院")}
    out = {"criteria": CRITERIA.format(frm=frm), "rule": RULE, "sources": SOURCES, "count": count,
           "count_by_status": {s: sum(1 for x in items if x["status"] == s) for s in ("答弁あり", "答弁待ち", "撤回")},
           "checked": today(), "items": items}
    if {k: v for k, v in out.items() if k != "checked"} != {k: v for k, v in old.items() if k != "checked"}:
        json.dump(out, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changes


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    ch = update(lim)
    print("\n".join(ch[:30]) + (f"\n…ほか{len(ch) - 30}件" if len(ch) > 30 else "") if ch else "変わったものはありませんでした")
