# VS Code／OpenCode用エージェント定義のサンプル

クイックスタートに掲載した設定を、コピーして使えるファイルとして用意しています。[VS Codeの手順](vscode-pair-parallel-quick-start.md)／[OpenCodeの手順](opencode-pair-parallel-quick-start.md)でモデル接続を設定した後、必要な定義を**開発対象のプロジェクト**へコピーしてください。

## サンプルの場所とコピー先

| エディター | このリポジトリのサンプルフォルダ | 開発対象プロジェクト内のコピー先 |
| --- | --- | --- |
| VS Code | `docs/pair-usage/examples/pair-agents/vscode/agents/` | `.github/agents/` |
| OpenCode v2 | `docs/pair-usage/examples/pair-agents/opencode/agents/` | `.opencode/agents/` |

コピー先のフォルダがなければ作成します。同名ファイルがある場合は、内容を比較して必要な項目を統合してください。このリポジトリのフォルダをエディターへ登録する設定ではなく、作業するプロジェクトに定義を置く方式です。

## 用意した定義

普段の開発全体を任せる場合は **PAIR Developer／pair-developer** を選びます。[3種類の親の違いと利用手順](pair-development-workflow.md)を参照してください。従来の調査用・UI用の親も保持しています。

| 選ぶ親 | 必要な定義 |
| --- | --- |
| Developer | DeveloperとMap・Review・Image・UI Layout・UI Style・Implementer・Testerの計8ファイル |
| Coordinator | CoordinatorとMap・Reviewの計3ファイル |
| UI Coordinator | UI CoordinatorとImage・UI Layout・UI Styleの計4ファイル |

各エディターの10ファイルをすべてコピーすると、3種類の親を選べます。

| 役割 | VS Code | OpenCode |
| --- | --- | --- |
| 開発全体を統括する親 | [pair-developer.agent.md](examples/pair-agents/vscode/agents/pair-developer.agent.md) | [pair-developer.md](examples/pair-agents/opencode/agents/pair-developer.md) |
| 処理の流れを調べる子 | [pair-map.agent.md](examples/pair-agents/vscode/agents/pair-map.agent.md) | [pair-map.md](examples/pair-agents/opencode/agents/pair-map.md) |
| 問題点を調べる子 | [pair-review.agent.md](examples/pair-agents/vscode/agents/pair-review.agent.md) | [pair-review.md](examples/pair-agents/opencode/agents/pair-review.md) |
| 調査を分担する親 | [pair-coordinator.agent.md](examples/pair-agents/vscode/agents/pair-coordinator.agent.md) | [pair-coordinator.md](examples/pair-agents/opencode/agents/pair-coordinator.md) |
| 画像を読む子 | [pair-image.agent.md](examples/pair-agents/vscode/agents/pair-image.agent.md) | [pair-image.md](examples/pair-agents/opencode/agents/pair-image.md) |
| 画面構造を実装する子 | [pair-ui-layout.agent.md](examples/pair-agents/vscode/agents/pair-ui-layout.agent.md) | [pair-ui-layout.md](examples/pair-agents/opencode/agents/pair-ui-layout.md) |
| 外観・テーマを実装する子 | [pair-ui-style.agent.md](examples/pair-agents/vscode/agents/pair-ui-style.agent.md) | [pair-ui-style.md](examples/pair-agents/opencode/agents/pair-ui-style.md) |
| UI実装を分担する親 | [pair-ui-coordinator.agent.md](examples/pair-agents/vscode/agents/pair-ui-coordinator.agent.md) | [pair-ui-coordinator.md](examples/pair-agents/opencode/agents/pair-ui-coordinator.md) |
| 一般コード・テストコードを実装する子 | [pair-implementer.agent.md](examples/pair-agents/vscode/agents/pair-implementer.agent.md) | [pair-implementer.md](examples/pair-agents/opencode/agents/pair-implementer.md) |
| ビルド・テストを実行する子 | [pair-tester.agent.md](examples/pair-agents/vscode/agents/pair-tester.agent.md) | [pair-tester.md](examples/pair-agents/opencode/agents/pair-tester.md) |

## 自分の環境に合わせる

1. モデル接続を先に用意します。VS Codeの `PAIR Code (customendpoint)`／`PAIR Vision (customendpoint)` は登録した表示名・提供元に合わせた値に、OpenCodeの `pair/code`／`pair/vision` は設定したprovider/modelに合わせます。接続先・APIのモデルID・トークン数の設定は各クイックスタートを参照してください。
2. 定義をコピーした後、新しいセッションでDeveloper、調査用Coordinator、UI用Coordinatorのいずれかを選びます。VS CodeではRun Subagent等のツールも有効にします。OpenCodeでは親の選択モデルも確認します。
3. 調査の確認には[共通の調査用サンプル](pair-client-sample.md)を使えます。UI実装では[ポンチ絵からの共通手順](pair-sketch-workflow.md)に従い、画像・仕様・worktreeを用意します。

UIの言語・フレームワーク・対象OSは依頼文で指定できます。既存アプリなら、そのプロジェクトの環境を使うよう依頼します。担当パスは `design/ui-spec.md` で決め、worktreeの場所を変える場合は本文とOpenCodeの編集権限も合わせて変更します。構造と外観を同じファイルへ書く環境では、親が編集順序を決めます。

```text
design/sketch.pngをもとに、C#／WPFでWindows向けの画面を実装してください。
```

DeveloperはTesterへビルド・テストの実行を依頼できます。従来のCoordinator・UI Coordinatorとその担当は、コマンド実行なしの設定を維持しています。worktree作成とGitでの差分確認・統合は利用者が行います。テスト実行時の承認と作業範囲は[Developerの手順](pair-development-workflow.md#テスト実行と結果の確認)を参照してください。子の呼び出し記録とPAIR Jobsで、画像の読み取り、使用モデル、実行ノード、並行処理を確認します。
