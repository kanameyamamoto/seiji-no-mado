"""人が見る必要があるものを知らせる。

- 公開しなかった法律の下書き（data/pending/ の status=held）
- 見直しで直せなかったページ、会派の組み替え、範囲外の国会、リンク切れ（data/watch.json の alerts）
→ GitHub Actions の実行結果のページ（Summary）に一覧を書き、Issue（ラベル「要確認」）を1件ずつ作る。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import watch_sources as W  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING = os.path.join(ROOT, "data", "pending")


def gh_issue(title, body):
    r = subprocess.run(["gh", "issue", "create", "--title", title[:120], "--body", body, "--label", "要確認"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main():
    token = bool(os.environ.get("GH_TOKEN"))
    if token:
        subprocess.run(["gh", "label", "create", "要確認", "--color", "D93F0B", "--force"], check=False, capture_output=True)
    lines = ["## 人が見る必要があるもの", ""]
    # 法律の下書き
    for fn in sorted(os.listdir(PENDING)) if os.path.isdir(PENDING) else []:
        if not fn.endswith(".json"):
            continue
        p = os.path.join(PENDING, fn)
        raw = json.load(open(p, encoding="utf-8"))
        if raw.get("status") != "held":
            continue
        lines.append(f"- 法律の下書き {raw['key']} {raw['formal']}：{raw.get('held_reason', '')}")
        if token and not raw.get("issue"):
            body = (f"自動の更新で、この記事は公開しませんでした。\n\n- 議案：{raw['formal']}\n- 理由：{raw.get('held_reason', '')}\n"
                    f"- 出典：{raw['source']['url']}\n- 下書き：`data/pending/{fn}`\n- チェックの記録：`data/logs/{raw['key']}.json`\n\n"
                    "直し方は docs/運用.md の「人が見る必要があるもの」を見てください。")
            url = gh_issue(f"要確認：{raw['formal'][:60]}", body)
            if url:
                raw["issue"] = url
                json.dump(raw, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 見直し・機械の更新からの知らせ
    w = W.load_watch()
    left = []
    for a in w.get("alerts", []):
        lines.append(f"- {a['title']}")
        if token:
            url = gh_issue(a["title"], a["body"])
            if url:
                w.setdefault("alerted", []).append({"date": a["date"], "title": a["title"], "issue": url})
                continue
        left.append(a)
    w["alerts"] = left
    w["alerted"] = w.get("alerted", [])[-200:]
    W.save_watch(w)
    if len(lines) == 2:
        lines.append("ありません。")
    due = w.get("due", {})
    lines += ["", f"見直し待ちのページ：{len(due)} 件"]
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
