"""公開しなかった下書き（status=held）を、人が見られるように知らせる。

- GitHub Actions の実行結果のページ（Summary）に一覧を書く。
- まだ知らせていないものは、Issue（ラベル「要確認」）を1件ずつ作る。
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING = os.path.join(ROOT, "data", "pending")


def main():
    held = []
    for fn in sorted(os.listdir(PENDING)) if os.path.isdir(PENDING) else []:
        if fn.endswith(".json"):
            p = os.path.join(PENDING, fn)
            raw = json.load(open(p, encoding="utf-8"))
            if raw.get("status") == "held":
                held.append((p, raw))
    lines = ["## 公開しなかった下書き", ""] + [f"- {r['key']} {r['formal']}：{r.get('held_reason', '')}" for _, r in held] if held else ["公開しなかった下書きはありません。"]
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not os.environ.get("GH_TOKEN"):
        return
    subprocess.run(["gh", "label", "create", "要確認", "--color", "D93F0B", "--force"], check=False, capture_output=True)
    for p, raw in held:
        if raw.get("issue"):
            continue
        body = (f"自動の更新で、この記事は公開しませんでした。\n\n- 議案：{raw['formal']}\n- 理由：{raw.get('held_reason', '')}\n"
                f"- 出典：{raw['source']['url']}\n- 下書き：`data/pending/{os.path.basename(p)}`\n- チェックの記録：`data/logs/{raw['key']}.json`\n\n"
                "直し方は docs/運用.md の「公開されなかった記事」を見てください。")
        r = subprocess.run(["gh", "issue", "create", "--title", f"要確認：{raw['formal'][:60]}", "--body", body, "--label", "要確認"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            raw["issue"] = r.stdout.strip()
            json.dump(raw, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
