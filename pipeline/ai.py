"""AIの呼び出し（書く担当と確かめる担当を、別の会社にする：ルールC-1）。

- 書く担当：Anthropic（環境変数 ANTHROPIC_API_KEY、モデルは WRITER_MODEL）
- 確かめる担当：OpenAI（環境変数 OPENAI_API_KEY、モデルは CHECKER_MODEL）
外部ライブラリは使わず、HTTPで直接呼ぶ。
"""
import json
import os
import urllib.request


def _post(url, headers, body, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def writer_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("WRITER_MODEL"))


def checker_available():
    return bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("CHECKER_MODEL"))


def write(system, user, max_tokens=8000):
    res = _post(
        "https://api.anthropic.com/v1/messages",
        {"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        {"model": os.environ["WRITER_MODEL"], "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
    )
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"), res.get("model", os.environ["WRITER_MODEL"])


def check(system, user):
    res = _post(
        "https://api.openai.com/v1/chat/completions",
        {"content-type": "application/json", "authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
        {"model": os.environ["CHECKER_MODEL"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
         "response_format": {"type": "json_object"}},
    )
    return res["choices"][0]["message"]["content"], res.get("model", os.environ["CHECKER_MODEL"])


def extract_json(text):
    """返答の中の最初の { から最後の } までをJSONとして読む。"""
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("JSONが見つかりません")
    return json.loads(text[i:j + 1])
