# ポンチ絵から画面を実装する共通手順

エディター別の定義ファイルは、[コピー用サンプルと配置方法](pair-agent-samples.md)から取得できます。画像担当・画面構造担当・外観担当を、PAIR DeveloperまたはUI Coordinatorから呼びます。Developerは一般コード・テスト・レビューも含む統括用、UI CoordinatorはUI実装専用です。[親の使い分け](pair-development-workflow.md#親の選び方)も参照してください。

[VS Code](vscode-pair-parallel-quick-start.md#5-ポンチ絵から画面を実装する)／[OpenCode](opencode-pair-parallel-quick-start.md#4-ポンチ絵から画面を実装する)の画像・編集エージェントを使う手順です。設定や画像は**開発対象のプロジェクト**に置きます。依頼した言語・UIフレームワーク・対象OSに合わせて画面を設計・実装します。デスクトップ、モバイル、Webのいずれにも同じ役割分担を使えます。

## 1. 実装環境と画像を用意する

開発対象のプロジェクトで `design/` を作り、自分のポンチ絵を `design/sketch.png` として保存します。画像モデルを使うため、この例ではPNGを使います。親への画像添付だけに頼らず、子からも読めるファイルを用意してください。

単独のPAIR Visionセッションで画像を添付し、文字や配置を読めることを先に確認します。次にエディター別手順で必要な定義をコピーします。UI Coordinatorなら親子4つ、Developerなら専門の子も含む8つを用意します。選んだ親へ、例えば次を依頼します。

```text
design/sketch.pngをもとに画面を実装してください。
Python 3とPySide6で、Windows向けデスクトップアプリにしてください。
既存プロジェクトの依存関係とディレクトリ構成に合わせてください。
まず画面仕様をdesign/ui-spec.mdへまとめ、確認できるようにしてください。
```

既存アプリなら「このプロジェクトの言語・UIフレームワークを使ってください」だけでも構いません。親がプロジェクト設定と既存の画面コードを調べ、使用する環境を仕様へ記録します。新規アプリでは「C#／WPF・Windows」「Dart／Flutter・Android」のように指定します。必要な環境が未指定で既存コードからも決まらない場合は、親が確認してから進めます。以降の短い依頼では、保存した仕様を引き継ぎます。

親は画像担当へ解析を依頼し、結果を `design/ui-spec.md` に保存します。この段階では画面のコードを書きません。子の記録で画像読み取りとPAIR Visionの使用を確認し、画像内の実際の文言や配置と回答を照合します。画像パスを知っているだけ、ファイルを見つけただけでは画像を読めたことにはなりません。

画像を子へ渡せない場合は、単独のPAIR Visionセッションへ画像を添付し、読み取った仕様を文章で親へ渡してください。見えていない画像の内容を推測させて実装へ進まないようにします。

## 2. 仕様と担当範囲を決める

仕様には、言語・UIフレームワークと使用する版、対象OS、既存アプリへの組込み方、画面の構成、文言、色・余白・配置、ウィンドウサイズや画面の向きを変えた場合の表示、確認方法をまとめます。画像から読み取れた事実と、実装上の提案、確認が必要な点を分けます。絵だけでは分からないボタンの動作やAPIは、必要なものだけ利用者へ確認します。

並行編集の前に、各担当のファイルと、共有する部品名・テーマの値・参照方法も保存します。例えばPython／PySide6でヘッダー・サイドバー・カード一覧の画面を作る場合は、次のようにします。これは画像からの解析結果そのものではなく、分担のための記載例です。

```text
環境: Python 3 / PySide6、対象OS: Windows
使用する版と起動方法: 既存プロジェクトの依存定義・起動コードに合わせる。
新規なら、Python/PySide6の版、エントリーポイント、起動コマンドも確定する。
画面構造担当: src/ui/window.py
外観担当: src/ui/theme.py
構造の作業先: .pair-worktrees/layout/src/ui/window.py
外観の作業先: .pair-worktrees/style/src/ui/theme.py
theme.pyはAPP_STYLESHEETという文字列を公開し、window.pyから参照する。
配置はQtのレイアウトで組み、外観はQt Style Sheetsで指定する。
objectName: app、header、sidebar、main、cards、card、primaryButton
最小ウィンドウ幅: 800px。本文が収まらなければスクロールできるようにする。
初回は画面表示を実装し、外部APIへの接続は行わない。
```

次のように、ファイルや連携方法は環境によって変わります。パスは構成例であり、既存プロジェクトの配置を優先します。

| 言語・UI環境 | 画面構造の担当例 | 外観の担当例・連携方法 |
| --- | --- | --- |
| Python／PySide6 | `src/ui/window.py` | `src/ui/theme.py`。共有するobjectNameとテーマの公開名を決める |
| C#／WPF | `Views/MainWindow.xaml` | `Themes/Colors.xaml`。リソースのキーと読込み元を決める |
| Dart／Flutter | `lib/screens/home_screen.dart` | `lib/theme/app_theme.dart`。テーマの公開名と部品の引数を決める |
| Swift／SwiftUI | `Views/MainView.swift` | `Theme/AppTheme.swift`。共有する色・寸法等の名前を決める |
| HTML／CSS、または既存Webフレームワーク | `src/ui/index.html`、または画面コンポーネント | `src/ui/styles.css`、または既存のテーマ。クラス名等を決める |

親が既存構成を調べて担当ファイルを決めるため、利用者が毎回子の名前や分割方法を指示する必要はありません。各子の編集先は、仕様で指定した自分のworktree内のファイルに限定します。エントリーポイントやプロジェクト設定も変更が必要なら担当と順序を先に決め、既存のファイルを別形式で作り直さないようにします。

UI環境によっては構造と装飾を同じファイルへ記述します。その場合は画面部品ごとに別ファイルへ分けるか、構造の確定・統合後に外観を編集する順序にします。同じファイルの並行編集を避け、外観を別ファイルへ分けるためだけにプロジェクトの設計を変えることもしません。以下のworktreeと統合コマンドは、Pythonの2ファイルを独立して編集できる場合の例です。

## 3. 並行編集用の作業フォルダを用意する

この例では、利用者がGit worktreeを2つ用意します。子を呼び出すだけではworktreeは自動作成されません。親と子は元の開発フォルダから操作し、担当worktree内のファイルをパスで指定します。

元の開発フォルダの `.gitignore` に、次の行を追加します。

```gitignore
/.pair-worktrees/
```

画像・仕様・エージェント定義を同じ基準から使えるようにコミットします。この例はGitで管理できる画像・設定を使います。既存の別の変更は混ぜず、`git diff --cached` で内容を確認してください。同名のブランチや作業フォルダが存在する場合は、削除・上書きせず、名前とエージェント定義のパスを変更します。

```sh
git status --short
git add .gitignore design/sketch.png design/ui-spec.md
```

VS Codeの場合は次の定義を追加します。

```sh
git add .github/agents/pair-image.agent.md .github/agents/pair-ui-layout.agent.md .github/agents/pair-ui-style.agent.md
```

UI Coordinatorを使う場合は `git add .github/agents/pair-ui-coordinator.agent.md`、Developerを使う場合は次の5つも追加します。

```sh
git add .github/agents/pair-developer.agent.md .github/agents/pair-map.agent.md .github/agents/pair-review.agent.md .github/agents/pair-implementer.agent.md .github/agents/pair-tester.agent.md
```

OpenCodeの場合はこちらを追加します。モデルの接続設定は各作業環境で用意し、実キーをコミットしません。

```sh
git add .opencode/agents/pair-image.md .opencode/agents/pair-ui-layout.md .opencode/agents/pair-ui-style.md
```

UI Coordinatorを使う場合は `git add .opencode/agents/pair-ui-coordinator.md`、Developerを使う場合は次の5つも追加します。

```sh
git add .opencode/agents/pair-developer.md .opencode/agents/pair-map.md .opencode/agents/pair-review.md .opencode/agents/pair-implementer.md .opencode/agents/pair-tester.md
```

利用する方の定義を追加したら、次を実行します。

```sh
git diff --cached
git commit -m "Prepare sketch UI implementation"
git worktree add -b pair-ui-layout .pair-worktrees/layout HEAD
git worktree add -b pair-ui-style .pair-worktrees/style HEAD
```

親のセッションは元の開発フォルダで続けます。Pythonの例では、子も同じフォルダを基準に `.pair-worktrees/layout/src/ui/window.py` と `.pair-worktrees/style/src/ui/theme.py` をそれぞれ編集します。他の環境では仕様の担当パスに読み替えます。OpenCodeをworktree内から起動する場合は相対パスと権限設定が変わるため、この例では元の開発フォルダから起動してください。

仕様を後から変更した場合は、各worktreeから古い仕様を読ませず、元フォルダの確定した `design/ui-spec.md` と変更点を親から両方の子へ渡します。順番に編集する場合は、先の変更を統合したHEADから後続のworktreeを作ります。

## 4. 親へ実装を依頼する

仕様を確認し、worktreeを用意したら、選択済みのPAIR DeveloperまたはUI Coordinatorへ次を送ります。

```text
画面仕様を確認しました。用意した作業フォルダで実装を進めてください。
```

親が画面構造担当と外観担当へ、確定した仕様、言語・UI環境、担当ファイル、作業フォルダ、変更できる範囲、必要な報告を渡します。独立した2件は仕様が共有できてから並行して開始し、両方が完了してから結果をまとめます。画像解析と実装や、同じファイルへの編集など依存関係がある作業は順番に進めます。

子が2つ起動しても別GPUで同時処理されるとは限りません。子の開始・完了記録に加え、PAIR Jobsでモデル、実行ノード、時間の重なりを確認します。

## 5. 変更を統合して画面を確認する

UI Coordinatorと編集担当にはコマンド実行を許可していません。利用者が元の開発フォルダで差分を確認し、担当ファイルだけをコミットして統合します。新しいファイルは `git diff` に出ないため、`git status` とエディターでも内容を確認してください。

```sh
git -C .pair-worktrees/layout status --short
git -C .pair-worktrees/layout diff -- src/ui/window.py
git -C .pair-worktrees/style status --short
git -C .pair-worktrees/style diff -- src/ui/theme.py
git -C .pair-worktrees/layout add src/ui/window.py
git -C .pair-worktrees/layout commit -m "Implement sketch UI structure"
git -C .pair-worktrees/style add src/ui/theme.py
git -C .pair-worktrees/style commit -m "Implement sketch UI appearance"
git cherry-pick pair-ui-layout pair-ui-style
```

元の開発フォルダで別の変更が作業中なら、先にその扱いを整理してから統合します。競合した場合は内容を確認して解決し、無条件に片方を採用しないでください。worktreeを作るだけでは編集先の強制やOS上のアクセス制限にはなりません。親子の実行記録と差分で、担当範囲が守られたことも確認します。

上のコマンドのファイル名は、仕様で決めた担当ファイルへ読み替えてください。統合後は、親へ実装ファイルを読み直して仕様と照合するよう依頼し、使用する言語・UI環境のビルドや既存テストを実行します。そのうえで、対象OSやシミュレーター等でアプリを起動し、文言、配置、余白、色、サイズ変更時の表示、キーボード操作とフォーカスを確認します。

起動コマンドは `design/ui-spec.md` に保存したプロジェクトのものを使います。Pythonの例でも `window.py` が起動用ファイルとは限らないため、既存のエントリーポイントから起動します。必要なSDK・ライブラリや対象OSが手元にない場合は、コードを読んだ確認と、まだ実行できていないビルド・画面確認を分けて報告します。

PAIR Developerを使う場合は、統合したことを親へ伝えてビルド・テストを依頼できます。親がTesterへ委任し、実行完了と結果を確認します。実際のアプリの画面確認やスクリーンショットは利用者が行い、必要なら画像担当へ比較を依頼します。

HTMLとCSSだけの画面を選んだ場合は、元の開発フォルダで次の方法も使えます。

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory src/ui
```

ブラウザーで `http://127.0.0.1:8000/` を開きます。Windowsで `python3` がなければ、利用するPythonの `python` に読み替えます。終了するときは端末でCtrl+Cを入力します。既存Webアプリへ組み込んだ場合は、そのプロジェクトの起動方法とテスト方法を使います。

表示を撮ったPNGを `design/result.png` に保存して親へ渡すと、画像担当に元のポンチ絵との差を調べさせることもできます。画像を見た比較、コードの確認、実際のアプリでの動作確認は分けて報告させます。見た目が合っていることだけで、ボタンやAPIの動作確認が済んだことにはしません。

```text
design/result.pngと元のdesign/sketch.pngを比較し、画面仕様と違う箇所を挙げてください。
修正が必要な場合は、指定したUI環境のどの担当ファイルを直すかも整理してください。
```
