# ADR-0011: 評価の外部アンカーと各評価手段の範囲

- 状態: 採用
- 日付: 2026-09-21

## 文脈

ADR-0005 は主指標を「target mastery 到達までの学習者時間/活動数」とした。2 つのレビューが同じ穴を指摘した。

mastery は pack 自身の learner model の推定値である。evaluator の rubric、learner model のパラメータ、閾値は全て pack が持つ (ADR-0002)。rubric や閾値を甘くすれば「効率が上がった」ように見える。推定値を早く上げる構成ほど良く見える循環があり、主指標が自己採点になっている。

あわせて、`simulate`、`replay`、実 session、golden set がそれぞれ何を測れて何を測れないかが明記されていなかった。

## 決定

### 外部アンカー

- pack の `eval/holdout/` に held-out 転移課題を置く。練習や診断では出題しない。練習課題とは異なる fixture (未知の故障) を使う。
- holdout の採点は決定的 check のみで行う。LLM evaluator と learner model の推定値を使わない。
- チューニングの比較期間中は holdout を凍結する。holdout を変えたら、それ以前の結果とは比較しない。holdout の同一性は ADR-0010 の content hash の考え方で確認する。hash の計算範囲の詳細は ADR-0010 で未確定のまま。
- 主指標を「holdout に合格するまでの学習者時間と活動数」に改める。「target mastery 到達」は主指標から外し、learner model の内部状態として扱う。
- 注意: holdout は一度受けると学習者にとって既知になる。同じ学習者に同じ holdout を繰り返し使うと汚染される。1 スキルにつき複数の holdout を用意し、使用済みを記録する。数と運用の詳細は未確定。

### 副指標

- 「mastery 推定精度」は、真の mastery が観測できないので定義できない。代わりに予測精度と較正で測る。learner model が出す「次の課題に成功する確率」と実際の成否を比べる。holdout の合否も予測対象に含める。
- hint 依存、最初の有効な行動までの時間は ADR-0005 のまま副指標とする。
- 遅延再テスト (定着) は今回は採用しない。将来の副指標候補として残す。

### 各手段が測れるものと測れないもの

| 手段 | 測れるもの | 測れないもの | 注意・制約 |
|---|---|---|---|
| `simulate` (合成学習者) | アルゴリズム構成の実装が正しく動くか、パラメータへの感度、policy の比較 (シミュレータの仮定の範囲内) | UX、実際の学習効果 | 合成学習者の生成モデルと learner model が同じ仮定を共有すると、シミュレータへの過適合になる。生成モデルは learner model と別の仮定で定義する |
| `replay` | model の構成を変えたときの推定の推移と予測精度 | policy の効果、UX | 範囲は ADR-0010 (保存済み evidence からの learner model 再計算) |
| 実 session | 主指標、UX の指標、副指標の全て | 同一スキルでの version 比較 (現時点) | 下記 |
| golden set (`eval/golden/`) | evaluator の回帰。signal の向き (positive/negative) と大小関係 | 数値の strength の厳密な一致は検証対象にしない | ラベルは人が付ける。ラベル付けの基準の詳細は未確定 |

- 実 session の制約: 現時点の実学習者はオーナー 1 人。同じ人は同じスキルを 2 度学べないので、同一スキルでの version 比較はできない。当面の UX チューニングは指標を参考にした主観判断になる。学習者が増えるか、別スキルで比較できるようになるまで、UX について統計的な主張はしない。
- `report` は上記を pack 単位でまとめる。どの数値がどの手段から来たかを必ず示す。

### 既存 ADR との関係

- ADR-0005 の主指標の定義と `replay` の説明を置き換える (`replay` は ADR-0010 と合わせて)。
- loop を人間が回すこと、評価資産を pack 内 `eval/` に置くこと、コマンド名が仮であることは有効のまま。

## 却下した案

- 主指標を target mastery 到達のままにする: 自己採点で、甘い構成が良く見える。
- 遅延再テストを主アンカーにする: 結果が出るまで数日〜数週間かかり、チューニングの loop が遅い。
- 人手でラベルした到達度を主アンカーにする: 学習者 1 人・評価者 1 人では基準が安定しない。
- 合成学習者の結果を学習効果の根拠にする: 実装検証には使えるが、学習効果の証明にはならない。

## 影響

- 未確定: 1 スキルあたりの holdout の数と使用済みの記録方法、holdout の同一性を確認する hash の計算範囲 (ADR-0010)、golden set のラベル付け基準。
- ADR-0005 の状態行を「一部置き換え (ADR-0010, ADR-0011)」にする必要がある。
- 更新が必要な docs:
  - `docs/LEARNING_LOOP.md`: Tuning and evaluation 節の主指標、副指標 (mastery 推定精度 → 予測精度と較正)、各手段の範囲。
  - `docs/SUBJECT_PACK.md`: pack 構造と Evaluation assets 節に `eval/holdout/` を追加する。`eval/golden/` の検証対象、`eval/learners/` の生成モデルの仮定。
  - `docs/ARCHITECTURE.md`: Evaluation commands の各コマンドの範囲と、`report` が数値の出所を示すこと。
  - `docs/ACCEPTANCE_CRITERIA.md`: I 節。AC-I1 は指標を「活動数」とだけ書いており ADR-0005 の「時間/活動数」と不一致なので、主指標の改定と合わせて直す。AC-I3 は数値の出所の明示を含める。
- 関連 ADR: ADR-0002 (調整値は全て pack が持つ)、ADR-0004 (`simulate` の対象は registry の構成)、ADR-0005、ADR-0010 (content hash と replay の範囲)。
