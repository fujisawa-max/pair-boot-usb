# VS Code／OpenCode用エージェント定義のサンプル

クイックスタートに掲載した設定を、コピーして使えるファイルとして用意しています。[VS Codeの手順](vscode-pair-parallel-quick-start.md)／[OpenCodeの手順](opencode-pair-parallel-quick-start.md)でモデル接続を設定した後、必要な定義を**開発対象のプロジェクト**へコピーしてください。

## サンプルの場所とコピー先

| エディター | このリポジトリのサンプルフォルダ | 開発対象プロジェクト内のコピー先 |
| --- | --- | --- |
| VS Code | `docs/examples/pair-agents/vscode/agents/` | `.github/agents/` |
| OpenCode v2 | `docs/examples/pair-agents/opencode/agents/` | `.opencode/agents/` |

コピー先のフォルダがなければ作成します。同名ファイルがある場合は、内容を比較して必要な項目を統合してください。このリポジトリのフォルダをエディターへ登録する設定ではなく、作業するプロジェクトに定義を置く方式です。USBや推論PCへコピーする必要はありません。

## 用意した定義

読み取り調査だけを試す場合は最初の3つ、画像からUIを実装する場合は後半の4つを使います。

| 役割 | VS Code | OpenCode |
| --- | --- | --- |
| 処理の流れを調べる子 | [pair-map.agent.md](examples/pair-agents/vscode/agents/pair-map.agent.md) | [pair-map.md](examples/pair-agents/opencode/agents/pair-map.md) |
| 問題点を調べる子 | [pair-review.agent.md](examples/pair-agents/vscode/agents/pair-review.agent.md) | [pair-review.md](examples/pair-agents/opencode/agents/pair-review.md) |
| 調査を分担する親 | [pair-coordinator.agent.md](examples/pair-agents/vscode/agents/pair-coordinator.agent.md) | [pair-coordinator.md](examples/pair-agents/opencode/agents/pair-coordinator.md) |
| 画像を読む子 | [pair-image.agent.md](examples/pair-agents/vscode/agents/pair-image.agent.md) | [pair-image.md](examples/pair-agents/opencode/agents/pair-image.md) |
| 画面構造を実装する子 | [pair-ui-layout.agent.md](examples/pair-agents/vscode/agents/pair-ui-layout.agent.md) | [pair-ui-layout.md](examples/pair-agents/opencode/agents/pair-ui-layout.md) |
| 外観・テーマを実装する子 | [pair-ui-style.agent.md](examples/pair-agents/vscode/agents/pair-ui-style.agent.md) | [pair-ui-style.md](examples/pair-agents/opencode/agents/pair-ui-style.md) |
| UI実装を分担する親 | [pair-ui-coordinator.agent.md](examples/pair-agents/vscode/agents/pair-ui-coordinator.agent.md) | [pair-ui-coordinator.md](examples/pair-agents/opencode/agents/pair-ui-coordinator.md) |

## 自分の環境に合わせる

1. モデル接続を先に用意します。VS Codeの `PAIR Code`／`PAIR Vision` は登録したモデルの識別子に、OpenCodeの `pair/code`／`pair/vision` は設定したprovider/modelに合わせます。接続先・APIのモデルID・トークン数の設定は各クイックスタートを参照してください。
2. 定義をコピーした後、新しいセッションで調査用またはUI用のCoordinatorを選びます。VS CodeではRun Subagent等のツールも有効にします。OpenCodeでは親の選択モデルも確認します。
3. 調査の確認には[共通の調査用サンプル](pair-client-sample.md)を使えます。UI実装では[ポンチ絵からの共通手順](pair-sketch-workflow.md)に従い、画像・仕様・worktreeを用意します。

UIの言語・フレームワーク・対象OSは依頼文で指定できます。既存アプリなら、そのプロジェクトの環境を使うよう依頼します。担当パスは `design/ui-spec.md` で決め、worktreeの場所を変える場合は本文とOpenCodeの編集権限も合わせて変更します。構造と外観を同じファイルへ書く環境では、親が編集順序を決めます。

```text
design/sketch.pngをもとに、C#／WPFでWindows向けの画面を実装してください。
```

サンプルの親子にはコマンド実行やGit操作を許可していません。worktree作成、差分の確認・統合、アプリのビルド・実行は共通手順に従って利用者が行います。子の呼び出し記録とPAIR Jobsで、画像の読み取り、使用モデル、実行ノード、並行処理を確認してください。
