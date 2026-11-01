#!/usr/bin/env bash
# 公開するファイルだけを _site/ に集める（下書き data/pending/ は公開しない）。
set -euo pipefail
rm -rf _site
mkdir -p _site/data/logs _site/prompts
cp index.html .nojekyll _site/
cp data/*.json _site/data/
cp data/logs/*.json _site/data/logs/ 2>/dev/null || true
cp prompts/*.md _site/prompts/
echo "公開するファイル: $(find _site -type f | wc -l) 件"
