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

## 5. 画像担当と編集を追加する

画像担当を使う場合は、PAIR Mapを別名のエージェントとして作り、`name` を `PAIR Image`、`model` を登録したPAIR Visionの識別子へ変更します。本文は画像の配置・文言・操作を整理する指示にします。親の `agents` に名前を追加します。

まず単独のVisionセッションで画像を添付して確認し、次に子からプロジェクト内の画像を読み取れるか試します。親への添付が子にも渡るとは仮定しません。子で読めなければ単独の画像セッションで仕様を作り、その文章を親へ渡します。画像解析を必要とする実装は、解析の完了後に開始します。

この例の親子には編集ツールがありません。実装へ進むときは結果を確認し、編集を許可した通常のエージェントへ依頼します。複数の編集を並行する場合は[共通準備のworktreeに関する説明](pair-client-setup.md)を参照してください。

## 困った場合と確認範囲

| 状況 | 確認すること |
| --- | --- |
| モデルが選べない | API疎通、モデルID、実際のツール対応、harnessと組織設定 |
| 子が呼ばれない | エージェント名、Run Subagentの有効化、親のagents一覧 |
| 2件が直列になる | まず手動2セッションで確認し、独立した仕事だと明示する |
| 子がファイルを知らない | 子の指示にパス・目的・制約を含める |
| コンテキスト超過 | 小さいファイルと新しい会話で、サーバとクライアントの上限を照合する |

2026-11-09時点の公式資料に基づくLocal用の設定例です。別のharnessへ同じツール名・設定を流用しないでください。USBイメージの再生成は不要です。[OpenCodeの手順](opencode-pair-parallel-quick-start.md)も用意しています。確認済みの範囲と残っている確認項目は [TODO](../TODO.md) を参照してください。

確認用の `pair-check-a.txt` と `pair-check-b.txt` は、確認後に削除して構いません。
