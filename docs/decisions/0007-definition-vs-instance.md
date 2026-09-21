# ADR-0007: Definition (pack 内定義) と Runtime instance (実行記録) を名前と ID で分ける

- 状態: 採用
- 日付: 2026-09-21

## 文脈

`docs/DOMAIN_MODEL.md` は Activity / Assessment / Environment / Evaluation 等を、pack 内の定義と session 内で実行される実体の両方の意味で使っている。`docs/SUBJECT_PACK.md` の activity は `diagnose-dns-resolver-failure-v1` のような pack 内 ID を持つ一方、`docs/EVENT_SCHEMA.md` の envelope は `activity_id: "act_..."` を持ち、`docs/ARCHITECTURE.md` の API sketch の `/activities/{id}` はどちらを指すか決まっていない。

外部レビューで、この混在により再試行・履歴・pack 更新・同一課題の複数実行で ID の意味が崩れると指摘された。pack は versioned であり (ADR-0001, ADR-0002)、historical session は使った pack version を指す必要があるため、定義と実行記録の区別を先に固定する。

## 決定

- 2 種類を名前で分ける。
  - Definition: pack 内の versioned データ。`SkillDefinition` / `ActivityDefinition` / `EnvironmentDefinition` (fixture) / `AssessmentDefinition` / `EvaluatorDefinition` (rubric) / `VisualizationDefinition` / `TargetProfile`。pack 内 ID (例 `diagnose-dns-resolver-failure-v1`) で識別し、完全修飾は `pack_id` + `pack_version` + definition id とする。
  - Runtime instance: DB 上の実行記録。`LearningSession` (`ses_`) / `AssessmentRun` (`asr_`) / `ActivityAttempt` (`att_`) / `LabInstance` (`lab_`) / `Evaluation` (`evl_`) / `Evidence` (`ev_`)。各 instance は元になった definition id と `pack_version` を必ず参照する。
- 関係:
  - 1 つの `ActivityDefinition` に対し `ActivityAttempt` は複数 (再試行・別 session)。
  - 1 つの `ActivityAttempt` に対し `LabInstance` は複数。lab reset は同じ attempt のまま新しい `LabInstance` を作る。
  - `Evaluation` は 1 つの `ActivityAttempt` に属し、`Evidence` を生む。
- event envelope: 現在の `activity_id` を `attempt_id` (`att_...`) に改め、`activity_definition_id` を併記する。
- API sketch の `/activities/{id}` 系の `{id}` は attempt id を指す。route 名の変更はこの ADR では決めず、docs 反映時に決める。
- `skill_id` は pack version をまたいで安定した識別子とする。`LearnerSkillState` は learner + `pack_id` + `skill_id` で識別する。skill の意味が変わる変更は新しい `skill_id` を発行し、旧 ID を再利用しない。
- core のコードは definition を pack loader 経由の読み取り専用データとして扱う。DB に definition のコピーを正として持たない (参照用キャッシュは可)。

## 却下した案

- 現状維持 (Activity を定義と実行の両方に使う): 再試行と pack 更新で ID の意味が崩れる。
- definition を DB に取り込んで正にする: pack が正という ADR-0001 / ADR-0002 と二重管理になる。
- attempt ごとに definition をスナップショットして埋め込む: provenance は別 ADR (ADR-0010 予定) で pack content hash により担保するので不要。

## 影響

- 更新が必要な docs:
  - `docs/DOMAIN_MODEL.md`: Core entities を Definition と Runtime instance に分けて改名し、Relationships を上記の関係 (`ActivityDefinition` 1─* `ActivityAttempt` 1─* `LabInstance` 等) に書き換える。`LearnerSkillState` の識別子に `pack_id` を加える。invariants に definition 参照と `skill_id` 再利用禁止を追加する。
  - `docs/EVENT_SCHEMA.md`: envelope の `activity_id: "act_..."` を `attempt_id: "att_..."` に置き換え、`activity_definition_id` を併記する。`act_` を使う他の例も置き換える。
  - `docs/LEARNING_LOOP.md`: `"activity_id": "act_123"` を使う例を同様に置き換える。
  - `docs/ARCHITECTURE.md`: API sketch の `/activities/{id}` 系が attempt id を指すことを明記する。route 名を変えるかはここで決める。core が definition を pack loader 経由の読み取り専用で扱う規則を追記する。
  - `docs/SUBJECT_PACK.md`: pack 内の各 `id` が definition id であること、完全修飾の形、`skill_id` の安定性と再利用禁止を明記する。
  - `docs/ACCEPTANCE_CRITERIA.md`: AC-B2 等の「activity ID」表現を attempt id (および definition id) に改める。
- 関連 ADR: ADR-0001 / ADR-0002 (pack が正、definition を DB に正として持たない根拠)、ADR-0005 (pack version をまたぐ replay・比較が `skill_id` の安定性に依存する)、ADR-0010 予定 (pack content hash による provenance)。
