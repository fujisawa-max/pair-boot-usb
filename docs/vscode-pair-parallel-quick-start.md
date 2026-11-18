# VS CodeからサブエージェントでPAIRノードを使う

VS Code標準チャットの **Local harnessとCustom Endpoint** を使います。最初に[PAIR側の準備とAPI確認](pair-client-setup.md)を済ませてください。外部モデルとLocal harnessを利用できる版・組織設定が前提です。設定が見つからない場合は、利用中の版とharnessを確認してください。

この文書のエージェント定義は、[コピー用サンプルと配置方法](pair-agent-samples.md)にも用意しています。VS Code用の10ファイルから、調査用・UI用・開発全体の統括用を選んで使えます。普段の開発を1つの親へ任せる場合は、[PAIR Developer](#6-開発全体をpair-developerに任せる)を使います。

## 1. モデルを登録する

コマンドパレットで `Chat: Manage Language Models` を開き、Custom Endpointを追加します。API形式は **Chat Completions**、認証なしのローカルPAIR接続でキー入力が必須ならダミー値 `local` を使います。認証ありの環境では管理者指定の方法でキーを保存し、設定例へ実キーを書き込まないでください。

開いた `chatLanguageModels.json` に次を統合します。2つの `id` を `/v1/models` の実際の値に置き換え、トークン数をエンジンの設定に合わせます。既存の接続先・認証参照は保持します。[公式のモデル設定](https://code.visualstudio.com/docs/agent-customization/language-models#custom-endpoint-configuration-reference)

```json
[
  {
    "name": "PAIR",
    "vendor": "customendpoint",
    "apiType": "chat-completions",
    "apiKey": "local",
    "models": [
      {
        "id": "YOUR_CODE_MODEL_ID",
        "name": "PAIR Code",
        "url": "http://127.0.0.1:8080/v1/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 14336,
        "maxOutputTokens": 2048
      },
      {
        "id": "YOUR_VISION_MODEL_ID",
        "name": "PAIR Vision",
        "url": "http://127.0.0.1:8080/v1/chat/completions",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 14336,
        "maxOutputTokens": 2048
      }
    ]
  }
]
```

画像を使わない場合はVisionの項目を削除できます。新しいLocalセッションでPAIR Codeを選び、短い質問への応答を確認します。モデルが表示されなければVS Codeを再起動します。

### エディターから実際にファイルを読ませる

ここからは、上の接続設定を済ませたエディターで操作します。開発対象のフォルダを開き、次の2ファイルを新規作成して保存してください。同名ファイルがあれば別名にして、依頼文のパスも変更します。USBや推論ノードへ作る必要はありません。

`pair-check-a.txt`：

```text
check_token = apricot-731
item_count = 3
```

`pair-check-b.txt`：

```text
check_token = cobalt-946
item_count = 5
```

新しいLocalセッションでPAIR Codeを選び、ファイル読み取りツールを許可して、次を送ります。ファイルの本文はチャットへ貼り付けません。

```text
プロジェクト内のpair-check-a.txtを読み取りツールで開き、
check_tokenとitem_countの値を報告してください。
ファイルは変更せず、コマンドも実行しないでください。
```

回答が `apricot-731` と `3` であることに加え、チャット内のツール実行記録を展開し、`pair-check-a.txt` を実際に読んだことを確認します。回答だけが合っていても読み取り記録がなければ未確認です。読み取りツールの許可と、モデル・チャットテンプレート・エンジンのツール対応を確認してください。


## 2. 自分で2セッションを開始する

セッションAで上の `pair-check-a.txt` の値を依頼し、完了前にセッションBで `pair-check-b.txt` の値を依頼します。Bの期待値は `cobalt-946` と `5` です。短すぎて時間が重ならなければ、より長い独立したコード調査に置き換えます。両方でPAIR Codeを選び、セッションの実行記録とPAIR Jobsを確認します。同じモデルで2要求を送って構いません。別ノードで処理されたかはJobsで確認します。

## 3. 親と子の設定を作る

**開発対象のプロジェクト**に次の3ファイルを作ります。既存ファイルがあれば統合します。USBや推論PCの設定ではありません。[カスタムエージェントの作成](https://code.visualstudio.com/docs/agent-customization/custom-agents)

`.github/agents/pair-map.agent.md`：

```markdown
---
name: PAIR Map
description: 指定されたコードの流れと関連ファイルを調べる
model: PAIR Code (customendpoint)
tools: ['read', 'search']
---
依頼された範囲のコードを読み、処理の流れと関連ファイルを報告してください。
ファイルを変更せず、コマンドを実行しないでください。
```

`.github/agents/pair-review.agent.md`：

```markdown
---
name: PAIR Review
description: 指定されたコードの不具合と不足する確認を調べる
model: PAIR Code (customendpoint)
tools: ['read', 'search']
---
指定されたコードを調べ、問題、影響、確認方法をファイル位置とともに返してください。
ファイルを変更せず、コマンドを実行しないでください。
```

`.github/agents/pair-coordinator.agent.md`：

```markdown
---
name: PAIR Coordinator
description: 独立したコード調査を分担し、結果をまとめる
model: PAIR Code (customendpoint)
tools: ['agent', 'read', 'search']
agents: ['PAIR Map', 'PAIR Review']
---
利用者は調査の目的だけを指定します。関連ファイルを探し、目的に必要な調査と分担を自分で決めてください。
処理の流れや関連ファイルの調査はPAIR Mapへ、入力検証や不具合の調査はPAIR Reviewへ委任してください。
独立して進められる調査は、片方の完了を待たず並行して開始してください。同時に動かす子は最大2件とします。
前の結果が必要な調査は、その結果を受け取ってから委任してください。小さな質問は親だけで回答して構いません。
利用者に子の名前や分割方法を指定させる必要はありません。
各子へ目的、実在する対象パス、読み取り専用の制約、必要な出力を渡してください。
結果を集め、確認済みの事実と未確認事項を分けてまとめてください。
親もファイルを変更せず、コマンドを実行しないでください。
```

上の登録例では、`model` に `PAIR Code (customendpoint)`／`PAIR Vision (customendpoint)` を指定します。Localでは表示名に提供元を付けた形式を使い、`PAIR Vision` だけの指定では親のモデルが使われる場合があります。別の表示名で登録した場合は、`.agent.md` の `model:` で `Ctrl+Space` を押し、登録したモデルを補完から選んでください。APIへ送るモデルIDとは区別します。親が別モデルを子へ明示指定すると選択が変わる場合もあるため、実行時のモデルを確認します。[Localの委任とモデル選択](https://code.visualstudio.com/docs/agents/run/subagents#subagents-in-local-harness-sessions)

## 4. 親から並行委任する

調査するコードがまだない場合は、[共通の調査用サンプル](pair-client-sample.md)に従って `src/main.ts` と `src/config.ts` を作成してください。回答の確認ポイントも掲載しています。

新しいLocalセッションでPAIR Coordinatorを選び、Configure ToolsでRun Subagent（`agent/runSubagent`）が有効なことを確認します。次は初回の動作確認用に、子と分担を明示した依頼文です。共通サンプルを使う場合はそのまま送れます。既存コードを調べる場合は、パスを実在するファイルへ置き換えます。

```text
PAIR Mapにsrc/main.tsの処理の流れを、
PAIR Reviewにsrc/config.tsの入力検証の問題を調べさせてください。
2件は独立しているので、片方の完了を待たず並行して開始してください。
全員読み取り専用で作業し、両方の結果を別々に示してから結論をまとめてください。
子には対象パスと調査目的を明示してください。
```

初回は調査対象を上のA／Bファイルに置き換え、それぞれの値を子に確認させても構いません。子の呼び出し記録を開き、2件の開始・完了、指定モデル、対象ファイルの読み取り記録、回答の値を確認します。PAIR Jobsでも要求のモデル・実行先・時間の重なりを照合します。指示文だけでは並行実行を保証しません。

共通サンプルを使った場合は、[回答の確認ポイント](pair-client-sample.md#委任と回答の確認)と両方の子の結果を照合します。

### 普段は短い依頼でよい

上記のCoordinatorを選んでいれば、もっと簡素に次のように書いても、Coordinatorが関連ファイルを探し、必要な仕事を自律的に分割して子エージェントへ渡します。毎回、子の名前、担当ファイル、分割方法を書く必要はありません。

```text
このプロジェクトの設定読み込みの流れと、入力検証の問題を調べてください。
```

共通サンプルでは、処理の流れをPAIR Map、入力検証をPAIR Reviewへ委任する想定です。この方針はCoordinator定義の本文に保存されており、普段の依頼文で繰り返す必要はありません。定義を変更した場合は、新しいセッションで選び直して確認します。この設定例は親子とも読み取り専用なので、調査結果をまとめるところまでを行います。

実際に分割・委任するかはモデルが判断します。小さな質問を親だけで処理する場合もあり、短い依頼だけで毎回の委任や並行実行が保証されるわけではありません。子が呼ばれない場合は、まず上の動作確認用の依頼でツール設定と呼び出し記録を確認してください。[VS Codeの自律的な委任とCoordinatorの例](https://code.visualstudio.com/docs/agents/run/subagents#orchestration-patterns)

## 5. ポンチ絵から画面を実装する

ここからは編集も行う別の親を用意します。調査用のPAIR Coordinatorはそのまま残します。先に[共通手順](pair-sketch-workflow.md)の全体を読み、開発対象プロジェクトへ次の4ファイルを追加してください。既存の同名設定がある場合は統合します。言語・UIフレームワーク・対象OSは依頼文または既存プロジェクトから決め、仕様へ保存します。共通手順にはPython／PySide6の例と、C#／WPF等への読み替えがあります。

| 役割 | モデル | 担当 |
| --- | --- | --- |
| PAIR Image | PAIR Vision | ポンチ絵や完成画面を読み、仕様や相違点を文章で返す |
| PAIR UI Layout | PAIR Code | 指定したUI環境の画面構造・部品配置を担当worktreeで編集する |
| PAIR UI Style | PAIR Code | 指定したUI環境の外観・テーマを担当worktreeで編集する |
| PAIR UI Coordinator | PAIR Code | 画像解析、仕様の保存、実装の分担、結果確認を行う |

### 画像担当

`.github/agents/pair-image.agent.md`：

```markdown
---
name: PAIR Image
description: ポンチ絵から画面仕様を読み取り、完成画面との違いも調べる
model: PAIR Vision (customendpoint)
tools: ['read']
---
親が指定した絶対パスの画像を、画像として開くツールで実際に開いてください。
design/sketch.pngやdesign/result.pngは例です。newimage.pngなど別名・別フォルダの画像も、指定されたパスを使ってください。
依頼にLifeやHiScoreなど着目する項目があれば、その部分を優先して読み取ってください。
画面の領域、文言、配置、色、余白、操作部品を整理して返してください。
対象画像のパス、確認できる画像サイズ、対象部品の相対位置・整列・間隔を報告してください。正確な座標が分からない場合は推定と明記し、値を断定しないでください。
読み取れた事実、実装上の提案、判読できない点を分けてください。
言語・UIフレームワークが渡された場合は、その環境の部品に対応付ける案も返してください。
ボタンの動作や外部APIなど、画像から分からない仕様は断定しないでください。
画像を実際に開けなければ、対象パスとツールのエラー・制約を返し、内容を推測しないでください。
比較を依頼された場合は、元の絵・完成画面・渡された仕様の相違点を列挙してください。
ファイルの変更、コマンド実行、別の子への委任はしないでください。
```

Configure Toolsで画像を開くツールが使えることを確認し、子の記録でも画像を実際に開いたか確認します。VS Codeには画像ファイルの読み取り機能がありますが、利用する版・Localセッション・PAIR Visionまで画像データが届くことを試してください。親へ添付しただけでは子への転送を保証しません。[画像ファイルの対応](https://code.visualstudio.com/updates/v1_112#_image-and-binary-file-support-for-agents)

### 画面構造担当と外観担当

`.github/agents/pair-ui-layout.agent.md`：

```markdown
---
name: PAIR UI Layout
description: 指定された言語とUI環境で画面構造・部品配置を実装する
model: PAIR Code (customendpoint)
tools: ['read', 'edit']
---
design/ui-spec.mdと親が渡した仕様を読んで実装してください。
仕様に記録した言語・UIフレームワーク・対象OSと既存プロジェクトの構成に従ってください。
編集してよいのは.pair-worktrees/layout/内で、親が担当として指定したファイルだけです。
存在しない場合は新規作成し、既存ファイルがある場合は内容を確認して変更してください。
仕様で決めた部品名、テーマの公開名・参照方法を使い、構造・文言・配置を実装してください。
外観担当のファイルは変更しないでください。
担当外の変更が必要なら、変更せず理由を親へ返してください。
変更したファイル、実装内容、残った確認事項を報告してください。
コマンド実行、Git操作、別の子への委任はしないでください。
```

`.github/agents/pair-ui-style.agent.md`：

```markdown
---
name: PAIR UI Style
description: 指定されたUI環境で配色・余白・テーマなどの外観を実装する
model: PAIR Code (customendpoint)
tools: ['read', 'edit']
---
design/ui-spec.mdと親が渡した仕様を読んで実装してください。
仕様に記録した言語・UIフレームワーク・対象OSと既存プロジェクトの構成に従ってください。
編集してよいのは.pair-worktrees/style/内で、親が担当として指定したファイルだけです。
存在しない場合は新規作成し、既存ファイルがある場合は内容を確認して変更してください。
仕様で決めた部品名やテーマの公開名を使い、配色、文字、余白、サイズ変更時の外観を実装してください。
画面構造担当のファイルや、もう一方のworktreeを変更しないでください。
担当外の変更が必要なら、変更せず理由を親へ返してください。
変更したファイル、実装内容、残った確認事項を報告してください。
コマンド実行、Git操作、別の子への委任はしないでください。
```

`edit` はファイル編集用のツールです。上記の担当パスはエージェントへの指示であり、VS Codeのツール一覧だけで書込み先を限定する設定ではありません。各子の変更を確認してください。[編集ツール](https://code.visualstudio.com/docs/agents/reference/tools-reference#edit-files-and-work-with-notebooks)

### 画像から実装まで進める親

`.github/agents/pair-ui-coordinator.agent.md`：

```markdown
---
name: PAIR UI Coordinator
description: ポンチ絵を画面仕様にし、指定した言語とUI環境で実装を分担する
model: PAIR Code (customendpoint)
tools: ['agent', 'read', 'search', 'edit']
agents: ['PAIR Image', 'PAIR UI Layout', 'PAIR UI Style']
---
画像を参照する依頼では、以下の画像委任ルールを最優先してください。
- 「画像を参照」「この絵に合わせて」、PNG等のファイル名、添付画像に基づく配置・色・文言の調整は、短い追加依頼でもPAIR Imageの担当です。
- 画像のパスを確認し、相対パスなら作業フォルダを基準に絶対パスへ直します。必要ならファイル検索だけを先に行います。
- 最初の解析操作としてrunSubagentを実際に呼び、agentNameを正確に「PAIR Image」にします。model引数は省略し、子に設定されたVision用モデルを使います。
- 子へ画像の絶対パス、利用者が調整したい項目、既知のUI環境を渡し、画像を開いて対象部分の配置・色・文言を報告するよう依頼します。解析のみと指定し、結果を待ちます。
- 親自身はview_image等で画像を開きません。子を呼ぶ前に「画像が見えない」と回答したり、画像全体の説明や座標を利用者へ求めたりしないでください。
- 子を呼べない場合や子が画像を読めなかった場合は、呼び出し・ファイルの所在・画像の読み取りのどこで失敗したかを報告します。内容を推測して編集せず、必要なパスや設定だけ確認してください。

利用者は画像、目的、必要なら言語・UIフレームワーク・対象OSを指定します。
既存のプロジェクト設定と画面コードを調べ、指定された環境を保ってください。
未指定なら既存構成を使い、新規で決められない項目だけ確認してください。
必要な調査と分担は自分で決めてください。
1. PAIR Imageへ画像のパス、目的、使用するUI環境を渡し、解析結果を待ってください。
   画像解析の返答に含まれる仕様文を使い、親自身が画像を見たとは報告しないでください。
2. 画面仕様をdesign/ui-spec.mdへ保存し、利用者の確認を受けてください。
   言語・UI環境の版、対象OS、担当ファイル、共有する部品名・参照方法、サイズ変更時の表示、
   プロジェクトのビルド・起動・確認方法を記録してください。
   構造と外観を別ファイルで編集できるか確認し、同じファイルを触る場合は順番に進めてください。
3. 利用者が仕様を確認し、必要なworktreeを用意したら実装を始めてください。
   PAIR UI LayoutとPAIR UI Styleへ確定した仕様、言語・UI環境、担当パス、変更範囲、必要な報告を渡してください。
   独立して編集できる2件は片方の完了を待たず並行して開始し、両方の完了を待ってください。
4. 各worktreeの変更を読み、仕様と担当範囲を確認して利用者へ統合手順を案内してください。
5. 統合後の担当ファイルを読み直して整合性を確認し、対象環境でのビルド・起動・画面確認を案内してください。
   完成画面のPNGが渡されたらPAIR Imageへ比較を委任してください。
親が編集してよいのはdesign/ui-spec.mdだけです。実装ファイルは担当の子へ依頼してください。
不明な要件だけを利用者へ確認し、子の選び方や分担を毎回指定させないでください。
画像解析と実装など依存する仕事は順番に進めてください。同時に動かす子は最大2件です。
コマンド実行、Git操作、worktreeの作成は行わず、必要な操作を利用者へ案内してください。
コードを読んだ確認、画像の比較、対象環境でのビルド・実行確認を分けて報告してください。
```

新しいLocalセッションで **PAIR UI Coordinator** を選びます。各`model`は登録済みモデルの識別子を使い、Run Subagentとファイルの読み取り・編集ツールを有効にしてください。画像解析はPAIR Vision、実装はPAIR Codeで実行されたかを記録で確認します。[親から編集担当へ委任する例](https://code.visualstudio.com/docs/agents/run/subagents#delegate-implementation-to-a-worker)

画像を `design/sketch.png` に保存したら、普段の依頼は次のように書けます。画像の内容を読めることを確認したうえで、[共通手順](pair-sketch-workflow.md)に従って仕様確認、worktreeの準備、編集、統合、対象アプリの画面確認へ進めます。担当の分割は親が行います。

```text
design/sketch.pngをもとに、Python 3／PySide6でWindows向けの画面を実装してください。
```

既存アプリなら「design/sketch.pngをもとに、このプロジェクトの言語・UIフレームワークで画面を実装してください」でも構いません。C#／WPF、Flutter等を使う場合も、その環境を指定します。設定済みの役割名はそのまま使えます。

## 6. 開発全体をPAIR Developerに任せる

親を切り替えずに調査・画像解析・実装・テストまで進めたい場合は、**PAIR Developer** を選びます。元の2種類の親は、用途を絞ったサンプルとしてそのまま使えます。

| 親 | 向いている依頼 |
| --- | --- |
| PAIR Developer | 不具合修正、機能追加、UIと一般コードの変更、テストとレビューまで進める |
| PAIR Coordinator | コードを変更せず、処理の流れや問題点を調べる |
| PAIR UI Coordinator | ポンチ絵からの画面仕様・UI実装だけに絞る |

[Developer用サンプル](examples/pair-agents/vscode/agents/pair-developer.agent.md)と、Map・Review・Image・UI Layout・UI Style・[Implementer](examples/pair-agents/vscode/agents/pair-implementer.agent.md)・[Tester](examples/pair-agents/vscode/agents/pair-tester.agent.md)を `.github/agents/` へコピーします。モデルは登録済みの識別子に合わせ、新しいLocalセッションでPAIR Developerを選んでください。

```text
設定ファイルが空の場合にアプリが落ちる原因を調べ、修正してテストしてください。
```

Developerは専門の子を直接呼び、調査だけの依頼なら変更せずに報告します。実装は編集担当、検証コマンドはTesterへ委任します。Testerの `execute/runInTerminal` と `execute/getTerminalOutput` が使えることを確認してください。UIでは既存と同じworktree準備・統合手順を使います。詳細は[開発全体の進め方](pair-development-workflow.md)を参照してください。

### 短い依頼でも画像担当へ委任する

VS Code用のDeveloperとUI Coordinatorには、画像を参照する依頼をPAIR Imageへ優先して渡す指示を入れています。例えば、プロジェクト直下に `newimage.png` がある場合は次のように依頼します。

```text
LifeやHiScoreの位置について、newimage.pngを参照して調整してください。
```

親が画像のパスと着目点を子へ渡し、画像解析の結果を待ってから修正の担当を決めます。画像の説明や座標を利用者へ求める前に、子による読み取りを試す指示です。画像の名前を `sketch.png` に変える必要はありません。

以前の定義を使っている場合は、[Developer](examples/pair-agents/vscode/agents/pair-developer.agent.md)、[UI Coordinator](examples/pair-agents/vscode/agents/pair-ui-coordinator.agent.md)、[Image](examples/pair-agents/vscode/agents/pair-image.agent.md)を開発対象プロジェクトの `.github/agents/` へ反映します。自分のモデル指定や追加の設定を保持して比較・統合し、新しいLocalセッションで試してください。

これはモデルに委任を指示する設定です。実際に委任されたかは、[エージェントのデバッグログ](https://code.visualstudio.com/docs/agents/agent-troubleshooting/chat-debug-view#logs-view)で `runSubagent` の呼び出し先が `PAIR Image`、子の要求がVision用モデルであることを確認します。短い依頼でも同じ結果になるかは利用するモデルで確認してください。

## 困った場合と確認範囲

| 状況 | 確認すること |
| --- | --- |
| モデルが選べない | API疎通、モデルID、実際のツール対応、harnessと組織設定 |
| 子が呼ばれない | エージェント名、Run Subagentの有効化、親のagents一覧 |
| 画像の説明や座標を求められる | 最新の親の画像委任ルールが適用されているか、runSubagentの呼び出し記録があるか |
| Imageの子も親と同じモデルになる | 子のmodelが表示名＋提供元になっているか、親がmodel引数で上書きしていないか |
| 2件が直列になる | まず手動2セッションで確認し、独立した仕事だと明示する |
| 子がファイルを知らない | 子の指示にパス・目的・制約を含める |
| コンテキスト超過 | 小さいファイルと新しい会話で、サーバとクライアントの上限を照合する |

2026-11-10時点の公式資料に基づくLocal用の設定例です。別のharnessへ同じツール名・設定を流用しないでください。USBイメージの再生成は不要です。[OpenCodeの手順](opencode-pair-parallel-quick-start.md)も用意しています。確認済みの範囲と残っている確認項目は [TODO](../TODO.md) を参照してください。

確認用の `pair-check-a.txt` と `pair-check-b.txt` は、確認後に削除して構いません。
