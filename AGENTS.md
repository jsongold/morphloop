# Agent Rules (morphloop)

## ローカル起動は per-worktree（docker compose 直打ち禁止）

このリポジトリは複数 worktree で並行開発する。`docker compose up` を直接実行すると、worktree 同士でコンテナ名・ネットワーク・DB volume・ホストポートが衝突する。

- SWE アプリ（web UI 含む）は別リポジトリ https://github.com/jsongold/browncircle にある。
- SDK 単体のスタック（db + 素の SDK api）の起動・停止・ログは `scripts/dev-stack.sh` を使う。
- このスクリプトは worktree パスから compose プロジェクト名 `morphloop-<hash>` とホストポート（api 17000+）を導出し、その worktree 専用のスタックを立てる。
- 主な使い方:
  - `./scripts/dev-stack.sh up [--fake-llm]`  # build + 起動（SDK api のみ）
  - `./scripts/dev-stack.sh logs [api|db]`
  - `./scripts/dev-stack.sh down [-v]` # 停止（-v で DB volume も削除）
- 別 worktree のスタック（他の `morphloop-*` プロジェクト）は `docker compose -p <project> ...` で触る。`docker compose` 単体ではこの worktree のスタックを指定できない。
- スクリプトの詳細・サブコマンドの正確な仕様は記憶から断言せず、`scripts/dev-stack.sh` の usage で確認する。