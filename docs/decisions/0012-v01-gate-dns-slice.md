# ADR-0012: v0.1 の受入ゲートを DNS 縦スライスに絞る

- 状態: 採用
- 日付: 2026-09-21

## 文脈

2 つのレビューが同じ点を指摘した。

1. `docs/ACCEPTANCE_CRITERIA.md` は AC-A〜I の全てを MVP の完了条件にしている。3 lab、適応診断、learner model、Docker 隔離、terminal/WebSocket、LLM tutor/evaluator、宣言的 layout、visualization、highlight/chat 永続化、simulate/replay/report、ダミー pack が全て入っており、範囲は MVP というより platform v1 である。
2. 未確定のものが受入条件に入っている。layout spec の schema (ADR-0003)、registry の interface (ADR-0004)、HTTP lab の fixture (ADR-0006)。
3. `docs/IMPLEMENTATION_PLAN.md` は末尾で縦スライス優先と述べるが、Phase は 基盤 → 学習系 → lab → コンテンツ → AI → UX の水平分割で、境界設計の誤りが統合時 (Phase 6) まで分からない。
4. 実例が SE pack 1 つしか無い段階で汎用の layout spec を先に設計するのは早すぎる。

## 決定

ADR-0001〜0011 の設計の方向は全て維持する。変えるのは実装順と、version ごとの受入ゲートだけである。

### v0.1 のゲート

DNS 1 本の縦スライス。学習者が次を通せること:

pack を path から読み込む → DNS の ActivityAttempt を開始する → 隔離された lab を起動する → browser 内 terminal で診断コマンドを実行する → command と output が event として永続化される → サービスを復旧して submit する → 決定的 check と (必要なら) LLM evaluator が構造化 evidence を出す → evidence ID を参照して DNS の mastery が更新される → timeline を見られる → DNS の概念を highlight して引用付きで AI に質問できる → reload しても session、chat、highlight が復元される。

これは `START_CLAUDE_CODE.md` の First implementation target と同じ範囲である。

### v0.1 から守る契約

後から入れると高くつくため、最初の slice から守る。

- ADR-0007: definition と instance の分離
- ADR-0008: event 契約
- ADR-0009: core / domain adapter / pack の 3 層の境界
- ADR-0010: provenance の記録
- AC-G1 (core が domain コードを import しない) と AC-G2 (pack interface 経由の読み込み) は v0.1 に残す。

### v0.1 で扱いを緩めるもの

- UX layout: SDK の UI 部品として作り、SE pack 用の layout は pack 側のデータとして持つ。汎用の layout spec schema は確定しない。ADR-0003 は「方向は採用。schema は最初の実装から抽出して確定する」に弱める。
- algorithm: registry の仕組みと pack からの宣言読み込みは作るが、実装は learner model 1 つで足りる。policy と assessment strategy は v0.2。
- DNS の visualization と reality mapping (AC-C1〜C3) は v0.1 に含める。`START_CLAUDE_CODE.md` の slice には明記が無いが、abstraction → reality は中核の原則で、DNS 1 本で検証できるため。

### v0.2 以降のゲートに移すもの

- HTTP lab と DB indexing lab。ADR-0006 の 3 スキルは v0.2 のゲートになる。
- 適応診断と policy による出題選択 (AC-A1, AC-A2, AC-A4)
- AC-C4 (DB の visualization)
- layout spec の汎用化 (AC-H1)
- simulate / replay / report (AC-I 群)
- holdout (ADR-0011)
- ダミー第 2 pack (AC-G3)

### 実装計画

水平の Phase をやめ、slice 単位に書き直す。

- Slice 1: DNS 縦スライス (v0.1)
- Slice 2: 適応 loop (診断 + policy + 2 本目の lab)
- Slice 3: 3 本目の lab と評価コマンドと holdout
- Slice 4: layout spec の汎用化とダミー pack

Slice 2 以降の順序は Slice 1 の結果で見直してよい。

### 受入条件の文書

AC の ID と文言は変えず、各 AC に対象 version (v0.1 / v0.2 以降) を付ける。

## 却下した案

- 現状維持 (AC-A〜I 全てを v0.1 のゲートにする): 範囲が platform v1 相当で、loop を 1 度も回さないまま未確定の schema に依存する。
- 3 lab と適応 loop を v0.1 に残し、layout spec の汎用化と simulate/report だけ延期する: 縦に 1 本通す前に lab を 3 つ作ることになり、境界の誤りの発見が遅れる。
- 汎用基盤 (layout spec、registry、評価コマンド) を先に完成させる: 実例 1 つからの早すぎる一般化になる。

## 影響

- ADR-0003 と ADR-0006 の状態行を「一部改訂 (ADR-0012)」にする必要がある。
- 更新が必要な docs:
  - `docs/MVP.md`: v0.1 の範囲を DNS 縦スライスにし、HTTP / DB indexing の lab と適応 journey を v0.2 以降と明記する。
  - `docs/ACCEPTANCE_CRITERIA.md`: 各 AC に対象 version を付ける (ID と文言は変えない)。末尾の完了条件を version ごとに分ける。
  - `docs/IMPLEMENTATION_PLAN.md`: Phase 0〜6 を Slice 1〜4 に書き直す。
  - `START_CLAUDE_CODE.md`: First implementation target が v0.1 のゲートであること、AC-C1〜C3 を含むこと、それ以降の作業が Slice 2 以降であることを反映する。
- 関連 ADR: ADR-0003 (layout spec、一部改訂)、ADR-0004 (registry の v0.1 実装範囲)、ADR-0005 (評価コマンドは v0.2 以降)、ADR-0006 (3 スキルは v0.2 のゲート、一部改訂)、ADR-0007、ADR-0008、ADR-0009、ADR-0010 (v0.1 から守る契約)、ADR-0011 (holdout は v0.2 以降)。
