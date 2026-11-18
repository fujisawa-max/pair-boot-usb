# PAIR Developerで開発全体を進める

PAIR Developerは、調査、画像解析、UIと一般コードの実装、テスト、レビューを統括する親です。普段はこの親を選び、目的を伝えます。既存の調査用・UI用Coordinatorも、その用途に絞ったサンプルとして利用できます。

## 親の選び方

| 親：VS Code名／OpenCode ID | 用途 | 編集・コマンド実行 |
| --- | --- | --- |
| **PAIR Developer／pair-developer** | 調査から実装・検証まで進める。必要な専門の子を直接選ぶ | 親は計画・画面仕様を編集。実装は編集担当、検証コマンドはTesterへ委任 |
| PAIR Coordinator／pair-coordinator | 処理の流れや問題点を調査・報告する | 親子とも読み取り専用。コマンド実行なし |
| PAIR UI Coordinator／pair-ui-coordinator | ポンチ絵からのUI実装に絞る | 親は画面仕様を編集。UI担当がworktreeで実装。コマンドは利用者が実行 |

Developerから既存Coordinatorを呼ぶ三段構成にはせず、各専門の子を直接呼びます。親を選び直す必要も、子からさらに委任するための設定もありません。

## 設定する

1. [VS Code](vscode-pair-parallel-quick-start.md#1-モデルを登録する)／[OpenCode](opencode-pair-parallel-quick-start.md#1-モデル接続を設定する)のモデル接続を設定します。
2. [サンプル一覧](pair-agent-samples.md)から、利用するエディターのDeveloperと専門の子7種類をコピーします。フォルダ内の10ファイルをすべてコピーしても構いません。その場合は既存Coordinatorも選べます。同名ファイルがあれば比較して統合してください。
3. 新しいセッションで、VS Codeでは **PAIR Developer**、OpenCodeでは **pair-developer** を選びます。OpenCodeでは親のモデルもPAIR Codeにします。VS Codeのツール設定ではRun Subagentと、Testerが使うターミナル実行・出力取得ツールを確認します。

コピー先は開発対象プロジェクト内の `.github/agents/` または `.opencode/agents/` です。登録済みモデルの識別子は各クイックスタートに従って置き換えます。

## 普段の依頼

一般の修正なら、例えば次のように依頼します。

```text
設定ファイルが空の場合にアプリが落ちる原因を調べ、修正してテストしてください。
```

既存コードの構成を調べ、必要な場合はMapやReviewへ委任し、Implementerが修正、Testerが検証、Reviewが変更内容を確認します。調査だけを依頼した場合は、実装には進みません。小さい調査は親だけで回答する場合もあります。

UIなら同じ親へ次を依頼できます。

```text
design/sketch.pngをもとに、このプロジェクトの言語・UIフレームワークで画面を実装してください。
まず画面仕様をまとめ、確認後に実装とテストへ進めてください。
```

画像はImage、画面構造はUI Layout、外観はUI Styleへ委任します。UI仕様の確認、worktreeの準備・統合、画面の撮影は[ポンチ絵からの共通手順](pair-sketch-workflow.md)を使います。一般の処理やテストコードも変更する場合は、Implementerへ担当ファイルを渡します。

## 専門の子と作業範囲

| 子 | 担当 |
| --- | --- |
| Map | コードの流れと関連ファイルの調査 |
| Review | 問題点、変更内容、テスト不足の読み取り調査 |
| Image | 画像の解析、元の絵と完成画面の比較 |
| UI Layout | `.pair-worktrees/layout/` 内の指定ファイルでUI構造を実装 |
| UI Style | `.pair-worktrees/style/` 内の指定ファイルで外観を実装 |
| Implementer | 親が指定したプロジェクト内の一般コード・テストコードを実装 |
| Tester | 指定した作業フォルダで既存のビルド・テストを実行し、終了コードと結果を報告 |

一般コードの修正は、そのプロジェクト内で担当ファイルを指定して進められます。並行して編集する場合は担当ファイルを重複させず、必要ならworktreeで分けます。UI担当は既存サンプルと同じworktreeを使うため、未準備なら親が準備方法を案内します。同じファイルへの変更や前の結果が必要な処理は順番に行い、同時に実行する子は全体で最大2件にします。

Developer自身はコマンド実行・Git操作をしません。worktree作成、commit、統合は利用者が行います。統合後にDeveloperへ続行を依頼すると、Testerへ検証を委任できます。親が切り替わるだけで、ファイル編集・画像受渡し・GPUでの並行処理が自動的に確認済みになるわけではありません。子の記録とPAIR Jobsで確認します。

## テスト実行と結果の確認

Testerだけに検証コマンドの実行機能を追加しています。VS Codeでは `execute/runInTerminal` と `execute/getTerminalOutput`、OpenCodeでは `shell` 権限を使います。OpenCodeのサンプルはコマンド実行を `ask` とし、実行時の承認を求めます。必要なら利用するプロジェクトの承認設定に合わせて調整してください。[VS Codeの実行ツール](https://code.visualstudio.com/docs/agents/reference/tools-reference#run-commands-and-diagnose-tests)、[OpenCodeの権限](https://opencode.ai/docs/permissions/)

Implementerの編集権限は一般コードへ対応するため広く設定しており、担当ファイルの限定は親子への指示と差分確認で行います。Testerは編集ツールを持ちませんが、コマンドはビルド出力等を作ることがあります。どちらも、親が渡した対象とプロジェクトの開発手順に従います。

検証に失敗した場合は、親が原因と担当を判断して修正・再検証を依頼します。同じ問題が2回の修正でも解消しなければ、その時点の状況と次の対応を報告します。SDK不足、承認されず未実行、スキップ、対象OSでの画面未確認は成功と分けて報告させます。端末コマンドを起動しただけでは、テスト完了としません。
