# ADR-0009: 拡張境界は core / domain adapter / pack の 3 層にする

- 状態: 採用
- 日付: 2026-09-21

## 文脈

ADR-0004 は「pack はコードを持たない。新しい実装は harness に追加する」とした。レビューで次の 2 点が指摘された。

1. domain 固有コードの置き場が決まっていない。`docs/SUBJECT_PACK.md` の Evaluator definition は `service_recovered` や `correct_resolver_config` という check 名を参照するが、それを実行するコードの所在が未定である。DNS lab の生成、DB fixture、将来の発音評価も純データでは実現できない。全部を harness に入れると harness が domain 固有実装の集積所になり、core の domain 非依存 (`CLAUDE.md` 原則 8、AC-G1) と衝突する。
2. 「pack は純データ」の主張が漏れている。pack の `environments/` に入る image 定義、command 配列、seed データは実行されるものである。

`CLAUDE.md` の Engineering rules は `core`、`subject packs`、`tool adapters`、UI の interface を保つことを求めているが、tool adapter 以外の domain 固有コード (check、fixture) の層は定義されていない。

## 決定

### 3 層

- core: domain 非依存。loop、event、registry の仕組み、pack loader。
- domain adapter: domain 固有のコード。同一リポジトリ内で core とは別パッケージとし、version を持つ。core は domain adapter を import しない。依存の向きは adapter → core の interface のみ。
- pack: データ。コードを持たない (ADR-0004 を維持)。

### domain adapter が持つもの

- 決定的 check の実装 (例: `service_recovered`, `correct_resolver_config`)。
- environment fixture provider (lab の起動・reset・破棄の domain 固有部分)。
- tool adapter (terminal, database 等。`docs/SUBJECT_PACK.md` の Domain-specific adapters に挙がっているもの)。

### 参照と登録

- pack は adapter の機能を名前で参照する。pack の manifest は、必要とする adapter とその version 範囲を宣言する。
- harness は pack 読み込み時に、参照された check / fixture / tool が登録済みかを検証し、無ければ読み込みを拒否する。
- 登録の仕組みは ADR-0004 の algorithm registry と同じ考え方にする (名前 + version で登録し、pack が名前で指定する)。registry を種類ごとに分けるか 1 つにまとめるかは実装時に決める。
- 汎用のアルゴリズム実装 (learner model / policy / assessment) は domain 非依存なので core 側の registry に置く。domain 固有のアルゴリズムが必要になった場合は domain adapter に置く。

### 実行境界

- pack に由来して実行されるもの (container image の定義、`command_exit` の command 配列、seed データ、fixture の設定) は learner 用 sandbox の中でのみ実行する。host 上で pack 由来の文字列を shell に渡さない。
- domain adapter のコードは harness の一部として host 側で動く。これは review 対象のコードであり、pack ではない。
- prompt は pack に置ける。LLM 出力が学習者状態に影響する場合は schema 検証を通す (既存規則の再確認)。
- 「pack は純データ」の意味を次のとおり定義し直す: pack は harness の process 内で実行されるコードを含まない。sandbox 内で実行される定義は含みうる。

### AC-G1 の対象

- AC-G1 の対象は core とする。core が domain adapter と pack のどちらも import しないことを検査する。

## 却下した案

- pack がコードを持つ: ADR-0004 の撤回になり、pack の安全性・互換性管理が重くなる。チューニングで頻繁に編集するものにコードを混ぜたくない。
- 外部 plugin パッケージとして配布し動的に読む: 分離は最も強いが、MVP には配布と互換性管理の仕組みが過剰である。3 層の境界を守っておけば後から切り出せる。
- domain 固有コードを core に置く (現状の暗黙の帰結): core が domain 実装の集積所になり、domain 非依存の原則が崩れる。

## 影響

- 未確定: domain adapter を配置するディレクトリ名 (`docs/ARCHITECTURE.md` の既存 `labs/` との関係を含む)。docs 反映時に決める。registry の分割方針は実装時に決める。
- ADR-0004 の状態行を「一部補足 (ADR-0009)」にする必要がある。「新しい実装は harness に追加する」は、domain 非依存なら core、domain 固有なら domain adapter、と読み替える。
- 更新が必要な docs:
  - `docs/ARCHITECTURE.md`: Modules (domain adapter 層の追加と `labs/` の整理)、Logical components の図、Harness fixed vs pack declared 表 (adapter 層の位置付けと「純データ」の再定義)。
  - `docs/SUBJECT_PACK.md`: `manifest.yaml` (必要な adapter と version 範囲の宣言)、Evaluator definition (check 名が adapter の登録名を指すこと)、Domain-specific adapters (check と fixture provider を含む層として書き直す)。
  - `CLAUDE.md`: Engineering rules の境界記述 (core / domain adapter / pack / UI) と、pack 由来の実行物を sandbox 内に限る規則。
  - `docs/ACCEPTANCE_CRITERIA.md`: AC-G1 (対象は core。domain adapter と pack のどちらも import しない)。
- 関連 ADR: ADR-0001 (harness は `contents/` を import しない)、ADR-0004 (registry と「pack はコードを持たない」)、ADR-0006 (ダミー pack による core 独立性の検査)、ADR-0007 (`EnvironmentDefinition` / `EvaluatorDefinition` は pack 内定義)、ADR-0010 (domain adapter の version を provenance に記録する)。
