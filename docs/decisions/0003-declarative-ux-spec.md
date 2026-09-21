# ADR-0003: UX は pack の宣言的 layout spec で決める

- 状態: 採用
- 日付: 2026-09-21

## 文脈

`docs/UX.md` は 3 ペイン固定 layout (main pane / side pane / persistent AI chat) を harness の仕様として記述している。しかし ADR-0002 により、UX はコンテンツごとに最適化する対象になった。terminal 主画面の SE pack と音声主画面の English pack が同じ固定 layout を共有する前提は成り立たない。

一方で pack はコードを持たない純データであることを維持したい (ADR-0001, ADR-0002)。

## 決定

- SDK が UI 部品を提供する。pack が YAML/JSON で配置・mode・遷移・文言を宣言する。
- `docs/UX.md` の 3 ペイン固定 layout は harness 仕様ではなく、SE pack の UX 宣言例という位置付けになる。
- harness 固定として残るもの:
  - highlight / chat / pane 状態の永続化
  - abstraction → reality 導線の仕組み
  - accessibility 要件
  - 全 UI 操作の event 化
- layout spec の具体的な schema は未確定。

## 却下した案

- SDK の固定 preset から名前で選ぶだけ: コンテンツごとの UX 最適化の余地が preset の数に制限される。
- pack が React コードを同梱する: pack がデータでなくなり、安全性と互換性の管理が重くなる。

## 影響

- 新しい UI 部品が必要になった場合は SDK 側に追加する (pack 側では追加できない)。
- 更新が必要な docs:
  - `docs/UX.md`: harness 固定の要件と、SE pack の UX 宣言例とを分離する。
  - `docs/SUBJECT_PACK.md`: pack 構造に layout spec を追加する。
  - `docs/ARCHITECTURE.md`: Web Client が pack の layout spec を解釈して UI 部品を配置すること。
  - `docs/ACCEPTANCE_CRITERIA.md`: D 系 (side pane / highlight) と C 系が SE pack の宣言を通して満たされることの確認。
- 関連 ADR: ADR-0001 (UI 部品は SDK の一部)、ADR-0002 (UX 調整値は pack が持つ)、ADR-0005 (UX は実 session の指標でのみ評価できる)。
