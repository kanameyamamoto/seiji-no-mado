"""自動では公開しない「直した版」を作り、プルリクエストで人に提案するための準備。

1. 形を変える直し（項目の追加・削除、会派の組み替えなど）
   見直しで「形を変えないと直せない」となったページについて、AIが直した版を作る。
   機械の確認（読むレベルのそろい方・新しい数字が出典にあるか）と、別の会社のAIの確認を通ったら、提案にする。
2. リンク切れ
   開けなくなった出典の代わりを、AIがウェブ検索で探す。プログラムが実際に開けるかを確かめ、
   別の会社のAIが「元の文の根拠になるか」を確かめたら、提案にする。

提案は _proposals/ に書き出し、open_proposals.py がプルリクエストにする。
"""
import copy
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import ai  # noqa: E402
import numcheck  # noqa: E402
import sources as SRC  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPOSALS = os.path.join(ROOT, "_proposals")
PROMPTS = os.path.join(ROOT, "prompts")
LEVELS = {"es", "hs", "std", "univ"}


def prompt_body(name):
    t = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    return t.split("---", 1)[1].strip() if "---" in t else t


def flatten(o, path="", out=None):
    out = {} if out is None else out
    if isinstance(o, dict):
        for k, v in o.items():
            flatten(v, f"{path}.{k}" if path else k, out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            flatten(v, f"{path}[{i}]", out)
    else:
        out[path] = o
    return out


def diff(old, new):
    a, b = flatten(old), flatten(new)
    out = []
    for p in sorted(set(a) | set(b)):
        if a.get(p) != b.get(p):
            out.append({"path": p, "before": a.get(p), "after": b.get(p)})
    return out


def level_problems(o, where="unit"):
    probs = []
    if isinstance(o, dict):
        ks = set(o)
        if ks & LEVELS and ks <= LEVELS | {"note"}:
            for l in sorted(LEVELS - ks):
                probs.append(f"{where}：読むレベル {l} がありません")
        for k, v in o.items():
            probs += level_problems(v, f"{where}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            probs += level_problems(v, f"{where}[{i}]")
    return probs


def save(name, proposal):
    os.makedirs(PROPOSALS, exist_ok=True)
    name = re.sub(r"[^0-9A-Za-z_-]", "_", name)
    json.dump(proposal, open(os.path.join(PROPOSALS, name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------- 1. 形を変える直し ----------

def structural(u, reasons, note, srcs, extra_text, today, log):
    """直した版を作って確かめる。通れば提案を保存して True。"""
    cur = copy.deepcopy(u.get())
    cur.pop("updates", None)
    cur.pop("checked", None)
    src_block = "\n\n".join(f"### 出典：{s['label']}\nURL：{s['url']}\n{s['text'] or '（開けない）'}" for s in srcs)
    if extra_text:
        src_block += "\n\n" + extra_text
    alltext = "\n".join(s["text"] for s in srcs) + "\n" + (extra_text or "")
    old_strings = "\n".join(str(v) for v in flatten(cur).values())
    user = ("【直す必要があること】\n" + (note or "") + "\n\n【見直しのきっかけ】\n" + "\n".join("- " + r for r in reasons or ["定期の見直し"]) +
            "\n\n【ページの中身（JSON）】\n" + json.dumps(cur, ensure_ascii=False) + "\n\n【出典の今の本文（抜粋）】\n" + src_block)
    feedback = ""
    for rnd in range(2):
        text, wmodel = ai.write(prompt_body("propose.md"), user + (f"\n\n【前回の案の問題】\n{feedback}" if feedback else ""), max_tokens=16000)
        entry = {"round": rnd + 1, "mode": "形を変える直し", "writer_model": wmodel}
        log["rounds"].append(entry)
        try:
            ans = ai.extract_json(text)
        except Exception as e:  # noqa: BLE001
            entry["result"] = f"JSONとして読めない: {e}"
            feedback = "指定の形のJSONだけを返してください。"
            continue
        new = ans.get("unit")
        if not isinstance(new, dict):
            entry["result"] = "直せない：" + str(ans.get("note", ""))
            return False
        probs = level_problems(new)
        for k in ("id", "url", "platform_url"):
            if cur.get(k) != new.get(k):
                probs.append(f"{k} は変えないでください")
        changes = diff(cur, new)
        if not changes:
            probs.append("変更がありません")
        for c in changes:
            if isinstance(c["after"], str):
                for t, v, _ in numcheck.compare([c["after"]], alltext):
                    if t == "数字" and v in numcheck.values_in(old_strings):
                        continue
                    probs.append(f"{c['path']}：{t}「{v}」が出典にありません")
        entry["changes"] = changes
        entry["numcheck"] = probs
        if probs:
            entry["result"] = "機械の確認で不合格"
            feedback = "\n".join("- " + p for p in probs[:40])
            continue
        ctext, cmodel = ai.check(prompt_body("recheck_check.md"), "【直す案】\n" + json.dumps(changes, ensure_ascii=False) + "\n\n【出典の今の本文（抜粋）】\n" + src_block)
        entry["checker_model"] = cmodel
        try:
            verdict = ai.extract_json(ctext)
        except Exception as e:  # noqa: BLE001
            verdict = {"pass": False, "problems": [{"detail": str(e)}]}
        entry["check"] = verdict
        if verdict.get("pass") and not verdict.get("problems"):
            entry["result"] = "合格（提案にする）"
            save(f"unit-{u.id}-{today}", {"kind": "unit", "unit": u.id, "title": u.title, "date": today,
                                          "summary": ans.get("summary", ""), "reasons": reasons, "note": note,
                                          "changes": changes, "new": new, "log": log.get("path", "")})
            return True
        entry["result"] = "確かめる担当AIの指摘あり"
        feedback = "\n".join(f"- {p.get('path', '')}：{p.get('detail', '')}" for p in verdict.get("problems", []))
    return False


# ---------- 2. リンク切れ ----------

def statements_for(url, us):
    """その出典を使っているページの、文の一覧（AIへの手がかり）。"""
    out = []
    for u in us:
        if any(s["url"] == url for s in u.sources):
            strs = [v for v in flatten(u.get()).values() if isinstance(v, str) and len(v) > 20]
            out.append(f"【{u.title}】\n" + "\n".join(strs[:30]))
    return "\n\n".join(out)[:20000]


def replacement(url, label, us, today, log):
    stm = statements_for(url, us)
    user = f"【開けなくなった出典】\n{label}\n{url}\n\n【その出典を根拠にしている文（ページごと）】\n{stm}"
    text, wmodel = ai.write_with_search(prompt_body("replace_source.md"), user)
    entry = {"mode": "代わりの出典", "writer_model": wmodel}
    log["rounds"].append(entry)
    try:
        ans = ai.extract_json(text)
    except Exception as e:  # noqa: BLE001
        entry["result"] = f"JSONとして読めない: {e}"
        return False
    entry["answer"] = ans
    if not ans.get("found") or not str(ans.get("url", "")).startswith("https://"):
        entry["result"] = "見つからない：" + str(ans.get("why", ""))
        return False
    try:
        kind, body = SRC.fetch_text(ans["url"])
    except Exception as e:  # noqa: BLE001
        entry["result"] = f"候補が開けない：{e}"
        return False
    ex = SRC.excerpt(body, [stm], 15000)
    ctext, cmodel = ai.check(
        "あなたは出典を確かめる担当です。「元の文」の中身が「候補のページの本文」で確かめられるかを判定し、"
        '{"pass": true/false, "problems": [{"detail": "確かめられない文と理由"}]} のJSONだけを返してください。'
        "候補が報道や個人のサイトなら pass は false にしてください。",
        f"【元の出典】{label} {url}\n【候補】{ans.get('label')} {ans['url']}\n\n【元の文】\n{stm}\n\n【候補のページの本文（抜粋）】\n{ex}")
    entry["checker_model"] = cmodel
    try:
        verdict = ai.extract_json(ctext)
    except Exception as e:  # noqa: BLE001
        verdict = {"pass": False, "problems": [{"detail": str(e)}]}
    entry["check"] = verdict
    if not verdict.get("pass"):
        entry["result"] = "確かめる担当AIが不合格"
        return False
    entry["result"] = "合格（提案にする）"
    save(f"source-{today}-{hashlib.sha1(url.encode()).hexdigest()[:10]}", {"kind": "source", "date": today, "old_url": url, "old_label": label,
                                                      "new_url": ans["url"], "new_label": ans.get("label") or label,
                                                      "why": ans.get("why", ""), "log": log.get("path", "")})
    return True
