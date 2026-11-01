# せいじのまど

政治の情報を、どの党にもメディアにも偏らずに届けるサイトです。
事実と意見を分けて、結論は書きません。書き方のルールは、すべて公開しています（[data/rules.json](data/rules.json)）。

- サイト：GitHub Pages で公開（サーバーは使いません）
- 更新：GitHub Actions が毎朝、次のことを行います（くわしくは [docs/運用.md](docs/運用.md)）
  - 会派別の議員数・国会の会期・法律の公布日を、元の資料から機械で更新
  - 新しく成立した法律・予算・条約の記事を、Claude（スケジュール）が書き、機械と Gemini で確かめて公開
  - 出典が変わったページ・確認日から日数がたったページを、出典と照らして見直す（出典は週1回すべて見張る）

## しくみ

```
参議院 議案情報 ──▶ update_laws.py ──▶ data/pending/（下書き：資料をそのまま保存）
                                          │
                         ai_draft.py ◀────┘
                           ├ 書く担当（Claude のスケジュール。work/ で受け渡し）が4つのレベルで書く
                           ├ 数字・日付を、AIを使わずに元の資料と照合（C-2）
                           ├ 確かめる担当（Google Gemini）が誤り・もれ・評価語・偏りを確認（C-1）
                           └ 通らなければ書き直し（最大3回、だめなら公開しない：C-3）
                                          │
                         publish.py ──▶ data/feed.json ──▶ validate.py ──▶ GitHub Pages
                                          │
                                   data/logs/（指示文・モデル名・チェック結果：C-4）
```

| ファイル | 役割 |
|---|---|
| `index.html` | サイト本体（1ファイル。`data/*.json` を読んで表示） |
| `data/*.json` | 中身（国会で決まったこと・学ぶ・政党・メディア・つながり・用語・ルール） |
| `data/config.json` | 確かめる国会の回次、見直しの間隔と1日の上限 |
| `data/watch.json` | 出典の見張りの記録と、見直し待ちのページ |
| `data/logs/` | AIへの指示文・モデル名・チェックの結果（記事・見直しごと） |
| `pipeline/` | 更新のプログラム（Python） |
| `work/` | Claude（スケジュール）への依頼と答えの受け渡し（[docs/claude-writer.md](docs/claude-writer.md)） |
| `prompts/` | AIへの指示文（そのまま公開） |
| `.github/workflows/` | 毎日の更新・公開・チェック |

## 間違いを見つけたら・ルールを変えたいとき

- 間違い：Issue の「訂正の依頼」から（ルールT-2）
- ルール：Issue の「ルール変更の提案」から。変更案と理由を公開してから反映します（ルールT-1）

運用の手順は [docs/運用.md](docs/運用.md) にまとめています。

## データを使う（公開API）

登録・キーなしで、無料で使えます（CC BY 4.0）。一覧：`https://kanameyamamoto.github.io/seiji-no-mado/api/v1/index.json`
（国会で決まったこと・政党・国会議員〈参議院議員は法律ごとの賛否つき〉・知事・学ぶ・つながり・ことば・ルール）

## ライセンス

- プログラム（`index.html` の中のコード、`pipeline/`、`.github/`）：MIT License（[LICENSE](LICENSE)）
- 文章・データ（`data/`、`prompts/`、`docs/`）：CC BY 4.0（[LICENSE-CONTENT](LICENSE-CONTENT)）
- 出典として引用している官公庁などの資料は、それぞれの出典の利用条件に従います。
