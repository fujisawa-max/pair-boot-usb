# ポンチ絵から画面を実装する共通手順

[VS Code](vscode-pair-parallel-quick-start.md#5-ポンチ絵から画面を実装する)／[OpenCode](opencode-pair-parallel-quick-start.md#4-ポンチ絵から画面を実装する)の画像・編集エージェントを使う手順です。設定や画像は、USBではなく**開発対象のプロジェクト**に置きます。この例はHTMLとCSSで画面を作ります。既存のフレームワークへ組み込む場合は、担当ファイルと設定の許可パスを実際の構成へ変更します。

## 1. 画像を用意する

開発対象のプロジェクトで `design/` を作り、自分のポンチ絵を `design/sketch.png` として保存します。画像モデルを使うため、この例ではPNGを使います。親への画像添付だけに頼らず、子からも読めるファイルを用意してください。

単独のPAIR Visionセッションで画像を添付し、文字や配置を読めることを先に確認します。次にエディター別手順で画像担当・HTML担当・CSS担当・UI Coordinatorの4つの定義を作り、UI Coordinatorを選んで次を依頼します。

```text
design/sketch.pngをもとに画面を実装してください。
まず画面仕様をdesign/ui-spec.mdへまとめ、確認できるようにしてください。
```

親は画像担当へ解析を依頼し、結果を `design/ui-spec.md` に保存します。この段階では画面のコードを書きません。子の記録で画像読み取りとPAIR Visionの使用を確認し、画像内の実際の文言や配置と回答を照合します。画像パスを知っているだけ、ファイルを見つけただけでは画像を読めたことにはなりません。

画像を子へ渡せない場合は、単独のPAIR Visionセッションへ画像を添付し、読み取った仕様を文章で親へ渡してください。見えていない画像の内容を推測させて実装へ進まないようにします。

## 2. 仕様と担当範囲を決める

仕様には、画面の構成、表示する文言、色・余白・配置、狭い画面での表示、確認方法をまとめます。画像から読み取れた事実と、実装上の提案、確認が必要な点を分けます。絵だけでは分からないボタンの動作やAPIは、必要なものだけ利用者へ確認します。

HTMLとCSSを並行して編集する前に、同じクラス名を使う約束も保存します。例えば、絵がヘッダー・サイドバー・カード一覧の画面なら、次のようにします。これは画像からの解析結果そのものではなく、分担のための記載例です。

```text
出力: src/ui/index.html、src/ui/styles.css
HTMLから同じフォルダのstyles.cssを読み込む。
ルート: .app
ヘッダー: .header
サイドバー: .sidebar
本文: .main
カード一覧: .cards、各カード: .card
主ボタン: .primary-button
768px未満ではサイドバーを本文の上に配置する。
初回は画面表示を実装し、外部APIへの接続は行わない。
```

親が担当ファイルを決めます。利用者が毎回子の名前や分割方法を指示する必要はありません。最初の例では、HTML担当は `index.html`、CSS担当は `styles.css` だけを編集します。共通ファイルの変更が必要な作業は順番に行います。

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
git add .github/agents/pair-image.agent.md .github/agents/pair-ui-layout.agent.md .github/agents/pair-ui-style.agent.md .github/agents/pair-ui-coordinator.agent.md
```

OpenCodeの場合はこちらを追加します。モデルの接続設定は各作業環境で用意し、実キーをコミットしません。

```sh
git add .opencode/agents/pair-image.md .opencode/agents/pair-ui-layout.md .opencode/agents/pair-ui-style.md .opencode/agents/pair-ui-coordinator.md
```

利用する方の定義を追加したら、次を実行します。

```sh
git diff --cached
git commit -m "Prepare sketch UI implementation"
git worktree add -b pair-ui-layout .pair-worktrees/layout HEAD
git worktree add -b pair-ui-style .pair-worktrees/style HEAD
```

親のセッションは元の開発フォルダで続けます。子も同じフォルダを基準に、`.pair-worktrees/layout/src/ui/index.html` と `.pair-worktrees/style/src/ui/styles.css` をそれぞれ編集します。OpenCodeをworktree内から起動する場合は相対パスと権限設定が変わるため、この例では元の開発フォルダから起動してください。

## 4. 親へ実装を依頼する

仕様を確認し、worktreeを用意したら、選択済みのUI Coordinatorへ次を送ります。

```text
画面仕様を確認しました。用意した作業フォルダで実装を進めてください。
```

親がHTML担当とCSS担当へ、確定した仕様、担当ファイル、作業フォルダ、変更できる範囲、必要な報告を渡します。2件は仕様が共有できてから並行して開始し、両方が完了してから結果をまとめます。画像解析と実装は依存関係があるため、同時には始めません。

子が2つ起動しても別GPUで同時処理されるとは限りません。子の開始・完了記録に加え、PAIR Jobsでモデル、実行ノード、時間の重なりを確認します。

## 5. 変更を統合して画面を確認する

この例のエージェントにはコマンド実行を許可していません。利用者が元の開発フォルダで差分を確認し、担当ファイルだけをコミットして統合します。新しいファイルは `git diff` に出ないため、`git status` とエディターでも内容を確認してください。

```sh
git -C .pair-worktrees/layout status --short
git -C .pair-worktrees/layout diff -- src/ui/index.html
git -C .pair-worktrees/style status --short
git -C .pair-worktrees/style diff -- src/ui/styles.css
git -C .pair-worktrees/layout add src/ui/index.html
git -C .pair-worktrees/layout commit -m "Implement sketch UI markup"
git -C .pair-worktrees/style add src/ui/styles.css
git -C .pair-worktrees/style commit -m "Implement sketch UI styles"
git cherry-pick pair-ui-layout pair-ui-style
```

元の開発フォルダで別の変更が作業中なら、先にその扱いを整理してから統合します。競合した場合は内容を確認して解決し、無条件に片方を採用しないでください。worktreeを作るだけでは編集先の強制やOS上のアクセス制限にはなりません。親子の実行記録と差分で、担当範囲が守られたことも確認します。

統合後は、親へ `src/ui/index.html` と `src/ui/styles.css` を読み直して仕様と照合するよう依頼します。実際の表示はブラウザーでも確認してください。HTMLとCSSだけの例なら、元の開発フォルダで次を実行できます。

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory src/ui
```

ブラウザーで `http://127.0.0.1:8000/` を開き、文言、配置、余白、色、狭い画面での表示を確認します。Windowsで `python3` がなければ、利用するPythonの `python` に読み替えます。終了するときは端末でCtrl+Cを入力します。既存アプリへ組み込んだ場合は、そのプロジェクトの起動方法とテスト方法を使います。

表示を撮ったPNGを `design/result.png` に保存して親へ渡すと、画像担当に元のポンチ絵との差を調べさせることもできます。画像を見た比較、コードの確認、ブラウザーでの動作確認は分けて報告させます。見た目が合っていることだけで、ボタンやAPIの動作確認が済んだことにはしません。

```text
design/result.pngと元のdesign/sketch.pngを比較し、画面仕様と違う箇所を挙げてください。
修正が必要な場合は、HTMLとCSSのどちらを直すかも整理してください。
```
