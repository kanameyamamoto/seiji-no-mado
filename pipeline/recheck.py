"""公開中のページを、出典と照らして見直す（学ぶ・つながり・政党・選挙・メディア）。

見直すページの選び方（毎日、data/config.json の recheck.daily_limit 件まで）
  1. 見直し待ち（出典が変わった・開けない・会派の人数が変わった など。data/watch.json の due）
  2. 確認日から一定の日数（recheck.max_age_days）がたったページ（古い順）

1ページの流れ（ルールC-1〜C-4、T-2）
  1. 出典の今の本文を取ってくる（長いものは、ページに関係する段落だけ）
  2. 見直し担当AI（Anthropic）が「食い違いなし」か「直す案（場所・前・後・根拠）」を出す
  3. 直す案は、AIを使わないプログラムで確かめる
     - 前の文が、今のページの文と一字一句同じか
     - 後の文に新しく出てくる数字・日付が、出典の本文にあるか（C-2）
  4. 確かめる担当AI（OpenAI）が、根拠・必要性・評価語・偏り・レベルを確かめる
  5. 通らなければ指摘を渡して出し直し（最大3回）。だめなら直さずに人へ知らせる（C-3）
  6. 直したら、ページの updates（更新の記録）に「いつ・何を」を残し、確認日を今日にする
  7. 指示文・モデル名・チェック結果を data/logs/recheck/ に残す（C-4）

形を変えないと直せないとき（項目の追加・削除など）は、直さずに人へ知らせる。
"""
import argparse
import copy
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ai  # noqa: E402
import numcheck  # noqa: E402
import propose  # noqa: E402
import sources as SRC  # noqa: E402
import units as U  # noqa: E402
import watch_sources as W  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOGS = os.path.join(DATA, "logs", "recheck")
PROMPTS = os.path.join(ROOT, "prompts")
MAX_REWRITES = 3
SRC_LIMIT = 8          # 1ページで読む出典の数の上限
SRC_CHARS = 12000      # 出典1つあたりの文字数の上限
HOLD_DAYS = 14         # 人へ知らせたページは、この日数は自動で見直さない


def prompt_body(name):
    t = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    return t.split("---", 1)[1].strip() if "---" in t else t


def policy():
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    return cfg.get("recheck", {})


# ---------- 場所（path）で値を読む・書く ----------

def parse_path(path):
    out = []
    for name, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        out.append(int(idx) if idx else name)
    return out


def get_at(obj, path):
    for k in parse_path(path):
        obj = obj[k]
    return obj


def set_at(obj, path, value):
    ks = parse_path(path)
    for k in ks[:-1]:
        obj = obj[k]
    obj[ks[-1]] = value


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


# ---------- 選ぶ ----------

def pick(us, watch, limit, max_age, today):
    held = watch.get("held", {})
    t = datetime.date.fromisoformat(today)

    def held_now(uid):
        h = held.get(uid)
        return h and (t - datetime.date.fromisoformat(h["date"])).days < HOLD_DAYS

    byid = {u.id: u for u in us}
    due = sorted((min(r["date"] for r in rs), uid) for uid, rs in watch.get("due", {}).items() if uid in byid and not held_now(uid))
    chosen = [byid[uid] for _, uid in due]
    old = []
    for u in us:
        if u in chosen or held_now(u.id) or not u.sources:
            continue
        age = max_age.get(u.kind)
        if age and u.checked and (t - datetime.date.fromisoformat(u.checked[:10])).days >= age:
            old.append((u.checked, u.id, u))
    chosen += [u for _, _, u in sorted(old)]
    return [u for u in chosen if u.sources][:limit]


# ---------- 1ページを見直す ----------

def view_of(u):
    v = copy.deepcopy(u.get())
    for k in U.LOCKED.get(u.kind, []):
        v.pop(k, None)
    v.pop("updates", None)
    v.pop("checked", None)
    return v


