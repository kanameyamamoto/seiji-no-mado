"""公開中の「国会で決まったこと」の記事に、「なぜ出されたか・だれに関係するか・委員会での審査・各会派が述べた理由」を足す。

流れ（ルールC-1〜C-4、V-1〜V-3）
  1. 資料を機械で集める（deliberation.py：法律案の「理由」、国会会議録の委員長報告と討論）。
     会議録がまだ公開されていないときは、次の回に回す。
  2. 書く担当（Claude）が書く（prompts/write_law_more.md）。
  3. 機械で確かめる：形、数字と日付（numcheck）、会派名が資料の討論にあるか、会派ごとの分量の差（V-1：2割以内）。
  4. 確かめる担当（Gemini）が確かめる（prompts/check_law_more.md）。通らなければ指摘を渡して書き直す。最大3回（C-3）。
  5. 通ったら記事の more に入れ、「作成と更新の記録」に残す。チェックの記録は data/logs/more/<記事ID>.json（C-4）。
     3回で通らなければ足さずに、14日後にもう一度試す。

1回に扱う記事の数は data/config.json の more.daily_limit まで。新しい記事から順に。
使い方：python pipeline/enrich.py [--limit N] [--id 記事ID]
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ai  # noqa: E402
import deliberation as DL  # noqa: E402
import numcheck  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOGS = os.path.join(DATA, "logs", "more")
PROMPTS = os.path.join(ROOT, "prompts")
LEVELS = ("es", "hs", "std", "univ")
MAX_REWRITES = 3
JST = datetime.timezone(datetime.timedelta(hours=9))


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def prompt_body(name):
    t = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    return t.split("---", 1)[1].strip() if "---" in t else t


def summary_of(it):
    """記事にすでに書いてある内容（くわしく〈univ〉の文）。"""
    lines = [f"題名：{it.get('title', '')}", f"正式名：{it.get('formal', '')}", f"種類：{it.get('kind', '')}", f"番号：{it.get('number', '')}"]
    if it.get("kind") == "法律案が否決":
        where = "衆議院" if (it.get("process") or {}).get("shugiin", {}).get("result") == "否決" else "参議院"
        lines.append(f"結果：この法律案は{where}の本会議で否決され、成立していない。")
    lines.append("要約：" + (it.get("lead") or {}).get("univ", ""))
    for f in it.get("facts", []):
        lines.append("・" + (f.get("text") or {}).get("univ", ""))
    return "\n".join(lines)


def material_groups(mats):
    g = set()
    for d in mats.get("debates", []):
        for t in (d.get("talks") or []) + (d.get("committee_talks") or []):
            if t.get("group"):
                g.add(t["group"])
    return g


def has_any(mats):
    return bool(mats.get("reason")) or any(d.get("report") or d.get("talks") or d.get("committee_talks") for d in mats.get("debates", []))


def texts_of(d):
    out = []
    for k in ("reason", "who", "committee"):
        if d.get(k):
            out += [d[k].get(l, "") for l in LEVELS]
    for v in d.get("views") or []:
        out += [v.get("text", {}).get(l, "") for l in LEVELS]
    return out


def shape_problems(d, mats):
    p = []
    for k in ("reason", "who", "committee"):
        v = d.get(k)
        if v is None:
            continue
        if not isinstance(v, dict) or any(not v.get(l) for l in LEVELS):
            p.append(f"{k} は4つのレベル（es・hs・std・univ）すべてを書くか、null にしてください")
    views = d.get("views")
    if not isinstance(views, list):
        p.append("views はリストにしてください")
        return p
    known = material_groups(mats)
    for i, v in enumerate(views):
        if v.get("side") not in ("賛成", "反対"):
            p.append(f"views[{i}].side は「賛成」か「反対」にしてください")
        if v.get("group") not in known:
            p.append(f"views[{i}].group「{v.get('group')}」は、資料の討論の会派名（{'、'.join(sorted(known)) or 'なし'}）にありません。資料の表記どおりにしてください")
        if any(not (v.get("text") or {}).get(l) for l in LEVELS):
            p.append(f"views[{i}].text は4つのレベルすべてを書いてください")
    lens = [len((v.get("text") or {}).get("std", "")) for v in views]
    if len(lens) >= 2 and min(lens) and (max(lens) - min(lens)) / max(lens) > 0.2:
        p.append(f"views の std の文字数の差が2割を超えています（{min(lens)}〜{max(lens)}字）。同じくらいの分量にしてください（V-1）")
    if views and d.get("no_debate"):
        p.append("views があるのに no_debate が true です")
    return p


def build_more(d, mats, it, log_path):
    rs = (mats.get("reason") or {}).get("source")
    reports = [(x["house"], x["date"], x["report"]) for x in mats.get("debates", []) if x.get("report")]
    rep_src = [{"label": f"国会会議録（{h} 本会議 {dt}・{r['speaker']}委員長の報告）", "url": r["url"]} for h, dt, r in reports if r.get("url")]
    fact_src = []
    for f in it.get("facts", []):
        for s in f.get("sources", []):
            if s not in fact_src:
                fact_src.append(s)
    more = {"date": today(), "log": log_path, "no_debate": bool(d.get("no_debate"))}
    if d.get("reason"):
        more["reason"] = {"text": {l: d["reason"][l] for l in LEVELS}, "sources": [rs] if rs else rep_src[:1]}
    if d.get("who"):
        more["who"] = {"text": {l: d["who"][l] for l in LEVELS}, "sources": ([rs] if rs else []) + fact_src[:1]}
    if d.get("committee"):
        more["committee"] = {"text": {l: d["committee"][l] for l in LEVELS}, "sources": rep_src}
    views = []
    for v in sorted(d.get("views") or [], key=lambda x: 0 if x["side"] == "賛成" else 1):  # 賛成→反対（V-2）
        srcs = []
        for x in mats.get("debates", []):
            for t in (x.get("talks") or []) + (x.get("committee_talks") or []):
                if t.get("group") == v["group"] and t.get("url"):
                    srcs.append({"label": f"国会会議録（{t['meeting']}・{t['speaker']}議員の討論）", "url": t["url"]})
        views.append({"side": v["side"], "group": v["group"], "where": v.get("where", ""), "text": {l: v["text"][l] for l in LEVELS}, "sources": srcs})
    more["views"] = views
    return more


def run_one(it, mats, log_all):
    key = f"more:{it['id']}"
    ai.set_item(key)
    src = DL.as_text(mats)
    user0 = "【記事にすでに書いてある内容】\n" + summary_of(it) + "\n\n" + src
    wsys, csys = prompt_body("write_law_more.md"), prompt_body("check_law_more.md")
    log = {"key": key, "date": today(), "rules": ["C-1", "C-2", "C-3", "C-4", "V-1", "V-2", "V-3"],
           "writer_prompt": "prompts/write_law_more.md", "checker_prompt": "prompts/check_law_more.md",
           "materials": f"work/sources/more-{it['id']}.json", "rounds": []}
    feedback, draft = "", None
    for rnd in range(MAX_REWRITES + 1):
        user = user0
        if feedback:
            user += "\n\n【前回の答え】\n" + json.dumps(draft, ensure_ascii=False) + "\n\n【直してほしいこと】\n" + feedback
        text, wmodel = ai.write(wsys, user)
        entry = {"round": rnd + 1, "writer_model": wmodel}
        try:
            draft = ai.extract_json(text)
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"JSONとして読めない: {e}"
            log["rounds"].append(entry)
            feedback = "指定の形のJSONだけを返してください。"
            continue
        entry["draft"] = draft
        probs = shape_problems(draft, mats)
        miss = numcheck.compare(texts_of(draft), user0)
        entry["numcheck"] = [{"type": t, "value": v, "in": s} for t, v, s in miss]
        if probs or miss:
            entry["result"] = "機械の照合で不合格"
            log["rounds"].append(entry)
            feedback = "\n".join(probs + [f"{t}「{v}」が元の資料にありません（{s}…）" for t, v, s in miss])
            continue
        ctext, cmodel = ai.check(csys, "【元の資料】\n" + user0 + "\n\n【足した説明】\n" + json.dumps(draft, ensure_ascii=False))
        entry["checker_model"] = cmodel
        try:
            verdict = ai.extract_json(ctext)
        except Exception as e:  # noqa: BLE001
            verdict = {"pass": False, "problems": [{"type": "確認できない", "detail": str(e)}]}
        entry["check"] = verdict
        if verdict.get("pass") and not verdict.get("problems"):
            entry["result"] = "合格"
            log["rounds"].append(entry)
            break
        entry["result"] = "確かめる担当AIの指摘あり"
        log["rounds"].append(entry)
        feedback = "\n".join(f"- {p.get('where', '')}：{p.get('detail', '')}（{p.get('fix', '')}）" for p in verdict.get("problems", []))
    else:
        log["final"] = "足さない（14日後にもう一度）"
        save_log(it["id"], log)
        it["more_wait_until"] = (datetime.date.fromisoformat(today()) + datetime.timedelta(days=14)).isoformat()
        ai.finish(key)
        print(f"[held] {it['id']}: チェック不合格")
        return "held"
    log_path = f"data/logs/more/{it['id']}.json"
    it["more"] = build_more(draft, mats, it, log_path)
    it.pop("more_wait_until", None)
    it.setdefault("updates", []).insert(0, {"date": today(), "text": "「なぜ出されたか」「だれに関係するか」「国会での議論」を足しました（国会会議録と法律案の理由をもとに、AIが書き、別の会社のAIとプログラムで確かめました）。", "log": log_path})
    log["final"] = "足す"
    save_log(it["id"], log)
    ai.finish(key)
    try:
        os.remove(os.path.join(DL.WORK, f"more-{it['id']}.json"))
    except OSError:
        pass
    print(f"[more] {it['id']} {it['title']}")
    return "passed"


def save_log(iid, log):
    os.makedirs(LOGS, exist_ok=True)
    json.dump(log, open(os.path.join(LOGS, f"{iid}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--id")
    ap.add_argument("--gather-only", action="store_true", help="資料を集めるだけ（AIは使わない）")
    ap.add_argument("--requests", action="store_true", help="Claudeへの依頼を作るだけ（確かめる担当のキーがなくても動く）")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    limit = a.limit or (cfg.get("more") or {}).get("daily_limit", 10)
    if not (a.gather_only or a.requests) and not (ai.writer_available() and ai.checker_available()):
        print("確かめる担当のAPIキー（GEMINI_API_KEY）がないので、記事に説明を足しません。")
        return
    fp = os.path.join(DATA, "feed.json")
    feed = json.load(open(fp, encoding="utf-8"))
    t = today()
    todo = [it for it in feed["items"] if not it.get("more") and (it.get("more_wait_until") or "") <= t and (not a.id or it["id"] == a.id)]
    done = {"passed": 0, "held": 0, "wait": 0, "nodata": 0, "writer": 0}
    changed = False
    n = 0
    for it in todo:
        if n >= limit:
            break
        try:
            mats = DL.gather(it, summary_of(it))
        except Exception as e:  # noqa: BLE001
            print(f"[more] 資料を集められませんでした {it['id']}: {e}")
            continue
        if mats is None:
            done["wait"] += 1  # 会議録がまだ
            continue
        if not has_any(mats):
            it["more_wait_until"] = (datetime.date.fromisoformat(t) + datetime.timedelta(days=30)).isoformat()
            done["nodata"] += 1
            changed = True
            continue
        n += 1
        if a.gather_only:
            continue
        try:
            r = run_one(it, mats, None)
            done[r] += 1
            changed = True
        except ai.NeedWriter:
            done["writer"] += 1
        except ai.RetryLater as e:
            print(f"[more] 確かめる担当の上限など：{e}。残りは次の回に。")
            break
    if changed:
        json.dump(feed, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"説明を足した {done['passed']}件・通らず {done['held']}件・Claudeの答え待ち {done['writer']}件・会議録待ち {done['wait']}件・資料なし {done['nodata']}件")


if __name__ == "__main__":
    main()
