"""国会議員と知事の一覧を、公式の資料から機械で作る（AIは使わない。ルールC-6）。

- 衆議院議員：衆議院「議員一覧（五十音順）」1〜10ページ ＋「会派名及び会派別所属議員数」（会派の略称→正式名）
- 参議院議員：参議院「議員一覧」（第N回国会）
- 参議院議員一人ひとりの賛否：参議院「本会議投票結果」（押しボタン式。feed.json の各記事の votes.source）
- 知事：全国知事会「現職知事一覧」（PDF）

書き出すファイル
- data/members.json       … 議員（全員を同じ項目で）
- data/member_votes.json  … 参議院議員の賛否（記事の順に1文字ずつ。Y=賛成 N=反対 -=投票なし）
- data/governors.json     … 知事（47都道府県）
"""
import datetime
import hashlib
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import sources as SRC  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SHU_LIST = "https://www.shugiin.go.jp/internet/itdb_annai.nsf/html/statics/syu/{n}giin.htm"
SHU_KAIHA = "https://www.shugiin.go.jp/internet/itdb_annai.nsf/html/statics/shiryo/kaiha_m.htm"
SAN_LIST = "https://www.sangiin.go.jp/japanese/joho1/kousei/giin/{s}/giin.htm"
NGA = "https://www.nga.gr.jp/outline/"
JST = datetime.timezone(datetime.timedelta(hours=9))
PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
         "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
         "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
         "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def page(url, cache=None):
    if cache:
        p = os.path.join(cache, url.rstrip("/").split("/")[-1])
        if os.path.exists(p):
            return open(p, encoding="utf-8", errors="replace").read()
    raw, cs, _ = SRC.get(url, retries=3)
    return SRC.decode(raw, cs)


def cells(row):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)]


