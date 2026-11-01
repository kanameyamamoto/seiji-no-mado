"""国会で新しく成立した法律・予算・条約を見つけて、下書き（data/pending/）を作る。

載せる範囲（feed.json の criteria）：第220回国会（2026年1月召集）以降に成立した法律・予算・条約を、すべて載せる。人は選ばない。
ここでは「両院で可決・承認された議案」を参議院の議案情報から機械的に集めるだけで、
文章は書かない（文章は ai_write.py、確認は ai_check.py）。

使い方：
  python pipeline/update_laws.py              # data/config.json の sessions を確認
  python pipeline/update_laws.py --sessions 222 223
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import sangiin as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PENDING = os.path.join(DATA, "pending")

KIND = {"法律案（内閣提出）": ("閣法", "法律が成立"), "法律案（衆法）": ("衆法", "法律が成立"),
        "法律案（参法）": ("参法", "法律が成立"), "予算": ("予算", "予算が成立"), "条約": ("条約", "条約を承認")}


def key_of(session, cat, no):
    return f"{session}-{cat}-{no}"


def existing_keys(feed):
    keys = set()
    for it in feed.get("items", []):
        if it.get("src_key"):
            keys.add(it["src_key"])
            continue
        # 既存データ（src_key がない分）は、参議院のURLから作る
        for f in it.get("facts", []) + [it.get("process", {})]:
            for s in (f.get("sources") or ([f.get("source")] if f.get("source") else [])):
                u = (s or {}).get("url", "")
                if "/meisai/m" in u:
                    keys.add(u.split("/meisai/")[1])
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", nargs="*")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    sessions = a.sessions or cfg["sessions"]
    feed = json.load(open(os.path.join(DATA, "feed.json"), encoding="utf-8"))
    have = existing_keys(feed)
    os.makedirs(PENDING, exist_ok=True)
    pending_now = {f for f in os.listdir(PENDING) if f.endswith(".json")}
    new = 0
    for ses in sessions:
        try:
            urls = S.list_bills(str(ses))
        except Exception as e:  # noqa: BLE001
            print(f"[skip] 第{ses}回国会の一覧を読めませんでした: {e}")
            continue
        for u in urls:
            page = u.split("/meisai/")[1]
            if page in have:
                continue
            r = S.parse_bill(u)
            if not S.is_enacted(r):
                continue
            typ = r.get("議案審議一覧/種別", "")
            if typ not in KIND:  # 決算・承認案件（NHK予算など）・人事は載せる範囲の外（feed.json の criteria_detail）
                continue
            cat, kind = KIND[typ]
            no = r.get("議案審議一覧/提出番号", "")
            k = key_of(ses, cat, no)
            fn = f"{k}.json"
            if fn in pending_now:
                continue
            vote = None
            if r.get("参議院本会議経過/vote"):
                try:
                    vote = S.parse_vote(r["参議院本会議経過/vote"])
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] 投票結果を読めませんでした {k}: {e}")
            dates = [S.wdate(r.get("参議院本会議経過/議決日", "")), S.wdate(r.get("衆議院本会議経過/議決日", ""))]
            raw = {
                "src_key": page,
                "key": k,
                "kind": kind,
                "category": cat,
                "session": ses,
                "number": f"{cat}第{no}号" if cat in ("閣法", "衆法", "参法") else "",
                "formal": r.get("議案審議一覧/件名", ""),
                "date": max(d for d in dates if d) if any(dates) else "",
                "promulgated": S.wdate(r.get("その他の/公布年月日", "")),
                "law_no": r.get("その他の/法律番号", ""),
                "ministry": (r.get("その他の/所管官庁", "") or "").split("、")[0],
                "yoshi": r.get("yoshi", ""),
                "bill": r,
                "vote": vote,
                "source": {"label": f"参議院 議案情報（第{ses}回国会 {cat}第{no}号）", "url": u},
                "status": "fetched",
            }
            json.dump(raw, open(os.path.join(PENDING, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            new += 1
            print(f"[new] {k} {raw['formal']}")
    print(f"新しく見つかった件数: {new}")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as gh:
        gh.write(f"new={new}\n")


if __name__ == "__main__":
    main()
