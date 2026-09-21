# ADR-0005: 評価基盤と最適化目標

- 状態: 採用
- 日付: 2026-09-21

## 文脈

ADR-0002 でチューニングを「pack version を上げること」と定義した。何を良くするためにチューニングするのか (目的関数)、誰が loop を回すのか、harness がそのために何を提供するのかを決める必要がある。

`docs/PRODUCT.md` は「学習者時間あたりの改善最大化」を製品の命題としている。

## 決定

- 主指標は学習効率 = target mastery 到達までの学習者時間/活動数。
- 副指標: mastery 推定精度、hint 依存、最初の有効な行動までの時間。
- チューニング loop は人間が回す。harness が pack 単位のレポートと pack version 間比較を出し、人間が pack を編集する。
- harness が提供する評価コマンドは 3 系統。コマンド名は仮で、CLI の正確な形は未確定。
  - `simulate`: 合成学習者でアルゴリズム構成の学習効率を測る。
  - `replay`: 記録済み session を別 pack version で再計算する。append-only event store の副産物。
  - `report`: pack 単位で主指標と副指標を出す。
- 評価資産は pack 内 `eval/` に置く。
  - `golden/`: ラベル付き行動ログ → 期待 evidence
  - `learners/`: 合成学習者の定義
  - `sessions/`: export した記録 session
- 限界: 合成学習者では UX を測れない。UX 最適化は実 session の指標でのみ評価できる。最初の実学習者はオーナー自身。

## 却下した案

- 主指標を転移/定着にする: 計測に時間がかかる。副次的に追う。
- 継続・没入を主指標にする: 学習成果と乖離しうる。
- AI agent が pack 変更を提案する: チューニング loop は人間が回す。
- 自動パラメータ探索: UX に効かない。
- 評価資産を pack 横断の top-level に置く: 評価資産は pack 単位のチューニングに属するため pack 内に置く。

## 影響

- 更新が必要な docs:
  - `docs/PRODUCT.md`: 主指標と副指標の定義。
  - `docs/SUBJECT_PACK.md`: pack 構造に `eval/` (`golden/`, `learners/`, `sessions/`) を追加する。
  - `docs/ARCHITECTURE.md`: 評価コマンド 3 系統の位置付け。
  - `docs/EVENT_SCHEMA.md`: `replay` と session export が前提とする event の要件を確認する。
  - `docs/IMPLEMENTATION_PLAN.md`: 評価基盤の実装順。
- 関連 ADR: ADR-0001 (評価資産は `contents/<pack-id>/` 配下)、ADR-0002 (`pack_version` 間比較)、ADR-0003 (UX は実 session でのみ評価)、ADR-0004 (`simulate` の対象は registry の構成)。
