# ADR-0001: このリポジトリは Harness (SDK)、コンテンツは contents/ に置く

- 状態: 採用
- 日付: 2026-09-21

## 文脈

既存仕様 (`docs/ARCHITECTURE.md`, `docs/SUBJECT_PACK.md`) は、adaptive learning core と subject pack を分離する方針を示しているが、次の点が未定だった。

- このリポジトリが「製品アプリ」なのか「エンジンを提供する SDK」なのか。
- subject pack と評価資産をどこに置くか (`docs/SUBJECT_PACK.md` は `packs/` を例示している)。
- Web UI がエンジン側の成果物なのか、エンジンを使う側の参照アプリなのか。

`CLAUDE.md` の原則 8 (Domain agnostic core) と AC-G1 (core は SE pack のコードを import しない) を構造で担保する必要がある。

## 決定

- このリポジトリは Harness (SDK) である。エンジン本体・部品・契約を提供する。
- コンテンツ/データ (subject pack と評価資産) は同一リポジトリ top-level の `contents/<pack-id>/` に置く。
- harness は `contents/` を import しない。pack のパスを受け取って読む。これにより `contents/` は後で別リポジトリに切り出せる。
- Web UI は SDK の一部とする。UI 部品 (terminal, editor, visualization renderer, chat, side pane 等) も SDK が提供する。

## 却下した案

- データを最初から別リポジトリにする: 境界はパス渡しで既に保てる。切り出しは後からできる。
- リポジトリ外の外部パスのみを受け付ける: 同一リポジトリに置けなくする理由がない。
- Web UI を SDK に含めず参照アプリにする: UI 部品を SDK が提供しないと、pack が UX を宣言する方式 (ADR-0003) が成立しない。
- UI を後回しにして headless で先行する: practice-first の製品では UI が学習体験の中核であり、後回しにしない。

## 影響

- 更新が必要な docs:
  - `docs/ARCHITECTURE.md`: リポジトリが SDK であること、`contents/` の位置付け、harness が `contents/` を import しない規則、Web UI と UI 部品が SDK の一部であること。
  - `docs/SUBJECT_PACK.md`: pack の置き場所を `packs/` から `contents/<pack-id>/` に変更。
  - `docs/PRODUCT.md`: Harness (SDK) とコンテンツの関係。
  - `CLAUDE.md`: Engineering rules の境界記述。
- 関連 ADR: ADR-0002 (調整値は pack が持つ)、ADR-0003 (UX は pack の宣言)、ADR-0004 (アルゴリズムは registry + pack 宣言)、ADR-0005 (評価資産は pack 内 `eval/`)、ADR-0006 (ダミー pack は `contents/` ではなく test fixture)。
