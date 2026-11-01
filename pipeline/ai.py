"""AIの呼び出し（書く担当と確かめる担当を、別の会社にする：ルールC-1）。

書く担当：Claude
  - 既定（WRITER=files）：Claude のスケジュール（毎朝動く Claude の作業）が書く。
    プログラムは「書いてほしいこと」を work/requests/ に書き出して、その件はいったん止まる。
    Claude が答えを work/answers/ に置くと、次に動いたときにそれを使って先に進む。
  - WRITER=api：Anthropic の API を直接呼ぶ（ANTHROPIC_API_KEY・WRITER_MODEL）。

確かめる担当：Google Gemini（GEMINI_API_KEY。モデルは GEMINI_MODELS の順に試す。CHECKER_MODEL で1つに決めることもできる）。
  OpenAI に戻すときは CHECKER=openai（OPENAI_API_KEY）。
  回数の上限（無料枠）に当たったら、失敗にはせず、その件を次の回に回す（RetryLater）。

同じ依頼には、いつも同じ答えを使う（答えと確認の結果を work/ に残し、その件が終わったら消す）。
こうすると、何度動かしても途中から同じように続けられる。
"""
import datetime
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
REQ, ANS, CHK = (os.path.join(WORK, d) for d in ("requests", "answers", "checks"))
WRITER = os.environ.get("WRITER", "files")
CHECKER = os.environ.get("CHECKER", "gemini")
CURRENT = {"item": ""}  # いま扱っている件（記事・ページ）のID。work/ のファイルに記録し、終わったら消す


class NeedWriter(Exception):
    """書く担当（Claude）の答えがまだない。"""


class RetryLater(Exception):
    """確かめる担当の回数の上限など。次の回にもう一度。"""


def set_item(item):
    CURRENT["item"] = item


def _key(*parts):
    return hashlib.sha256("\u0000".join(parts).encode("utf-8")).hexdigest()[:20]


def _jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _post(url, headers, body, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def writer_available():
    return WRITER == "files" or bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("WRITER_MODEL"))


def checker_available():
    if CHECKER == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return bool(os.environ.get("GEMINI_API_KEY"))


# ---------- 書く担当 ----------

def _from_files(kind, system, user):
    k = _key(kind, system, user)
    ans = os.path.join(ANS, k + ".json")
    if os.path.exists(ans):
        a = json.load(open(ans, encoding="utf-8"))
        return a.get("text", ""), a.get("model", "Claude（スケジュール）")
    _jdump(os.path.join(REQ, k + ".json"), {
        "id": k, "kind": kind, "for": CURRENT["item"],
        "created": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d"),
        "how": "system の指示に従って user に答え、答えの文だけを work/answers/" + k + '.json に {"text": "…", "model": "…"} の形で保存する'
               + ("。ウェブ検索を使ってよい" if kind == "search" else ""),
        "system": system, "user": user})
    raise NeedWriter(k)


def write(system, user, max_tokens=8000):
    if WRITER == "files":
        return _from_files("write", system, user)
    res = _post(
        "https://api.anthropic.com/v1/messages",
        {"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        {"model": os.environ["WRITER_MODEL"], "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
    )
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"), res.get("model", os.environ["WRITER_MODEL"])


def write_with_search(system, user, max_tokens=4000, max_searches=5):
    """書く担当に、ウェブ検索を使わせる（リンク切れの代わりの出典を探すときだけ使う）。"""
    if WRITER == "files":
        return _from_files("search", system, user)
    res = _post(
        "https://api.anthropic.com/v1/messages",
        {"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        {"model": os.environ["WRITER_MODEL"], "max_tokens": max_tokens, "system": system,
         "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}],
         "messages": [{"role": "user", "content": user}]},
        timeout=300,
    )
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"), res.get("model", os.environ["WRITER_MODEL"])


# ---------- 確かめる担当 ----------

# 上から順に試す。名前がない（404）・回数の上限（429）・応答しないときは、次のモデルへ。
# 2026年11月に試した結果：gemini-3.8-flash は約50秒、軽い版（gemini-flash-lite-latest → gemini-3.5-flash-lite）は約1秒で、
# どちらも見本の記事のまちがい（評価語・施行日の誤り）を見つけた。
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-flash-lite-latest"]


def _gemini(system, user):
    models = [os.environ["CHECKER_MODEL"]] if os.environ.get("CHECKER_MODEL") else GEMINI_MODELS
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    reasons = []
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for i in range(2):
            try:
                res = _post(url, {"content-type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]}, body, timeout=150)
                cands = res.get("candidates") or []
                text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
                if not text.strip():
                    reasons.append(f"{model}：空の返事")
                    break
                return text, res.get("modelVersion", model)
            except urllib.error.HTTPError as e:
                if e.code in (500, 502, 503, 504) and i == 0:
                    time.sleep(15)
                    continue
                reasons.append(f"{model}：HTTP {e.code}")
                if e.code in (404, 429, 500, 502, 503, 504):
                    break  # 次のモデルへ
                raise
            except (TimeoutError, OSError) as e:  # 応答しない・接続が切れた
                reasons.append(f"{model}：{type(e).__name__}")
                break
    raise RetryLater("Gemini で確かめられない（" + "、".join(reasons) + "）")


def _openai(system, user):
    model = os.environ.get("CHECKER_MODEL") or "gpt-5-mini"
    res = _post(
        "https://api.openai.com/v1/chat/completions",
        {"content-type": "application/json", "authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
        {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
         "response_format": {"type": "json_object"}},
    )
    return res["choices"][0]["message"]["content"], res.get("model", model)


def check(system, user):
    k = _key("check", CHECKER, system, user)
    path = os.path.join(CHK, k + ".json")
    if os.path.exists(path):
        c = json.load(open(path, encoding="utf-8"))
        return c["text"], c["model"]
    text, model = (_openai if CHECKER == "openai" else _gemini)(system, user)
    _jdump(path, {"for": CURRENT["item"], "text": text, "model": model})
    return text, model


# ---------- 片づけ ----------

def finish(item):
    """その件が終わったら（公開・提案・人へ知らせる）、work/ の依頼・答え・確認の結果を消す。"""
    ids = set()
    for d in (REQ, CHK):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            try:
                if json.load(open(p, encoding="utf-8")).get("for") == item:
                    os.remove(p)
                    ids.add(fn)
            except (ValueError, OSError):
                pass
    # 答えは依頼と同じ名前。依頼がなくなった答えは消す
    if os.path.isdir(ANS):
        live = set(os.listdir(REQ)) if os.path.isdir(REQ) else set()
        for fn in os.listdir(ANS):
            if fn not in live:
                os.remove(os.path.join(ANS, fn))


def extract_json(text):
    """返答の中の最初の { から最後の } までをJSONとして読む。"""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("JSONが見つかりません")
    return json.loads(text[i:j + 1])
