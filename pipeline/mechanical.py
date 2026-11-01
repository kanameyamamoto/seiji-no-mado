"""AIを使わずに、元の資料から機械で更新する部分（毎日）。

1. 会派別の所属議員数（衆議院・参議院）→ parties.json の members
2. 国会の会期一覧（衆議院）→ feed.json の sessions、data/config.json の sessions（新しい国会）
3. 成立した法律の公布日・法律番号（参議院 議案情報）→ feed.json の各記事
4. 国会議員の一覧・参議院議員一人ひとりの賛否・知事の一覧（people.py）→ members.json・member_votes.json・governors.json

機械で決められないこと（どの党か決められない会派の組み替えなど）は、
data/watch.json の due（見直し待ち）か、alerts（人への知らせ。report.py が Issue にする）に入れる。
"""
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import sangiin as S  # noqa: E402
import sources as SRC  # noqa: E402
import people  # noqa: E402
import watch_sources as W  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SHU_KAIHA = "https://www.shugiin.go.jp/internet/itdb_annai.nsf/html/statics/shiryo/kaiha_m.htm"
SHU_KAIKI = "https://www.shugiin.go.jp/internet/itdb_annai.nsf/html/statics/shiryo/kaiki.htm"
SAN_KAIHA = "https://www.sangiin.go.jp/japanese/joho1/kousei/giin/{s}/giinsu.htm"
LOG = []


def say(msg):
    LOG.append(msg)
    print(msg)


def jload(name):
    return json.load(open(os.path.join(DATA, name), encoding="utf-8"))


def jsave(name, d):
    json.dump(d, open(os.path.join(DATA, name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def page(url):
    raw, cs, _ = SRC.get(url)
    return SRC.decode(raw, cs)


def cells(row):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)]


def wdate_any(s):
    m = re.search(r"(令和|平成)\s*(\d+|元)年\s*(\d+)月\s*(\d+)日", s or "")
    if not m:
        return ""
    y = 1 if m.group(2) == "元" else int(m.group(2))
    y += 2018 if m.group(1) == "令和" else 1988
    return "%d-%02d-%02d" % (y, int(m.group(3)), int(m.group(4)))


# ---------- 1. 会派別の所属議員数 ----------

def parse_kaiha(src):
    """会派の表を {会派名: 人数} にする。人数は、その行の数字のうち最も大きいもの（内訳・女性の数より大きい）。"""
    text = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", src)))
    m = re.search(r"((令和|平成)(\d+|元)年\d+月\d+日)現在", text)
    out, asof = {}, wdate_any(m.group(1)) if m else ""
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", src):
        c = cells(row)
        if len(c) < 2 or not c[0] or c[0] in ("会派名", "会派", "計", "合計", "欠員", "総数", "総定数", "定数", "現員"):
            continue
        nums = [int(x) for cc in c[1:] for x in re.findall(r"(?<![\d（(])(\d+)(?![日年月])", cc.split("（")[0].split("(")[0])]
        if nums:
            out[c[0]] = max(nums)
    return out, asof


def strip_kaiha(name):
    return re.sub(r"（.*?）|\(.*?\)", "", name or "").strip()


def update_members(watch):
    P = jload("parties.json")
    cfg = jload("config.json")
    changed = False
    houses = [("shu", SHU_KAIHA, "衆議院「会派名及び会派別所属議員数」"),
              ("san", SAN_KAIHA.format(s=max(cfg["sessions"])), f"参議院「会派別所属議員数一覧」第{max(cfg['sessions'])}回国会")]
    for house, url, label in houses:
        try:
            table, asof = parse_kaiha(page(url))
        except Exception as e:  # noqa: BLE001
            say(f"[skip] {label} を読めませんでした: {e}")
            continue
        if len(table) < 5:
            say(f"[skip] {label} の表を読み取れませんでした（{len(table)} 行）")
            continue
        prev = watch.setdefault("kaiha", {}).get(house, {})
        for p in P["national"]:
            m = p.get("members") or {}
            k = m.get(f"kaiha_{house}", "")
            if "統一会派" in k:
                base = strip_kaiha(k)
                if base in table and prev.get(base) not in (None, table[base]):
                    W.add_due(watch, f"party:{p['id']}", f"{'衆' if house == 'shu' else '参'}議院の会派「{base}」の人数が変わった（{prev.get(base)}→{table[base]}）", W.today())
                continue
            name = strip_kaiha(k)
            if not name or name.startswith("—"):
                continue
            if name not in table:
                W.add_due(watch, f"party:{p['id']}", f"{'衆' if house == 'shu' else '参'}議院の会派一覧に「{name}」が見つからない", W.today())
                continue
            key = "shu" if house == "shu" else "san"
            if m.get(key) != table[name] or (asof and m.get("as_of" if house == "shu" else "as_of_san") != asof):
                if m.get(key) != table[name]:
                    say(f"[members] {p['name']} {key}: {m.get(key)} → {table[name]}")
                m[key] = table[name]
                if asof:
                    m["as_of" if house == "shu" else "as_of_san"] = asof
                    jp = "令和%d年%d月%d日" % (int(asof[:4]) - 2018, int(asof[5:7]), int(asof[8:]))
                    m["source" if house == "shu" else "source_san"] = {"label": f"{label}（{jp}現在）", "url": url}
                changed = True
        new_names = sorted(set(table) - set(prev)) if prev else []
        gone = sorted(set(prev) - set(table)) if prev else []
        hn = "衆" if house == "shu" else "参"
        unmatched = []
        # 会派がなくなった・できた → 関係する党のページを見直し待ちにする（recheck.py が直した版を作って提案する）
        for g in gone:
            hit = [p for p in P["national"] if strip_kaiha((p.get("members") or {}).get(f"kaiha_{house}", "")) == g]
            for p in hit:
                W.add_due(watch, f"party:{p['id']}", f"{hn}議院の会派「{g}」がなくなった", W.today())
            if not hit:
                unmatched.append(f"なくなった会派：{g}")
        for g in new_names:
            hit = [p for p in P["national"] if p["name"] in g or (p.get("short") and p["short"] in g)]
            for p in hit:
                W.add_due(watch, f"party:{p['id']}", f"{hn}議院に会派「{g}」（{table[g]}人）ができた", W.today())
            if not hit:
                unmatched.append(f"新しい会派：{g}（{table[g]}人）")
        if unmatched:
            watch.setdefault("alerts", []).append({
                "date": W.today(), "title": f"{hn}議院の会派が変わりました（どの党か自動では決められないもの）",
                "body": "\n".join(unmatched) + f"\n出典：{url}\n各党の members.kaiha_{house} を確かめてください。"})
        watch["kaiha"][house] = table
    if changed:
        jsave("parties.json", P)
    return changed


# ---------- 2. 国会の会期 ----------

def update_sessions(watch):
    feed = jload("feed.json")
    cfg = jload("config.json")
    try:
        rows = [cells(r) for r in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page(SHU_KAIKI))]
    except Exception as e:  # noqa: BLE001
        say(f"[skip] 国会の会期一覧を読めませんでした: {e}")
        return False
    rows = [r for r in rows if len(r) >= 4 and re.match(r"第\d+回", r[0])]
    have = {s["name"]: s for s in feed["sessions"]}
    changed = False
    for r in rows:
        mm = re.match(r"第(\d+)回\s*（(.+?)）", r[0])
        if not mm:
            continue
        no, kind = mm.groups()
        name = f"第{no}回国会"
        start = wdate_any(r[1])
        end = wdate_any(r[2])
        dissolved = "解散" in r[2]
        if name in have:
            s = have[name]
            if end and s.get("end") != end:
                s["end"] = end
                s["note"] = (f"{end[:4]}年{int(end[5:7])}月{int(end[8:])}日に衆議院解散。会期{r[3]}。" if dissolved else
                             f"会期{r[3]}（当初{r[4]}" + (f"、延長{r[5]}" if len(r) > 5 and r[5] else "") + "）。")
                say(f"[session] {name} の会期が終わった：{end}")
                changed = True
            continue
        if int(no) <= max(int(re.search(r"\d+", n).group()) for n in have):
            continue  # 古い国会は対象外
        if start:
            feed["sessions"].append({"name": name, "kind": kind, "start": start, "end": end,
                                     "note": f"会期{r[3]}。", "source": {"label": "衆議院 国会会期一覧", "url": SHU_KAIKI}})
            if int(no) not in cfg["sessions"]:
                cfg["sessions"].append(int(no))
            say(f"[session] 新しい国会：{name}（{kind}、{start} 召集）")
            changed = True
    # 閉会から60日たった国会は、毎日の確認から外す（成立したものはすべて載せ終えているため）
    today = datetime.date.fromisoformat(W.today())
    for s in feed["sessions"]:
        no = int(re.search(r"\d+", s["name"]).group())
        if s.get("end") and no in cfg["sessions"] and (today - datetime.date.fromisoformat(s["end"])).days > 60:
            cfg["sessions"].remove(no)
            say(f"[session] {s['name']} は閉会から60日たったので、毎日の確認から外した")
            changed = True
    if changed:
        feed["sessions"].sort(key=lambda s: int(re.search(r"\d+", s["name"]).group()))
        jsave("feed.json", feed)
        jsave("config.json", cfg)
    return changed