def gather_sources(u, reasons):
    hints = list(strings(view_of(u)))
    want = [s for s in u.sources if any(s["label"] and s["label"] in r for r in reasons)]
    rest = [s for s in u.sources if s not in want]
    out = []
    for s in (want + rest)[:SRC_LIMIT]:
        try:
            kind, text = SRC.fetch_text(s["url"])
            out.append({"label": s["label"], "url": s["url"], "text": SRC.excerpt(text, hints, SRC_CHARS)})
        except Exception as e:  # noqa: BLE001
            out.append({"label": s["label"], "url": s["url"], "text": "", "error": f"開けない（{type(e).__name__}）"})
    return out


def sources_block(srcs):
    parts = []
    for i, s in enumerate(srcs, 1):
        body = s["text"] or f"（{s.get('error', '本文なし')}）"
        parts.append(f"### 出典{i}：{s['label']}\nURL：{s['url']}\n{body}")
    return "\n\n".join(parts)


def mech_check(view, changes, srcs):
    """AIを使わない確認。問題の一覧を返す。"""
    probs = []
    alltext = "\n".join(s["text"] for s in srcs)
    if not changes:
        probs.append("changes が空です")
    for c in changes:
        p = c.get("path", "")
        try:
            cur = get_at(view, p)
        except (KeyError, IndexError, TypeError, ValueError):
            probs.append(f"{p}：ページの中にこの場所がありません")
            continue
        if not isinstance(cur, str):
            probs.append(f"{p}：文字の場所ではありません（形を変える直しは needs_human にしてください）")
            continue
        if cur != c.get("before"):
            probs.append(f"{p}：before が今のページの文と一致しません")
        after = c.get("after")
        if not isinstance(after, str) or not after.strip():
            probs.append(f"{p}：after が空です")
            continue
        old_vals, old_dates = numcheck.values_in(cur), numcheck.dates_in(cur)
        for t, v, _ in numcheck.compare([after], alltext):
            if (t == "数字" and v in old_vals) or (t == "日付" and v in old_dates):
                continue
            probs.append(f"{p}：{t}「{v}」が出典の本文にありません")
    return probs


def recheck_unit(u, reasons, today):
    view = view_of(u)
    srcs = gather_sources(u, reasons)
    log = {"_srcs": srcs, "unit": u.id, "title": u.title, "date": today, "reasons": reasons,
           "writer_prompt": "prompts/recheck.md", "checker_prompt": "prompts/recheck_check.md",
           "sources": [{"label": s["label"], "url": s["url"], "chars": len(s["text"]), "error": s.get("error")} for s in srcs],
           "rounds": []}
    if not any(s["text"] for s in srcs):
        log["final"] = "出典がどれも開けないため、見直しできない"
        return "held", log, None
    wsys, csys = prompt_body("recheck.md"), prompt_body("recheck_check.md")
    base = ("【見直す理由】\n" + ("\n".join("- " + r for r in reasons) if reasons else "- 確認日から一定の日数がたった（定期の見直し）") +
            "\n\n【ページの中身（JSON）】\n" + json.dumps(view, ensure_ascii=False) + "\n\n【出典の今の本文（抜粋）】\n" + sources_block(srcs))
    feedback, prev = "", None
    for rnd in range(MAX_REWRITES + 1):
        user = base + (f"\n\n【前回の案】\n{json.dumps(prev, ensure_ascii=False)}\n\n【直してほしいこと】\n{feedback}" if feedback else "")
        text, wmodel = ai.write(wsys, user, max_tokens=8000)
        entry = {"round": rnd + 1, "writer_model": wmodel}
        try:
            ans = ai.extract_json(text)
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"JSONとして読めない: {e}"
            log["rounds"].append(entry)
            feedback = "指定の形のJSONだけを返してください。"
            continue
        entry["answer"] = ans
        prev = ans
        st = ans.get("status")
        if st == "ok":
            entry["result"] = "食い違いなし"
            log["rounds"].append(entry)
            log["final"] = "直すところなし（確認日を更新）"
            return "ok", log, None
        if st == "needs_human":
            entry["result"] = "形を変える直しが必要"
            log["rounds"].append(entry)
            log["final"] = "人へ知らせる：" + ans.get("note", "")
            return "needs_human", log, ans.get("note", "")
        if st != "update":
            entry["result"] = "status が ok・update・needs_human のどれでもない"
            log["rounds"].append(entry)
            feedback = "status は ok・update・needs_human のどれかにしてください。"
            continue
        changes = ans.get("changes") or []
        probs = mech_check(view, changes, srcs)
        entry["numcheck"] = probs
        if probs:
            entry["result"] = "機械の確認で不合格"
            log["rounds"].append(entry)
            feedback = "\n".join("- " + p for p in probs)
            continue
        used = {c.get("source") for c in changes}
        rel = [s for s in srcs if s["url"] in used] or srcs
        ctext, cmodel = ai.check(csys, "【直す案】\n" + json.dumps(changes, ensure_ascii=False) + "\n\n【出典の今の本文（抜粋）】\n" + sources_block(rel))
        entry["checker_model"] = cmodel
        try:
            verdict = ai.extract_json(ctext)
        except Exception as e:  # noqa: BLE001
            verdict = {"pass": False, "problems": [{"type": "確認できない", "detail": str(e)}]}
        entry["check"] = verdict
        if verdict.get("pass") and not verdict.get("problems"):
            entry["result"] = "合格"
            log["rounds"].append(entry)
            log["final"] = "直して公開"
            return "update", log, ans
        entry["result"] = "確かめる担当AIの指摘あり"
        log["rounds"].append(entry)
        feedback = "\n".join(f"- {p.get('path', '')}：{p.get('detail', '')}（{p.get('fix', '')}）" for p in verdict.get("problems", []))
    log["final"] = f"{MAX_REWRITES}回出し直してもチェックに通らないため、直さない（C-3）"
    return "held", log, None


