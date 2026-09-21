# ADR-0006: MVP のスキル範囲は lab 付き 3 スキル

- 状態: 採用 (一部改訂: ADR-0012)
- 日付: 2026-09-21

## 文脈

`docs/MVP.md` の Learning scope は DNS resolution、TCP、HTTP request/response lifecycle、basic indexing、`EXPLAIN` を挙げているが、lab fixture は Lab A (broken DNS resolver) と Lab B (slow indexed lookup) の 2 つしか定義していない。

AC-A1 は診断完了時に 3 スキル以上の mastery 推定を要求する。lab が 2 つでは、practice-first 原則 (`CLAUDE.md` 原則 1、および「実践課題を選択式クイズで置き換えない」規則) を崩さずに 3 スキルを診断できない。

## 決定

- MVP は lab 付き 3 スキルとする: DNS resolution、HTTP request/response lifecycle、DB indexing/EXPLAIN。
- TCP は延期する。
- HTTP 用の lab を 1 つ追加定義する。lab の具体的な内容は未確定。
- これにより AC-A1 を practice-first 原則を崩さずに満たす。
- AC-G3 のダミー非 SW pack は `contents/` ではなく harness の test fixture に置く。

## 却下した案

- 4 スキル全てを lab 付きにする: lab 作成コストが最大になる。
- TCP・HTTP を lab なしの predict/explain 課題で診断する: practice-first に反する方向である。

## 影響

- 更新が必要な docs:
  - `docs/MVP.md`: Learning scope から TCP を外して延期と明記し、HTTP 用の lab を Required lab fixtures に追加する。
  - `docs/LEARNING_LOOP.md`: target 例の `network.tcp.connection` と policy 出力例の TCP skill の扱いを見直す。
  - `docs/ACCEPTANCE_CRITERIA.md`: AC-A1 を満たす 3 スキルの明記、AC-G3 のダミー pack の置き場所。
  - `docs/IMPLEMENTATION_PLAN.md`: HTTP lab の追加と TCP の延期を反映する。
  - `docs/SUBJECT_PACK.md`, `docs/UX.md`: TCP を使った例は例示として残せるが、MVP 範囲外であることを確認する。
- 関連 ADR: ADR-0001 (`contents/` と test fixture の区別)、ADR-0004 (MVP で必要な registry 実装の範囲)、ADR-0005 (3 スキルに対する評価資産)。
