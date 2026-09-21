# ADR-0010: Provenance の記録と replay の範囲

- 状態: 採用
- 日付: 2026-09-21

## 文脈

ADR-0002 は「event に記録される `pack_version` の version 間比較がそのままチューニングの評価になる」とし、ADR-0005 は `replay` を「記録済み session を別 pack version で再計算する」と説明した。レビューで 2 つの穴が指摘された。

1. 結果は `pack_version` 以外にも依存する。harness とアルゴリズム実装の version、pack の実体、lab image と fixture、evaluator と prompt の version、LLM の provider/model/設定、event schema と projection 実装。`pack_version` だけでは再現も比較もできない。
2. replay できる範囲が過大。policy を変えれば出題が変わり学習者の行動も変わる (反実仮想) ので、保存済み行動からは再計算できない。UX も同様。activity や skill の定義が変わった version 間で過去の行動を replay する意味も未定義。LLM evaluator は非決定的。

## 決定

### Provenance

- 再現に必要な情報を event に記録する。記録する項目:
  - harness version
  - `pack_id`、`pack_version`、pack content hash
  - 使用した registry 実装の `name@version` (learner model / policy / assessment)
  - domain adapter の version (adapter 層は ADR-0009 で定義予定)
  - lab image の digest と fixture id (lab 開始時)
  - evaluator rubric の id と version
  - LLM を使った event には provider、model、prompt の version、生成パラメータ
- どの event にどの項目を載せるかは docs 反映時に決める。原則は「session 開始時に一括、変わりうるものはその発生 event に」。
- pack content hash は pack ディレクトリの内容から計算する。`eval/` を hash に含めるかは未確定。
- `pack_version` は人間向けのラベル、pack content hash が同一性の正とする。version を上げ忘れた編集も hash で検出できる。

### Replay の範囲

- Level 1 (MVP で提供): 保存済み evidence から learner model を再計算する。アルゴリズム実装やパラメータを変えて learner state の推移を比較する。決定的。
- Level 2 (MVP 後): 保存済み event に対して決定的 check を再評価し、evidence を作り直す。
- LLM の出力は replay で再実行しない。保存済みの出力を使う。LLM evaluator を変えて再評価したい場合は replay ではなく新しい evaluation として記録し、元の evaluation と区別する。
- 対象外: policy の反実仮想 (別の policy ならどう出題したか) と UX の違い。これらは `simulate` か実 session でしか評価できない。
- `skill_id` の集合や activity definition が変わった version 間では、共通する `skill_id` についてのみ Level 1 を行う。対応しない evidence は保持したまま無視する。

### Version 間比較

- 比較レポートは、2 つの pack content の差分を top-level ディレクトリ単位 (`algorithm/`, `ux/`, `activities/`, `evaluators/` 等) で示し、「何を変えた結果か」を読めるようにする。
- tuning 用と content 用で version 体系を分けることはしない。
- pack を更新したときの learner state は、保存済み evidence から新しい構成で再計算する (Level 1 と同じ経路)。

### 既存 ADR との関係

- ADR-0002 の「`pack_version` の比較がそのままチューニングの評価になる」と、ADR-0005 の `replay` の説明 (別 pack version で session を再計算) は、この ADR で置き換える。それ以外の決定は有効。

## 却下した案

- `pack_version` だけを記録する (現状): 再現に必要な情報が足りない。
- attempt ごとに pack 全体をスナップショット保存する: hash と git 履歴で足りる。
- replay で LLM を再実行する: 非決定的で費用もかかり、比較の基準にならない。
- tuning 用と content 用で version を分ける: 体系が増える。差分の提示で足りる。
- replay で policy の効果を推定する: 反実仮想で、保存済み行動からは分からない。

## 影響

- 未確定: pack content hash に `eval/` を含めるか、各 provenance 項目をどの event に載せるか。
- ADR-0002 と ADR-0005 の状態行を「一部置き換え (ADR-0010)」にする必要がある。
- 更新が必要な docs:
  - `docs/EVENT_SCHEMA.md`: provenance 項目の追加と、載せる event の割り当て。
  - `docs/ARCHITECTURE.md`: Persistence の `pack_version` 記述と、Evaluation commands の `replay` の説明 (Level 1/2 と対象外)。
  - `docs/LEARNING_LOOP.md`: Tuning and evaluation 節の version 間比較と `replay` の説明。
  - `docs/SUBJECT_PACK.md`: pack content hash、`eval/sessions/` と replay の説明、pack 更新時の learner state 再計算。
  - `docs/ACCEPTANCE_CRITERIA.md`: AC-H3 (`pack_version` のみ → provenance 一式) と AC-I2 (replay を Level 1 の範囲に限定)。
- 関連 ADR: ADR-0002、ADR-0004 (registry 実装の `name@version`)、ADR-0005、ADR-0007 (runtime instance)、ADR-0008 (event 契約)、ADR-0009 (adapter 層、予定)。
