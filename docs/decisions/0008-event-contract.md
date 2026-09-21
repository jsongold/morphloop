# ADR-0008: event 契約 — 順序・原子性・冪等性・因果・再構築

- 状態: 採用
- 日付: 2026-09-21

## 文脈

`CLAUDE.md` 原則 5 は「append-only event から learner session を再構築できる」ことを要求し、`docs/DOMAIN_MODEL.md` の invariants は「LearnerSkillState は派生であり、evidence/events が source of truth」としている。`docs/ARCHITECTURE.md` の Persistence 節は、単一 PostgreSQL に append-only の `learning_events` を置き、現在状態は正規化テーブルに派生させるとしている。

外部レビューで、この構成について次が未定義だと指摘された: event 追記と派生状態更新の原子性、再送時の冪等性、同時更新の競合、timestamp 同一時の順序、causation/correlation、projection の再構築、event schema の移行。`docs/EVENT_SCHEMA.md` の envelope が持つ時刻は `occurred_at` だけで、これだけで時系列順を保証するのは弱い。完全な event sourcing か、監査ログ付き通常 DB かを決めるべき、という指摘である。

## 決定

- 位置付け: 完全な event sourcing framework (非同期 projection、CQRS) でも、監査ログ付き通常 DB でもない中間を取る。学習状態 (`LearnerSkillState`、session の再開状態、highlight、chat) は event から再構築できる派生状態 (projection) とする。単一 PostgreSQL、同期 projection。
- 順序:
  - `learning_events` に DB 採番の単調増加値 `position` を持たせる。timeline と再構築の順序は `position` で決める。
  - `occurred_at` は発生源の時刻とし、別に DB 記録時刻 `recorded_at` を持つ。`occurred_at` を順序に使わない。
  - 既知の注意: 並行 transaction では採番順と commit 順が一致しない場合がある。MVP は projection を同一 transaction 内で同期更新し、`position` を追尾する非同期 consumer を置かないので問題にならない。非同期 consumer を導入するときに再検討する。
- 原子性: event の追記と、それに対応する projection の更新は同一 DB transaction で行う。片方だけ成功する状態を作らない。
- 冪等性: client と terminal bridge が送る event は `idempotency_key` を持つ。同じ key の再送は新しい event を作らず、既存 event を返す。
- 競合: 同一 learner の learner-skill 更新は直列化する。具体的な手段 (行 lock 等) は実装時に決める。
- 因果: envelope に次の 2 つを追加する。
  - `causation_id`: この event を直接引き起こした event。
  - `correlation_id`: 1 つの attempt や 1 回の chat 往復など、関連する event の束。
  - 例: `evaluation.completed` → `evidence.created` → `learner_skill.updated` は `causation_id` で辿れる。AC-F3 (learner update audit) をこれで満たす。
- append-only の強制: application の規約だけに頼らず、DB レベルで `learning_events` への UPDATE / DELETE を禁止する。具体的な手段は実装時に決める。AC-F1 に対応する。
- 再構築: projection を捨てて event から作り直す rebuild 手段を harness が提供する。「rebuild した状態が稼働中の状態と一致する」ことをテストで確認する。
- schema 進化: 保存済み event は書き換えない。`event_version` を使い、読み取り時に upcast する。
- 大きな出力: `terminal.output` は chunk 化してよいが、各 chunk は event として参照可能であること (`docs/EVENT_SCHEMA.md` の既存記述を維持)。
- redaction: 永続化の前に redaction hook を通す、という既存 docs の要求は維持する。hook の仕様はこの ADR の範囲外で未確定。

## 却下した案

- 完全な event sourcing framework / CQRS / 非同期 projection: 単一 service・単一 DB の MVP には過剰で、`CLAUDE.md` の「複雑な orchestration を入れない」に反する。
- 監査ログ付き通常 DB (可変の状態を正とする): 「LearnerSkillState は派生で再構築可能」「mastery 更新は evidence から再現可能」という不変条件を満たせない。
- `occurred_at` による順序付け: 同一時刻と clock のずれで順序が定まらない。
- stream (session) 単位の連番のみ: session をまたぐ learner 状態の再構築に全体順序が要る。session 内の順序は `session_id` + `position` で得られる。

## 影響

- 更新が必要な docs:
  - `docs/EVENT_SCHEMA.md`: envelope に `position`、`recorded_at`、`idempotency_key`、`causation_id`、`correlation_id` を追加する。`occurred_at` は発生源の時刻で順序に使わないこと、`event_version` と読み取り時 upcast、`terminal.output` の chunk が event として参照可能であること、Storage rule が DB レベルで強制されることを明記する。
  - `docs/ARCHITECTURE.md`: Persistence 節に、同一 transaction での event 追記と projection 更新、`position` による順序、同一 learner の learner-skill 更新の直列化、rebuild 手段、採番順と commit 順に関する既知の注意を書く。
  - `docs/ACCEPTANCE_CRITERIA.md`: F 節で、AC-F1 を DB レベルの UPDATE / DELETE 禁止に、AC-F2 の「chronological order」を `position` 順に、AC-F3 を `causation_id` で辿れることに対応させる。冪等な再送と、rebuild した状態と稼働中の状態の一致を基準に加えるかを決める。
  - `docs/IMPLEMENTATION_PLAN.md`: Phase 1 の Deliver に `position`・`idempotency_key`・因果 ID を含む event store、同期 projection、rebuild 手段を加え、Exit に rebuild 一致のテストを加える。
  - `docs/DOMAIN_MODEL.md`: invariants の「LearnerSkillState is derived」が rebuild で検証されることを確認する。
- 関連 ADR: ADR-0002 (全 event が `pack_version` を記録する)、ADR-0003 (highlight・chat・pane state の永続化は harness 固定)、ADR-0005 (`replay` と session export は `position` 順の event を前提とする)、ADR-0007 (envelope の `attempt_id`、`correlation_id` が束ねる単位としての attempt)。
