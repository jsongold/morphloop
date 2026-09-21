# ADR-0004: 学習アルゴリズムは harness の registry + pack の宣言

- 状態: 採用
- 日付: 2026-09-21

## 文脈

`docs/LEARNING_LOOP.md` は learner model と policy を差し替え可能にすると述べ、初期実装として BKT 系 model と priority 式 (`gap × uncertainty × target_weight × prerequisite_readiness × novelty_adjustment`) を挙げている。ADR-0002 により学習アルゴリズムはコンテンツごとに最適化する対象になったため、「誰がアルゴリズムを選び、どこに実装を置くか」を決める必要がある。

pack は純データでなければならない (ADR-0001, ADR-0002)。

## 決定

- harness が learner model / policy / assessment strategy の実装を registry で提供する。
- pack は実装名と全パラメータを宣言する。pack はコードを持たない。
- 新しいアルゴリズムが必要な場合は harness に実装を追加する。
- `docs/LEARNING_LOOP.md` の priority 式と BKT 系 model は registry の一実装という位置付けになる。
- harness 固定として残るもの:
  - loop の骨格
  - event schema
  - mastery 更新は必ず evidence ID を参照するという不変条件
  - 「正しい最終状態 ≠ mastery」の原則
  - LLM 出力の schema 検証
- registry の interface と pack 側の宣言 schema の詳細は未確定。

## 却下した案

- pack が Python 実装を同梱する: pack がデータでなくなり、安全性・互換性の管理が重くなる。
- pack が式・ルールの DSL を書く: DSL の設計・保守コストが大きい。

## 影響

- registry に入る実装は特定 subject に依存してはならない (AC-G1 と同じ境界)。
- 更新が必要な docs:
  - `docs/LEARNING_LOOP.md`: priority 式と BKT 系 model を registry の一実装として書き直し、harness 固定の不変条件を明示する。
  - `docs/SUBJECT_PACK.md`: pack によるアルゴリズム実装名とパラメータの宣言を追加する。
  - `docs/ARCHITECTURE.md`: `core/learner`, `core/policy` と assessment の registry 構造。
  - `docs/DOMAIN_MODEL.md`: 影響の有無を確認する。
- 関連 ADR: ADR-0002 (パラメータは全て pack が持つ)、ADR-0005 (`simulate` でアルゴリズム構成を評価する)、ADR-0006 (MVP で必要な実装の範囲)。
