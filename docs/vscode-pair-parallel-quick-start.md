# VS CodeからサブエージェントでPAIRノードを使う

VS Code標準チャットの **Local harnessとCustom Endpoint** を使います。最初に[PAIR側の準備とAPI確認](pair-client-setup.md)を済ませてください。外部モデルとLocal harnessを利用できる版・組織設定が前提です。設定が見つからない場合は、利用中の版とharnessを確認してください。

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
model: PAIR Code
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
model: PAIR Code
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
model: PAIR Code
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

`model` が認識されない場合は、カスタムエージェントの編集画面で登録したモデルを選び直し、VS Codeが保存した識別子を使います。APIのIDと表示名を混同しないでください。親が別モデルを子へ明示指定すると選択が変わる場合もあるため、実行時のモデルを確認します。[Localの委任とモデル選択](https://code.visualstudio.com/docs/agents/run/subagents#subagents-in-local-harness-sessions)

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

ここからは編集も行う別の親を用意します。調査用のPAIR Coordinatorはそのまま残します。先に[共通手順](pair-sketch-workflow.md)の全体を読み、開発対象プロジェクトへ次の4ファイルを追加してください。既存の同名設定がある場合は統合します。

| 役割 | モデル | 担当 |
| --- | --- | --- |
| PAIR Image | PAIR Vision | ポンチ絵や完成画面を読み、仕様や相違点を文章で返す |
| PAIR UI Layout | PAIR Code | HTMLだけを担当worktreeで編集する |
| PAIR UI Style | PAIR Code | CSSだけを担当worktreeで編集する |
| PAIR UI Coordinator | PAIR Code | 画像解析、仕様の保存、実装の分担、結果確認を行う |

### 画像担当

`.github/agents/pair-image.agent.md`：

```markdown
---
name: PAIR Image
description: ポンチ絵から画面仕様を読み取り、完成画面との違いも調べる
model: PAIR Vision
tools: ['read']
---
親が指定したdesign/sketch.pngやdesign/result.pngを画像として開いてください。
画面の領域、文言、配置、色、余白、操作部品を整理して返してください。
読み取れた事実、実装上の提案、判読できない点を分けてください。
ボタンの動作や外部APIなど、画像から分からない仕様は断定しないでください。
画像を実際に開けなければ、その旨を返し、内容を推測しないでください。
比較を依頼された場合は、元の絵・完成画面・渡された仕様の相違点を列挙してください。
ファイルの変更、コマンド実行、別の子への委任はしないでください。
```

Configure Toolsで画像を開くツールが使えることを確認し、子の記録でも画像を実際に開いたか確認します。VS Codeには画像ファイルの読み取り機能がありますが、利用する版・Localセッション・PAIR Visionまで画像データが届くことを試してください。親へ添付しただけでは子への転送を保証しません。[画像ファイルの対応](https://code.visualstudio.com/updates/v1_112#_image-and-binary-file-support-for-agents)

### HTML担当とCSS担当

`.github/agents/pair-ui-layout.agent.md`：

```markdown
---
name: PAIR UI Layout
description: 確定した画面仕様からHTMLを実装する
model: PAIR Code
tools: ['read', 'edit']
---
design/ui-spec.mdと親が渡した仕様を読んで実装してください。
編集してよいのは.pair-worktrees/layout/src/ui/index.htmlだけです。
存在しない場合は新規作成し、既存ファイルがある場合は内容を確認して変更してください。
仕様で決めたクラス名とstyles.cssの参照を使い、HTMLの構造と文言を実装してください。
スタイルをHTMLへ埋め込まず、CSS担当のファイルを変更しないでください。
担当外の変更が必要なら、変更せず理由を親へ返してください。
変更したファイル、実装内容、残った確認事項を報告してください。
コマンド実行、Git操作、別の子への委任はしないでください。
```

`.github/agents/pair-ui-style.agent.md`：

```markdown
---
name: PAIR UI Style
description: 確定した画面仕様からCSSを実装する
model: PAIR Code
tools: ['read', 'edit']
---
design/ui-spec.mdと親が渡した仕様を読んで実装してください。
編集してよいのは.pair-worktrees/style/src/ui/styles.cssだけです。
存在しない場合は新規作成し、既存ファイルがある場合は内容を確認して変更してください。
仕様で決めたクラス名を使い、配置、配色、余白、狭い画面での表示を実装してください。
HTML担当のファイルや、もう一方のworktreeを変更しないでください。
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
description: ポンチ絵を画面仕様にし、HTMLとCSSの実装を分担する
model: PAIR Code
tools: ['agent', 'read', 'search', 'edit']
agents: ['PAIR Image', 'PAIR UI Layout', 'PAIR UI Style']
---
利用者は画像と目的を指定します。必要な調査と分担は自分で決めてください。
1. PAIR Imageへ画像のパスと目的を渡し、解析結果を待ってください。
   画像解析の返答に含まれる仕様文を使い、親自身が画像を見たとは報告しないでください。
2. 画面仕様をdesign/ui-spec.mdへ保存し、利用者の確認を受けてください。
   HTMLとCSSが共有するクラス名、ファイル名、狭い画面での表示も先に決めてください。
3. 利用者が仕様を確認し、2つのworktreeを用意したら実装を始めてください。
   PAIR UI LayoutとPAIR UI Styleへ仕様、担当パス、変更範囲、必要な報告を渡してください。
   独立して編集できる2件は片方の完了を待たず並行して開始し、両方の完了を待ってください。
4. 各worktreeの変更を読み、仕様と担当範囲を確認して利用者へ統合手順を案内してください。
5. 統合後のsrc/ui/index.htmlとsrc/ui/styles.cssを読み直して整合性を確認してください。
   完成画面のPNGが渡されたらPAIR Imageへ比較を委任してください。
親が編集してよいのはdesign/ui-spec.mdだけです。実装ファイルは担当の子へ依頼してください。
不明な要件だけを利用者へ確認し、子の選び方や分担を毎回指定させないでください。
画像解析と実装など依存する仕事は順番に進めてください。同時に動かす子は最大2件です。
コマンド実行、Git操作、worktreeの作成は行わず、必要な操作を利用者へ案内してください。
実際に行った確認と、利用者によるブラウザー確認が必要な点を分けて報告してください。
```

新しいLocalセッションで **PAIR UI Coordinator** を選びます。各`model`は登録済みモデルの識別子を使い、Run Subagentとファイルの読み取り・編集ツールを有効にしてください。画像解析はPAIR Vision、実装はPAIR Codeで実行されたかを記録で確認します。[親から編集担当へ委任する例](https://code.visualstudio.com/docs/agents/run/subagents#delegate-implementation-to-a-worker)

画像を `design/sketch.png` に保存したら、普段の依頼は次のように書けます。画像の内容を読めることを確認したうえで、[共通手順](pair-sketch-workflow.md)に従って仕様確認、worktreeの準備、並行編集、統合、ブラウザー確認へ進めます。

```text
design/sketch.pngをもとに画面を実装してください。
```

## 困った場合と確認範囲

| 状況 | 確認すること |
| --- | --- |
| モデルが選べない | API疎通、モデルID、実際のツール対応、harnessと組織設定 |
| 子が呼ばれない | エージェント名、Run Subagentの有効化、親のagents一覧 |
| 2件が直列になる | まず手動2セッションで確認し、独立した仕事だと明示する |
| 子がファイルを知らない | 子の指示にパス・目的・制約を含める |
| コンテキスト超過 | 小さいファイルと新しい会話で、サーバとクライアントの上限を照合する |

2026-11-10時点の公式資料に基づくLocal用の設定例です。別のharnessへ同じツール名・設定を流用しないでください。USBイメージの再生成は不要です。[OpenCodeの手順](opencode-pair-parallel-quick-start.md)も用意しています。確認済みの範囲と残っている確認項目は [TODO](../TODO.md) を参照してください。

確認用の `pair-check-a.txt` と `pair-check-b.txt` は、確認後に削除して構いません。
