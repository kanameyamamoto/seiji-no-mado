"""公開する前の機械チェック。1つでも問題があれば終了コード1で止める（公開しない）。

- すべての data/*.json がJSONとして読めるか
- 4つの読むレベル（es・hs・std・univ）が、どれか1つでもあれば全部そろっているか（L-1）
- 「国会で決まったこと」の各記事：必要な項目、出典（F-1）、IDの重複、並び順（D-6）
- 記事についているルール札が rules.json にあるか
- 評価を含む言葉が入っていないか（V-4。引用は除く）
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import publish  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LEVELS = {"es", "hs", "std", "univ"}
REQUIRED = ["id", "kind", "date", "session", "title", "formal", "number", "topic", "lead", "facts", "process", "checked"]
EVAL_WORDS = ["画期的", "暴挙", "悪法", "愚策", "英断", "ようやく", "残念", "素晴らしい", "許されない", "改悪", "朗報"]

errors = []


def err(where, msg):
    errors.append(f"{where}: {msg}")


def walk_levels(x, where):
    if isinstance(x, dict):
        ks = set(x)
        if ks & LEVELS and ks <= LEVELS | {"note"}:
            for l in LEVELS - ks:
                err(where, f"読むレベル {l} がありません")
            for l in ks & LEVELS:
                if not isinstance(x[l], str) or not x[l].strip():
                    err(where, f"読むレベル {l} が空です")
        for k, v in x.items():
            walk_levels(v, f"{where}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            walk_levels(v, f"{where}[{i}]")


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def main():
    data = {}
    for fn in sorted(os.listdir(DATA)):
        if fn.endswith(".json"):
            try:
                data[fn] = json.load(open(os.path.join(DATA, fn), encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                err(fn, f"JSONとして読めません: {e}")
    for fn, d in data.items():
        walk_levels(d, fn)

    rules = data.get("rules.json", {})
    rule_ids = {r["id"] for g in rules.get("groups", []) for r in g.get("rules", [])}
    if not rule_ids:
        err("rules.json", "ルールがありません")

    feed = data.get("feed.json", {})
    seen = set()
    items = feed.get("items", [])
    for it in items:
        w = f"feed.json:{it.get('id', '?')}"
        for k in REQUIRED:
            if not it.get(k) and not (k == "facts" and it.get("facts_note")):  # 確かめられない時は facts_note に理由を書く
                err(w, f"{k} がありません")
        if it.get("id") in seen:
            err(w, "IDが重複しています")
        seen.add(it.get("id"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", it.get("date", "")):
            err(w, "date の形が YYYY-MM-DD ではありません")
        for i, f in enumerate(it.get("facts", [])):
            if not f.get("sources") or not all(s.get("url", "").startswith("https://") for s in f["sources"]):
                err(w, f"facts[{i}] に出典（https のURL）がありません（F-1）")
            for r in f.get("rules", []):
                if r not in rule_ids:
                    err(w, f"facts[{i}] のルール札 {r} が rules.json にありません")
        for s in strings({"title": it.get("title"), "lead": it.get("lead"), "facts": it.get("facts")}):
            body = re.sub(r"「[^」]*」", "", s)  # 引用の中は除く
            for ew in EVAL_WORDS:
                if ew in body:
                    err(w, f"評価を含む言葉「{ew}」があります（V-4）")
    if [i["id"] for i in items] != [i["id"] for i in sorted(items, key=publish.sort_key)]:
        err("feed.json", "並び順が決まり（成立日の新しい順…）と違います")
    if feed and feed.get("count") != len(items):
        err("feed.json", f"count（{feed.get('count')}）と件数（{len(items)}）が違います")

    if errors:
        print(f"問題が {len(errors)} 件あります。公開しません。")
        for e in errors[:200]:
            print(" -", e)
        sys.exit(1)
    print(f"チェックOK（{len(data)} ファイル、国会で決まったこと {len(items)} 件）")


if __name__ == "__main__":
    main()
