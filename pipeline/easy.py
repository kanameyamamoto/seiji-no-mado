"""公開中の記事やページの「かんたん（es）」「やさしい（hs）」を、新しい決まり（ルール L-5）で書き直す。

かんたん＝小学生向け（すべての漢字にふりがな）、やさしい＝中学生向け（小学校で習わない漢字にふりがな）。

流れ（ルールC-1〜C-4・L-1〜L-5）
  1. 記事・ページの中の、4つのレベルの文（{es, hs, std, univ}）を、場所（path）ごとに集める。
  2. 書く担当（Claude）が、es・hs を書き直す（prompts/write_easy.md）。
  3. 機械で確かめる：すべての場所があるか、ふりがなのもれと漢字の範囲（furigana.py）、数字と日付が std・univ にあるか（numcheck）。
  4. 確かめる担当（Gemini）が確かめる（prompts/check_easy.md）：中身のずれ・名前と数字・評価・ふりがなの読み・読みやすさ。
     通らなければ指摘を渡して書き直す。最大3回（C-3）。
  5. 通ったら es・hs を置きかえ、「作成と更新の記録」に残す。記録は data/logs/easy/<種類>-<ID>.json（C-4）。
     3回で通らなければ置きかえず、14日後にもう一度。

順番：国会で決まったこと（新しい順）→ 学ぶ → つながり。1回に扱う数は data/config.json の easy.daily_limit まで。
使い方：python pipeline/easy.py [--limit N] [--id 種類:ID]
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ai  # noqa: E402
import furigana as F  # noqa: E402
import numcheck  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOGS = os.path.join(DATA, "logs", "easy")
PROMPTS = os.path.join(ROOT, "prompts")
MAX_REWRITES = 3
VERSION = "L-5"
JST = datetime.timezone(datetime.timedelta(hours=9))


def today():
    return datetime.datetime.now(JST).strftime("%Y-%m-%d")


def prompt_body(name):
    t = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    return t.split("---", 1)[1].strip() if "---" in t else t


def targets():
    """(種類, ID, 中身のオブジェクト, 保存するファイル名)"""
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    out = [("a", it["id"], it, "feed") for it in feed["items"]]
    docs = {"feed": feed}
    for name in ("learn", "links"):
        d = json.load(open(os.path.join(DATA, name + ".json"), encoding="utf-8"))
        docs[name] = d
        out += [(name, p["id"], p, name) for s in d.get("sections", []) for p in s.get("pages", [])]
    return out, docs


def request_text(obj, title):
    rows = []
    for path, t in F.walk_levels(obj):
        rows.append({"path": path, "std": t.get("std", ""), "univ": t.get("univ", ""), "es_now": t.get("es", ""), "hs_now": t.get("hs", "")})
    return rows, f"【題名】{title}\n\n【書き直す文】（path ごとに、std と同じ中身で es・hs を書く。es_now・hs_now はいまの文）\n" + json.dumps(rows, ensure_ascii=False, indent=1)


def problems(draft, rows):
    p = []
    for r in rows:
        d = draft.get(r["path"])
        if not isinstance(d, dict) or not d.get("es") or not d.get("hs"):
            p.append(f"{r['path']} の es と hs を両方書いてください")
            continue
        for lv in ("es", "hs"):
            p += [f"{r['path']}（{lv}）：{x}" for x in F.problems(d[lv], lv)]
            if "{{" in d[lv]:
                p.append(f"{r['path']}（{lv}）：{{{{ }}}} は使わないでください")
    extra = [k for k in draft if k not in {r["path"] for r in rows}]
    if extra:
        p.append("渡していない場所があります：" + "、".join(extra[:5]))
    return p


def run_one(kind, iid, obj, title, log_name):
    key = f"easy:{kind}:{iid}"
    ai.set_item(key)
    rows, user0 = request_text(obj, title)
    if not rows:
        return "none"
    truth = "\n".join(r["std"] + "\n" + r["univ"] for r in rows)
    wsys, csys = prompt_body("write_easy.md"), prompt_body("check_easy.md")
    log = {"key": key, "date": today(), "rules": ["C-1", "C-2", "C-3", "C-4", "L-1", "L-2", "L-5"],
           "writer_prompt": "prompts/write_easy.md", "checker_prompt": "prompts/check_easy.md", "rounds": []}
    feedback, draft = "", None
    for rnd in range(MAX_REWRITES + 1):
        user = user0
        if feedback:
            user += "\n\n【前回の答え】\n" + json.dumps(draft, ensure_ascii=False) + "\n\n【直してほしいこと】\n" + feedback
        text, wmodel = ai.write(wsys, user, max_tokens=16000)
        entry = {"round": rnd + 1, "writer_model": wmodel}
        try:
            draft = ai.extract_json(text)
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"JSONとして読めない: {e}"
            log["rounds"].append(entry)
            feedback = "指定の形のJSONだけを返してください。"
            continue
        entry["draft"] = draft
        probs = problems(draft, rows)
        miss = [] if probs else numcheck.compare([F.plain(v[lv]) for v in draft.values() for lv in ("es", "hs")], truth)
        entry["machine"] = probs[:50]
        entry["numcheck"] = [{"type": t, "value": v, "in": s} for t, v, s in miss]
        if probs or miss:
            entry["result"] = "機械の照合で不合格"
            log["rounds"].append(entry)
            feedback = "\n".join(probs[:60] + [f"{t}「{v}」が std・univ にありません（{s}…）" for t, v, s in miss])
            continue
        show = [{"path": r["path"], "std": r["std"], "es": draft[r["path"]]["es"], "hs": draft[r["path"]]["hs"]} for r in rows]
        ctext, cmodel = ai.check(csys, json.dumps(show, ensure_ascii=False, indent=1))
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
        log["final"] = "置きかえない（14日後にもう一度）"
        save_log(log_name, log)
        obj["easy_wait_until"] = (datetime.date.fromisoformat(today()) + datetime.timedelta(days=14)).isoformat()
        ai.finish(key)
        return "held"
    for r in rows:
        t = F.at(obj, r["path"])
        t["es"], t["hs"] = draft[r["path"]]["es"], draft[r["path"]]["hs"]
    path = f"data/logs/easy/{log_name}.json"
    obj["easy"] = {"rule": VERSION, "date": today(), "log": path}
    obj.pop("easy_wait_until", None)
    obj.setdefault("updates", []).insert(0, {"date": today(), "text": "「かんたん」を小学生向け、「やさしい」を中学生向けに書き直し、ふりがなをつけました（AIが書き、別の会社のAIとプログラムで確かめました。中身は「ふつう」と同じです）。", "log": path})
    log["final"] = "置きかえる"
    save_log(log_name, log)
    ai.finish(key)
    return "passed"


def save_log(name, log):
    os.makedirs(LOGS, exist_ok=True)
    json.dump(log, open(os.path.join(LOGS, f"{name}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--id", help="種類:ID（例 a:r8-law-075、learn:what-is-politics）")
    ap.add_argument("--count", action="store_true", help="残りの数を数えるだけ")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    limit = a.limit or (cfg.get("easy") or {}).get("daily_limit", 30)
    items, docs = targets()
    t = today()
    # 新しい決まりで書かれた記事（ふりがなの機械のチェックに通るもの）は、書き直さない
    todo = [(k, i, o, f) for k, i, o, f in items if (o.get("easy") or {}).get("rule") != VERSION and (o.get("easy_wait_until") or "") <= t
            and (not a.id or f"{k}:{i}" == a.id) and F.check_item(o)]
    if a.count:
        print(f"書き直しが残っている：{len(todo)}件（全部で {len(items)}件）")
        return
    if not (ai.writer_available() and ai.checker_available()):
        print("確かめる担当のAPIキー（GEMINI_API_KEY）がないので、書き直しません。")
        return
    done = {"passed": 0, "held": 0, "writer": 0, "none": 0}
    changed = set()
    for n, (kind, iid, obj, fname) in enumerate(todo):
        if n >= limit:
            break
        try:
            r = run_one(kind, iid, obj, obj.get("title", iid), f"{kind}-{iid}")
            done[r] += 1
            if r in ("passed", "held"):
                changed.add(fname)
        except ai.NeedWriter:
            done["writer"] += 1
        except ai.RetryLater as e:
            print(f"[easy] 確かめる担当の上限など：{e}。残りは次の回に。")
            break
    for fname in changed:
        json.dump(docs[fname], open(os.path.join(DATA, fname + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"書き直した {done['passed']}件・通らず {done['held']}件・Claudeの答え待ち {done['writer']}件（残り {len(todo) - done['passed'] - done['held']}件）")


if __name__ == "__main__":
    main()
