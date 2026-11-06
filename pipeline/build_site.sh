#!/usr/bin/env bash
# 公開するファイルだけを _site/ に集める（下書き data/pending/ は公開しない）。
set -euo pipefail
rm -rf _site
mkdir -p _site/data/logs _site/prompts
cp index.html .nojekyll _site/
cp -r assets _site/
cp data/*.json _site/data/
mkdir -p _site/data/rs && cp data/rs/*.json _site/data/rs/ 2>/dev/null || true  # 国の予算事業と支出先（rs.py）
rm -f _site/data/watch.json  # 見張りの記録はサイトでは使わない（リポジトリでは公開している）
cp -r data/logs/. _site/data/logs/ 2>/dev/null || true
cp prompts/*.md _site/prompts/
python3 pipeline/stats.py _site/data/stats.json  # トップページの数
python3 pipeline/prerender.py _site
python3 pipeline/api.py _site
echo "公開するファイル: $(find _site -type f | wc -l) 件"
