# ADR 索引

このディレクトリの ADR (Architecture Decision Record) が設計決定の正本である。実装・他ドキュメントと矛盾する場合は ADR を優先する。

後の ADR が前の ADR を置き換え・改訂している箇所は、後の ADR が優先する。各 ADR の状態行に置き換え・改訂元の ADR 番号を記載している。

| 番号 | タイトル | 状態 | 要旨 |
|---|---|---|---|
| 0001 | リポジトリは Harness (SDK)、コンテンツは contents/ に置く | 採用 | このリポジトリはエンジン (SDK) であり、subject pack は `contents/<pack-id>/` に置き harness はそれを import しない |
| 0002 | チューニングはコンテンツ単位、調整値は全て pack が持つ | 採用 (一部置き換え: ADR-0010) | チューニング = pack version を上げること。調整値は harness ではなく全て pack が持つ |
| 0003 | UX は pack の宣言的 layout spec で決める | 採用 (一部改訂: ADR-0012) | UI 部品は SDK が提供し、配置・mode・遷移・文言は pack が宣言する |
| 0004 | 学習アルゴリズムは harness の registry + pack の宣言 | 採用 (一部補足: ADR-0009) | learner model / policy / assessment の実装は harness の registry に置き、pack は名前とパラメータのみ宣言する |
| 0005 | 評価基盤と最適化目標 | 採用 (一部置き換え: ADR-0010, ADR-0011) | 主指標は学習効率、評価コマンドは simulate/replay/report、評価資産は pack 内 `eval/` に置く |
| 0006 | MVP のスキル範囲は lab 付き 3 スキル | 採用 (一部改訂: ADR-0012) | MVP は DNS resolution・HTTP・DB indexing の 3 スキルを lab 付きで診断し、TCP は延期する |
| 0007 | Definition (pack 内定義) と Runtime instance (実行記録) を名前と ID で分ける | 採用 | pack 内の versioned な Definition と DB 上の Runtime instance (attempt/session 等) を分離する |
| 0008 | event 契約 — 順序・原子性・冪等性・因果・再構築 | 採用 | `position` による順序、同一 transaction での projection 更新、idempotency key、rebuild 可能な projection を定める |
| 0009 | 拡張境界は core / domain adapter / pack の 3 層にする | 採用 | domain 固有コード (決定的 check、fixture provider、tool adapter) は core でも pack でもなく domain adapter 層に置く |
| 0010 | Provenance の記録と replay の範囲 | 採用 | 再現に必要な情報一式を event に記録し、pack content hash を同一性の正とする。replay は Level 1 (learner model 再計算) に限る |
| 0011 | 評価の外部アンカーと各評価手段の範囲 | 採用 | 主指標を held-out 転移課題の合格までの学習者時間/活動数に改め、learner model 自身の mastery 推定値を成功指標にしない |
| 0012 | v0.1 の受入ゲートを DNS 縦スライスに絞る | 採用 | v0.1 のゲートは DNS 1 本の縦スライスに限定し、汎用基盤 (layout spec 汎用化、policy/assessment、評価コマンド) は v0.2 以降に回す |
