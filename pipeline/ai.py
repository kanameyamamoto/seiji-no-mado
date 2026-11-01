"""AIの呼び出し（書く担当と確かめる担当を、別の会社にする：ルールC-1）。

書く担当：Claude
  - 既定（WRITER=files）：Claude のスケジュール（毎朝動く Claude の作業）が書く。
    プログラムは「書いてほしいこと」を work/requests/ に書き出して、その件はいったん止まる。
    Claude が答えを work/answers/ に置くと、次に動いたときにそれを使って先に進む。
  - WRITER=api：Anthropic の API を直接呼ぶ（ANTHROPIC_API_KEY・WRITER_MODEL）。

確かめる担当：Google Gemini（GEMINI_API_KEY、モデルは CHECKER_MODEL。既定 gemini-flash-latest）。
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

GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]  # 名前が見つからない（404）ときは次を試す


def _gemini(system, user):
    models = [os.environ["CHECKER_MODEL"]] if os.environ.get("CHECKER_MODEL") else GEMINI_MODELS
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    last = None
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for i in range(3):
            try:
                res = _post(url, {"content-type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]}, body)
                cands = res.get("candidates") or []
                text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else []))
                return text, res.get("modelVersion", model)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404:
                    break  # このモデル名はない → 次のモデル
                if e.code == 429:  # 無料枠の回数の上限
                    if i < 2:
                        time.sleep(30)
                        continue
                    raise RetryLater("Gemini の回数の上限（無料枠）")
                if e.code in (500, 502, 503, 504):
                    time.sleep(10 * (i + 1))
                    continue
                raise
        else:
            raise RetryLater("Gemini が応答しない")
    raise last


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