# ---------- 3. 公布日・法律番号の後追い ----------

def update_promulgation():
    feed = jload("feed.json")
    changed = False
    for it in feed["items"]:
        if it.get("kind") != "法律が成立" or (it.get("law_no") and it.get("promulgated")):
            continue
        url = (it.get("process") or {}).get("source", {}).get("url", "")
        if "sangiin.go.jp" not in url:
            continue
        try:
            r = S.parse_bill(url)
        except Exception as e:  # noqa: BLE001
            say(f"[skip] {it['id']} を読めませんでした: {e}")
            continue
        pd = S.wdate(r.get("その他の/公布年月日", ""))
        no = r.get("その他の/法律番号", "")
        hit = False
        if pd and not it.get("promulgated"):
            it["promulgated"] = pd
            hit = True
        if no.isdigit() and not it.get("law_no"):
            it["law_no"] = f"令和{int(pd[:4]) - 2018 if pd else int(it['date'][:4]) - 2018}年法律第{no}号"
            hit = True
        if hit:
            changed = True
            say(f"[promulgated] {it['id']} {it['title']}：公布 {it.get('promulgated')}、{it.get('law_no')}")
    if changed:
        jsave("feed.json", feed)
    return changed


def main():
    watch = W.load_watch()
    update_members(watch)
    update_sessions(watch)
    update_promulgation()
    for c in people.update():  # 国会議員・参議院議員の賛否・知事の一覧
        say("[people] " + c)
    W.save_watch(watch)
    if not LOG:
        print("機械で更新するものはありませんでした")


if __name__ == "__main__":
    main()
