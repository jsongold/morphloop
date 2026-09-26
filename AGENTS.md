# Agent Rules (morphloop)

## ローカル起動は per-worktree（docker compose 直打ち禁止）

このリポジトリは複数 worktree で並行開発する。`docker compose up` を直接実行すると、worktree 同士でコンテナ名・ネットワーク・DB volume・ホストポートが衝突する。

- SWE アプリ（db / api / web）は `cd apps/swe && ./scripts/dev.sh up [--fake-llm]`（プロジェクト名 `morphloop-swe-<hash>`、api 18000+ / web 13000+）。E2E は `./scripts/dev.sh e2e`、停止は `./scripts/dev.sh down [-v]`。詳細は `apps/swe/README.md`。
- SDK 単体のスタック（db + 素の SDK api）の起動・停止・ログ・pack import・e2e は `scripts/dev-stack.sh` を使う。
- このスクリプトは worktree パスから compose プロジェクト名 `morphloop-<hash>` とホストポート（api 17000+）を導出し、その worktree 専用のスタックを立てる。
- 主な使い方:
  - `./scripts/dev-stack.sh up`       # build + 起動 + pack import
  - `./scripts/dev-stack.sh import`   # pack を import（既定 contents/software-engineering）
  - `./scripts/dev-stack.sh logs [api|db]`
  - `./scripts/dev-stack.sh down [-v]` # 停止（-v で DB volume も削除）
  - `./scripts/dev-stack.sh test`      # e2e テストを api コンテナ内で実行
- 別 worktree のスタック（他の `morphloop-*` プロジェクト）は `docker compose -p <project> ...` で触る。`docker compose` 単体ではこの worktree のスタックを指定できない。
- スクリプトの詳細・サブコマンドの正確な仕様は記憶から断言せず、`scripts/dev-stack.sh` の usage で確認する。