"""_proposals/ の提案を、1件ずつプルリクエストにする（人が見て「取り込む」を押すと公開される）。

プルリクエストを作れないとき（リポジトリの設定で Actions に許可していないなど）は、
同じ中身を Issue にする。
"""
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import units as U  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPOSALS = os.path.join(ROOT, "_proposals")
REPO_URL = "https://github.com/kanameyamamoto/seiji-no-mado/blob/main/"
JST = datetime.timezone(datetime.timedelta(hours=9))


def run(*args, check=True):
    try:
        r = subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError as e:
        r = subprocess.CompletedProcess(list(args), 127, "", str(e))
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip()[:300]}")
    return r


def short(v, n=160):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def apply_unit(p, today):
    data = U.load()
    u = next((x for x in U.units(data) if x.id == p["unit"]), None)
    if not u:
        raise RuntimeError(f"ページ {p['unit']} が見つかりません")
    old = u.get()
    new = dict(p["new"])
    new["updates"] = [{"date": today, "text": p.get("summary", ""), "log": p.get("log", "")}] + old.get("updates", [])
    new["checked"] = today
    u.set(new)
    U.save(data, [u.file])
    body = (f"自動の見直しで、形を変える直しが必要になりました。AIが直した版を作り、機械の確認と別の会社のAIの確認を通っています（ルールC-1〜C-4）。\n"
            f"**中身を見て、よければ「Merge」してください。** 取り込むとサイトに公開されます。\n\n"
            f"## {p['title']}（{p['unit']}）\n\n**直したこと**：{p.get('summary', '')}\n\n**きっかけ**：{'、'.join(p.get('reasons') or ['定期の見直し'])}\n\n"
            "## 変わるところ\n\n| 場所 | 前 | 後 |\n|---|---|---|\n" +
            "\n".join(f"| `{c['path']}` | {short(c['before']).replace('|', '｜')} | {short(c['after']).replace('|', '｜')} |" for c in p["changes"][:60]) +
            (f"\n\n（ほか {len(p['changes']) - 60} か所）" if len(p["changes"]) > 60 else "") +
            (f"\n\nチェックの記録：[{p['log']}]({REPO_URL}{p['log']})" if p.get("log") else ""))
    return f"見直しの提案：{p['title']}", body


def apply_source(p, today):
    data = U.load()
    hit = []
    for u in U.units(data):
        if not any(s["url"] == p["old_url"] for s in u.sources):
            continue

        def walk(o):
            if isinstance(o, dict):
                if o.get("url") == p["old_url"]:
                    o["url"] = p["new_url"]
                    if "label" in o:
                        o["label"] = p["new_label"]
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        obj = u.get()
        walk(obj)
        obj.setdefault("updates", []).insert(0, {"date": today, "text": f"出典「{p['old_label']}」が開けなくなったため、「{p['new_label']}」に差し替えた。", "log": p.get("log", "")})
        hit.append(u)
    U.save(data, sorted({u.file for u in hit}))
    body = (f"出典が3回続けて開けなくなったため、AIがウェブ検索で代わりを探しました。プログラムが開けることを確かめ、"
            f"別の会社のAIが「元の文の根拠になる」ことを確かめています。\n**よければ「Merge」してください。**\n\n"
            f"- 前：{p['old_label']}\n  {p['old_url']}\n- 後：{p['new_label']}\n  {p['new_url']}\n- 関係：{p.get('why', '')}\n\n"
            f"使っているページ：{'、'.join(u.title for u in hit)}" + (f"\n\nチェックの記録：[{p['log']}]({REPO_URL}{p['log']})" if p.get("log") else ""))
    return f"出典の差し替えの提案：{p['old_label'][:50]}", body


def main():
    if not os.path.isdir(PROPOSALS):
        print("提案はありません")
        return
    files = sorted(f for f in os.listdir(PROPOSALS) if f.endswith(".json"))
    if not files:
        print("提案はありません")
        return
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    run("gh", "label", "create", "提案", "--color", "0E8A16", "--force", check=False)
    for fn in files:
        p = json.load(open(os.path.join(PROPOSALS, fn), encoding="utf-8"))
        branch = "proposal/" + fn[:-5]
        try:
            run("git", "fetch", "-q", "origin", "main")
            run("git", "checkout", "-q", "-B", branch, "origin/main")
            title, body = (apply_unit if p["kind"] == "unit" else apply_source)(p, today)
            v = run("python", "pipeline/validate.py", check=False)
            if v.returncode != 0:
                raise RuntimeError("公開前のチェックに通らない：" + v.stdout[-500:])
            run("git", "add", "data")
            run("git", "commit", "-q", "-m", title)
            run("git", "push", "-q", "-f", "origin", branch)
            r = run("gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body, "--label", "提案", check=False)
            if r.returncode != 0:
                run("gh", "issue", "create", "--title", "要確認：" + title, "--label", "要確認", "--body",
                    body + f"\n\n---\nプルリクエストを自動で作れなかったため、Issue にしました（{r.stderr.strip()[:200]}）。"
                    f"\n直した版はブランチ `{branch}` にあります。GitHub でこのブランチからプルリクエストを作ると取り込めます。"
                    "\nリポジトリの Settings → Actions → General → 「Allow GitHub Actions to create and approve pull requests」をオンにすると、次からはプルリクエストになります。",
                    check=False)
                print(f"[issue] {title}")
            else:
                print(f"[pr] {title}：{r.stdout.strip()}")
        except Exception as e:  # noqa: BLE001
            print(f"[error] {fn}: {e}")
        finally:
            run("git", "checkout", "-q", "-f", "main", check=False)
        os.remove(os.path.join(PROPOSALS, fn))


if __name__ == "__main__":
    main()