def rows(src):
    return [(cells(r), r) for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", src)]


def norm_name(s):
    return re.sub(r"[\s　]+", "", s or "").rstrip("君")


def asof_of(src):
    t = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", src)))
    m = re.search(r"(令和|平成)(\d+|元)年(\d+)月(\d+)日現在", t)
    if not m:
        return ""
    y = (1 if m.group(2) == "元" else int(m.group(2))) + (2018 if m.group(1) == "令和" else 1988)
    return "%d-%02d-%02d" % (y, int(m.group(3)), int(m.group(4)))


def jp_date(s):
    """「R 9. 4.22」「H31. 4.23」「令和10年7月25日」→ YYYY-MM-DD"""
    s = (s or "").replace(" ", "")
    m = re.match(r"([RHS])(\d+)\.(\d+)\.(\d+)", s)
    if m:
        base = {"R": 2018, "H": 1988, "S": 1925}[m.group(1)]
        return "%d-%02d-%02d" % (base + int(m.group(2)), int(m.group(3)), int(m.group(4)))
    m = re.match(r"(令和|平成)(\d+|元)年(\d+)月(\d+)日", s)
    if m:
        y = (1 if m.group(2) == "元" else int(m.group(2))) + (2018 if m.group(1) == "令和" else 1988)
        return "%d-%02d-%02d" % (y, int(m.group(3)), int(m.group(4)))
    return ""


def party_ids(parties, house, kaiha):
    """会派の正式名 → その会派を名のる党（統一会派なら複数）。"""
    out = []
    for p in parties["national"]:
        k = re.sub(r"（.*?）|\(.*?\)", "", ((p.get("members") or {}).get(f"kaiha_{house}") or "")).strip()
        if k and k == kaiha:
            out.append(p["id"])
    return out


# ---------- 衆議院 ----------

def shu_members(parties, cache=None):
    abbr = {"無": "無所属"}
    for c, _ in rows(page(SHU_KAIHA, cache)):
        if len(c) >= 3 and c[1] and c[0] not in ("会派名",):
            abbr[c[1]] = c[0]
    out, asof = [], ""
    for n in range(1, 11):
        src = page(SHU_LIST.format(n=n), cache)
        asof = asof or asof_of(src)
        for c, _ in rows(src):
            if len(c) == 5 and re.search(r"[ぁ-ん]", c[1]) and c[0] != "氏名":
                name = re.sub(r"君$", "", c[0]).strip()
                kaiha = abbr.get(c[2], c[2])
                dist = c[3]
                prop = dist.startswith("（比）")
                out.append({
                    "id": "shu-" + hashlib.sha1((norm_name(name) + c[1]).encode()).hexdigest()[:8],
                    "house": "衆議院", "name": name, "yomi": c[1], "kaiha": kaiha, "kaiha_abbr": c[2],
                    "parties": party_ids(parties, "shu", kaiha),
                    "district": dist.replace("（比）", "") + ("ブロック" if prop else "区") if dist else "",
                    "elected_by": "比例代表" if prop else "小選挙区",
                    "terms": c[4],
                })
    return out, asof


# ---------- 参議院 ----------

def san_vote_groups(url, cache=None):
    """投票結果のページ → [(会派名, 議員名, 賛否)]"""
    src = page(url, cache)
    out = []
    for kaiha, body in re.findall(r'<h4 class="party">(.*?)\(\s*\d+名\)</h4>(.*?)(?=<h4 class="party">|</div>\s*</div>\s*<!--|$)', src, flags=re.S):
        for pros, cons, name in re.findall(r'<span class="pros">(.*?)</span><span class="cons">(.*?)</span><span class="names">(.*?)</span>', body, flags=re.S):
            v = "Y" if "賛成" in pros else ("N" if "反対" in cons else "-")
            out.append((html.unescape(re.sub(r"<[^>]+>", "", kaiha)).strip(), norm_name(html.unescape(name)), v))
    return out


def san_members(parties, session, latest_vote_url, cache=None):
    src = page(SAN_LIST.format(s=session), cache)
    asof = asof_of(src)
    raw = []
    for c, r in rows(src):
        link = re.search(r'href="\.\./profile/(\d+)\.htm"', r)
        if not link:
            continue
        # 先頭に「あ」「か」などの見出しの列がある行もあるので、2列目以降で「よみ」の列を探す
        i = next((k for k in range(1, len(c)) if len(c[k]) > 1 and re.fullmatch(r"[ぁ-んー\s]+", c[k])), None)
        if i is None or i + 3 >= len(c):
            continue
        name, yomi, abbr, dist, term_end = c[i - 1], c[i], c[i + 1], c[i + 2], c[i + 3]
        raw.append((link.group(1), name, yomi, abbr, dist, term_end))
    # 会派の略称 → 正式名：いちばん新しい投票結果のページで、同じ議員がどの会派の欄にいるかで決める
    full_by_name = {}
    if latest_vote_url:
        for kaiha, nm, _ in san_vote_groups(latest_vote_url, cache):
            full_by_name[nm] = kaiha
    votes = {}
    for _, name, _, abbr, _, _ in raw:
        k = full_by_name.get(norm_name(re.sub(r"\[.*?\]", "", name)))
        if k:
            votes.setdefault(abbr, {}).setdefault(k, 0)
            votes[abbr][k] += 1
    abbr_map = {a: max(d, key=d.get) for a, d in votes.items()}
    try:
        current = [c[0] for c, _ in rows(page(SAN_LIST.format(s=session).replace("giin.htm", "giinsu.htm"), cache))
                   if len(c) >= 2 and re.search(r"\d", c[1] or "") and c[0] not in ("会派名", "合計", "総定数", "欠員")]
    except Exception:  # noqa: BLE001
        current = []

    def subseq(a, b):
        it = iter(b)
        return all(ch in it for ch in a)
    for a in {x[3] for x in raw}:
        if current and abbr_map.get(a) not in current:
            cand = [k for k in current if subseq(a, k)]
            if len(cand) == 1:
                abbr_map[a] = cand[0]
            elif a == "無所属" and "各派に属しない議員" in current:
                abbr_map[a] = "各派に属しない議員"
    out = []
    for pid, name, yomi, abbr, dist, term_end in raw:
        kaiha = abbr_map.get(abbr, abbr)
        alt = re.search(r"\[(.+?)\]", name)
        name = re.sub(r"\[.*?\]", "", name).strip()  # 「石井 めぐみ[根本 めぐみ]」→ 通称を名前に、[ ] の中は本名として別に持つ
        out.append({
            "id": "san-" + pid, "house": "参議院", "name": name, "yomi": yomi, "kaiha": kaiha, "kaiha_abbr": abbr,
            **({"name_registered": alt.group(1)} if alt else {}),
            "parties": party_ids(parties, "san", kaiha),
            "district": dist if dist == "比例" else dist + "選挙区", "elected_by": "比例代表" if dist == "比例" else "選挙区",
            "term_end": jp_date(term_end),
            "url": f"https://www.sangiin.go.jp/japanese/joho1/kousei/giin/profile/{pid}.htm",
        })
    return out, asof


def san_votes(feed, members, old, cache=None):
    """参議院議員一人ひとりの賛否。記事の順に1文字ずつ（Y=賛成 N=反対 -=投票なし・その時は議員でない）。"""
    by_name = {norm_name(m["name"]): m["id"] for m in members if m["house"] == "参議院"}
    known = {it: i for i, it in enumerate(old.get("items", []))}
    items = [it for it in feed["items"] if (it.get("votes") or {}).get("chamber") == "参議院" and (it["votes"].get("source") or {}).get("url")]
    rec = {mid: {} for mid in by_name.values()}
    for mid, s in old.get("votes", {}).items():
        if mid in rec:
            for iid, i in known.items():
                if i < len(s):
                    rec[mid][iid] = s[i]
    fetched = 0
    for it in items:
        if it["id"] in known:
            continue
        try:
            groups = san_vote_groups(it["votes"]["source"]["url"], cache)
        except Exception as e:  # noqa: BLE001
            print(f"[skip] 投票結果を読めませんでした {it['id']}: {e}")
            continue
        fetched += 1
        for _, nm, v in groups:
            if nm in by_name:
                rec[by_name[nm]][it["id"]] = v
        known[it["id"]] = -1
    order = [it["id"] for it in items if it["id"] in known]
    return {"note": "参議院の本会議投票結果（押しボタン式）の、議員一人ひとりの賛否。items の順に1文字ずつ：Y＝賛成、N＝反対、-＝投票の記録なし（欠席・その時点で議員でない など）。",
            "items": order, "votes": {mid: "".join(r.get(i, "-") for i in order) for mid, r in rec.items()}}, fetched


# ---------- 知事 ----------

def governors(cache=None):
    src = page(NGA, cache)
    m = re.search(r'href="([^"]+\.pdf)"[^>]*>\s*現職知事一覧（([^）]*)）', src)
    if not m:
        raise RuntimeError("全国知事会のページに「現職知事一覧」が見つかりません")
    url = "https://www.nga.gr.jp" + m.group(1) if m.group(1).startswith("/") else m.group(1)
    raw, _, _ = SRC.get(url)
    text = SRC.pdf_text(raw)
    asof = jp_date(m.group(2).replace("現在", "").replace("R", "R").replace(".", ".")) or ""
    t = re.sub(r"[ \t]+", " ", text)
    pos = sorted((t.find(" " + p + " "), p) for p in PREFS if t.find(" " + p + " ") >= 0)
    out = []
    for k, (i, pref) in enumerate(pos):
        seg = t[i + len(pref) + 1: pos[k + 1][0] if k + 1 < len(pos) else len(t)]
        seg = re.sub(r"\n[^\n]{0,3}\n", "\n", seg)
        name = re.match(r"\s*(\S+)\s+(\S+)", seg)
        yomi = re.findall(r"[ぁ-ん]{2,}", seg)
        dates = re.findall(r"[RHS]\s?\d+\.\s?\d+\.\s?\d+", seg)
        rest = re.sub(r"[RHS]\s?\d+\.\s?\d+\.\s?\d+", " ", seg)  # 日付を除いて、残った1〜2けたの数が「在任期数」
        terms = re.search(r"(?<![\d.])(\d{1,2})(?![\d.])", rest)
        if not name or len(dates) < 4:
            continue
        out.append({"pref": pref, "name": name.group(1) + " " + name.group(2), "yomi": " ".join(yomi[:2]),
                    "terms": int(terms.group(1)) if terms else None, "election": jp_date(dates[1]),
                    "term_end": jp_date(dates[2]), "first_took_office": jp_date(dates[3])})
    out.sort(key=lambda g: PREFS.index(g["pref"]))
    return out, url, asof


# ---------- まとめ ----------

def update(cache=None):
    parties = json.load(open(os.path.join(DATA, "parties.json"), encoding="utf-8"))
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    changed = []
    mp = os.path.join(DATA, "members.json")
    old = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    try:
        shu, shu_asof = shu_members(parties, cache)
        session = max(cfg["sessions"]) if cfg.get("sessions") else 222
        latest = next((it["votes"]["source"]["url"] for it in feed["items"]
                       if (it.get("votes") or {}).get("chamber") == "参議院" and (it["votes"].get("source") or {}).get("url")), "")
        san, san_asof = san_members(parties, session, latest, cache)
        if len(shu) < 400 or len(san) < 200:
            raise RuntimeError(f"議員の数が少なすぎます（衆{len(shu)}・参{len(san)}）")
        new = {"note": "国会議員の一覧（全員を同じ項目で）。衆議院・参議院の公式の議員一覧から機械で作る（pipeline/people.py）。評価は書かない。",
               "checked": old.get("checked", today()),
               "shu": {"as_of": shu_asof, "count": len(shu), "source": {"label": "衆議院「議員一覧（五十音順）」", "url": SHU_LIST.format(n=1)}},
               "san": {"as_of": san_asof, "count": len(san), "source": {"label": f"参議院「議員一覧」第{session}回国会", "url": SAN_LIST.format(s=session)}},
               "members": shu + san}
        cmp_old = {k: v for k, v in old.items() if k != "checked"}
        cmp_new = {k: v for k, v in new.items() if k != "checked"}
        if cmp_old != cmp_new:
            new["checked"] = today()
            json.dump(new, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            changed.append(f"議員の一覧（衆{len(shu)}人・参{len(san)}人）")
        vp = os.path.join(DATA, "member_votes.json")
        oldv = json.load(open(vp, encoding="utf-8")) if os.path.exists(vp) else {}
        nv, fetched = san_votes(feed, shu + san, oldv, cache)
        if nv != oldv:
            json.dump(nv, open(vp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            changed.append(f"参議院議員の賛否（投票結果 {fetched} 件を新しく読んだ）")
    except Exception as e:  # noqa: BLE001
        print(f"[skip] 議員の一覧を作れませんでした: {e}")
    gp = os.path.join(DATA, "governors.json")
    try:
        gov, url, asof = governors(cache)
        if len(gov) != 47:
            raise RuntimeError(f"知事を {len(gov)} 人しか読み取れませんでした")
        oldg = json.load(open(gp, encoding="utf-8")) if os.path.exists(gp) else {}
        newg = {"note": "都道府県知事の一覧。全国知事会「現職知事一覧」から機械で作る（pipeline/people.py）。",
                "checked": oldg.get("checked", today()), "as_of": asof,
                "source": {"label": "全国知事会「現職知事一覧」", "url": url}, "governors": gov}
        if {k: v for k, v in oldg.items() if k != "checked"} != {k: v for k, v in newg.items() if k != "checked"}:
            newg["checked"] = today()
            json.dump(newg, open(gp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            changed.append("知事の一覧")
    except Exception as e:  # noqa: BLE001
        print(f"[skip] 知事の一覧を作れませんでした: {e}")
    for c in changed:
        print("[people] " + c + " を更新")
    return changed


if __name__ == "__main__":
    update(sys.argv[1] if len(sys.argv) > 1 else None)
