# ADR-0002: チューニングはコンテンツ単位、調整値は全て pack が持つ

- 状態: 採用
- 日付: 2026-09-21

## 文脈

`docs/PRODUCT.md` は「学習者時間あたりの改善最大化」を掲げており、そのために UX と学習アルゴリズムを継続的に調整する必要がある。「チューニング」が何を指すか、調整値をどこに置くかが未定義だった。

domain ごとに最適な UX と学習アルゴリズムは異なる。例: SE pack は terminal 主画面 + BKT 系、English pack は音声主画面 + 別の retention モデル。

event には既に `pack_version` が記録される (`docs/EVENT_SCHEMA.md`)。

## 決定

- 「チューニング」とは、コンテンツごとの UX と学習アルゴリズムの最適化である。harness 全体で共通のパラメータを調整することではない。
- 調整値は全て pack が持つ。harness は既定値を持たない。
- pack は純データなので、チューニング = pack version を上げること、と定義する。
- event に記録される `pack_version` を使い、version 間比較をそのままチューニングの評価とする。
- config 用の別 version 体系は作らない。

## 却下した案

- harness 既定値 + pack override の二層: 実効値が二箇所に分散し、`pack_version` だけでは挙動を再現・比較できなくなる。domain 間で共有できる妥当な既定値も想定しにくい。
- harness 既定値のみで pack 上書き不可: domain ごとに最適解が異なるという前提に反する。

## 影響

- pack は必要な調整値を全て宣言しなければならない。欠けている場合の扱い (validation の詳細) は未確定。
- 更新が必要な docs:
  - `docs/SUBJECT_PACK.md`: pack が UX 宣言とアルゴリズム宣言・全パラメータを持つこと、version を上げることがチューニングであること。
  - `docs/LEARNING_LOOP.md`: 式や model のパラメータが harness 既定値ではなく pack 宣言であること。
  - `docs/ARCHITECTURE.md`: harness が既定値を持たないこと。
  - `docs/EVENT_SCHEMA.md`: `pack_version` が version 間比較の軸であることの明記。
- 関連 ADR: ADR-0001 (pack の所在)、ADR-0003 (UX の宣言)、ADR-0004 (アルゴリズムの宣言)、ADR-0005 (version 間比較による評価)。