def log_path(log):
    return "data/logs/recheck/" + re.sub(r"[^0-9A-Za-z_-]", "_", log["unit"]) + "-" + log["date"] + ".json"


def save_log(log):
    os.makedirs(LOGS, exist_ok=True)
    path = log_path(log)
    json.dump({k: v for k, v in log.items() if not k.startswith("_")}, open(os.path.join(ROOT, path), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def kaiha_text(watch):
    k = watch.get("kaiha") or {}
    lines = []
    for house, name in (("shu", "衆議院"), ("san", "参議院")):
        if k.get(house):
            lines.append(f"### {name}の会派別所属議員数（今の表）\n" + "\n".join(f"{n}：{c}人" for n, c in k[house].items()))
    return "\n\n".join(lines)


def fix_broken_sources(us, watch, today, limit):
    """リンク切れの出典の代わりを探して提案する（見つからなければ人へ知らせる）。"""
    done = 0
    for url, st in sorted(watch.get("sources", {}).items()):
        if done >= limit:
            break
        if not st.get("broken") or st.get("proposed") or st.get("alerted"):
            continue
        done += 1
        label = st.get("label", url)
        log = {"unit": "source-" + re.sub(r"[^0-9A-Za-z]", "_", url)[-60:], "date": today, "reasons": [f"出典「{label}」が3回続けて開けない"],
               "writer_prompt": "prompts/replace_source.md", "rounds": []}
        log["path"] = log_path(log)
        ok = False
        try:
            ok = propose.replacement(url, label, us, today, log)
        except Exception as e:  # noqa: BLE001
            log["rounds"].append({"result": f"エラー：{e}"})
        save_log(log)
        if ok:
            st["proposed"] = today
            print(f"[proposal] 代わりの出典を提案：{label}")
        else:
            st["alerted"] = today
            used = [u.title for u in us if any(s["url"] == url for s in u.sources)]
            watch.setdefault("alerts", []).append({
                "date": today, "title": f"リンク切れ：{label}",
                "body": f"出典 {url} が3回続けて開けず、代わりの出典も自動では見つかりませんでした。\n\n- 使っているページ：{'、'.join(used)}\n"
                        f"- 記録：`{log['path']}`\n\n代わりの出典に差し替えてください（docs/運用.md「人が見る必要があるもの」）。"})
            print(f"[alert] リンク切れ：{label}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--unit", help="このユニットだけ見直す（例：learn:what-is-politics）")
    ap.add_argument("--list", action="store_true", help="今日見直すページを表示するだけ")
    a = ap.parse_args()
    pol = policy()
    today = W.today()
    data = U.load()
    us = U.units(data)
    watch = W.load_watch()
    limit = a.limit if a.limit is not None else pol.get("daily_limit", 8)
    todo = [u for u in us if u.id == a.unit] if a.unit else pick(us, watch, limit, pol.get("max_age_days", {}), today)
    print(f"今日見直すページ：{len(todo)} 件（見直し待ち {len(watch.get('due', {}))} 件）")
    for u in todo:
        print(f"  - {u.id} {u.title}（確認日 {u.checked}）" + "".join(f"\n      理由：{r['reason']}" for r in watch.get("due", {}).get(u.id, [])))
    if a.list or not todo:
        return
    if not (ai.writer_available() and ai.checker_available()):
        print("APIキーとモデル名がないので、見直しはしません（見直し待ちはそのまま残ります）。")
        return
    fix_broken_sources(us, watch, today, limit=3)
    touched = set()
    for u in todo:
        reasons = [r["reason"] for r in watch.get("due", {}).get(u.id, [])]
        try:
            if any("会派" in r for r in reasons):
                # 会派の組み替え・人数の変化は、党のページの形（会派名など）に関わるので、直した版を作って提案する
                srcs = gather_sources(u, reasons)
                log = {"_srcs": srcs, "unit": u.id, "title": u.title, "date": today, "reasons": reasons, "rounds": []}
                result, extra = "needs_human", "会派の組み替え・人数の変化に合わせて、members（kaiha_shu・kaiha_san・note）と関係する文を直す。"
            else:
                result, log, extra = recheck_unit(u, reasons, today)
            if result in ("needs_human",):
                log["path"] = log_path(log)
                if propose.structural(u, reasons, extra, log.get("_srcs", []), kaiha_text(watch) if u.kind == "party" else "", today, log):
                    result = "proposed"
                    log["final"] = "直した版をプルリクエストで提案"
        except Exception as e:  # noqa: BLE001
            print(f"[error] {u.id}: {e}（次の回にもう一度）")
            continue
        path = save_log(log)
        if result == "ok":
            u.mark_checked(today)
            touched.add(u.file)
            watch.get("due", {}).pop(u.id, None)
            watch.get("held", {}).pop(u.id, None)
            print(f"[ok] {u.id}")
        elif result == "update":
            obj = u.get()
            for c in extra["changes"]:
                set_at(obj, c["path"], c["after"])
            obj.setdefault("updates", []).insert(0, {"date": today, "text": extra.get("summary", ""), "log": path})
            u.mark_checked(today)
            touched.add(u.file)
            watch.get("due", {}).pop(u.id, None)
            watch.get("held", {}).pop(u.id, None)
            print(f"[update] {u.id}：{extra.get('summary', '')}")
        elif result == "proposed":
            watch.setdefault("held", {})[u.id] = {"date": today, "reason": "直した版をプルリクエストで提案中", "log": path}
            watch.get("due", {}).pop(u.id, None)
            print(f"[proposal] {u.id}：直した版を提案")
        else:
            why = extra if result == "needs_human" else log["final"]
            watch.setdefault("held", {})[u.id] = {"date": today, "reason": why, "log": path}
            watch.setdefault("alerts", []).append({
                "date": today, "title": f"要確認：{u.title}（{u.id}）",
                "body": f"自動の見直しで直せませんでした。\n\n- 理由：{why}\n- 見直しのきっかけ：{'、'.join(reasons) or '定期の見直し'}\n"
                        f"- チェックの記録：`{path}`\n\n直し方は docs/運用.md の「人が見る必要があるもの」を見てください。"})
            print(f"[held] {u.id}：{why}")
    if touched:
        U.save(data, sorted(touched))
    W.save_watch(watch)


if __name__ == "__main__":
    main()
