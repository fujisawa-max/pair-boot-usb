# OpenCodeからサブエージェントでPAIRノードを使う

**OpenCode v2形式**の設定例です。最初に[PAIR側の準備とAPI確認](pair-client-setup.md)を済ませてください。v1用の `provider`・`permission`・`task` の設定例と混ぜないでください。[公式のv2設定](https://opencode.ai/docs/config/)、[権限形式](https://opencode.ai/docs/permissions/)

## 1. モデル接続を設定する

開発対象プロジェクトの `opencode.jsonc` に次を統合します。ユーザー全体で使う場合は、OpenCodeのユーザー設定 `~/.config/opencode/opencode.json(c)` を使えます。同じ目的のjsonとjsoncを重複して作らず、既存設定を残して統合してください。

2つの `modelID` を `/v1/models` の実際の値へ置き換えます。以下のcontext/outputは1要求に16384トークンを使える場合の例です。実際のエンジン設定に合わせます。[モデルIDとOpenAI互換サーバ](https://opencode.ai/docs/models/#compatible)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "pair/code",
  "providers": {
    "pair": {
      "name": "PAIR",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": { "baseURL": "http://127.0.0.1:8080/v1" },
      "models": {
        "code": {
          "name": "PAIR Code",
          "modelID": "YOUR_CODE_MODEL_ID",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"]
          },
          "limit": { "context": 16384, "output": 2048 }
        },
        "vision": {
          "name": "PAIR Vision",
          "modelID": "YOUR_VISION_MODEL_ID",
          "capabilities": {
            "tools": true,
            "input": ["text", "image"],
            "output": ["text"]
          },
          "limit": { "context": 16384, "output": 2048 }
        }
      }
    }
  }
}
```

この例は認証なしのローカル接続です。認証が必要な場合は管理者指定の環境変数などでキーを渡します。`pair/code` はOpenCode内の選択名、`modelID` はAPIへ送る正確なIDです。[接続先と認証の設定](https://opencode.ai/docs/providers/#custom)

画像を使わなければvisionを省略できます。必要ならOpenCodeを再起動し、新しいセッションの `/models` でPAIR Codeを選び、短い質問への応答を確認します。

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

新しいセッションでPAIR Codeを選び、ファイル読み取りを許可して、次を送ります。ファイルの本文はチャットへ貼り付けません。

```text
プロジェクト内のpair-check-a.txtを読み取りツールで開き、
check_tokenとitem_countの値を報告してください。
ファイルは変更せず、コマンドも実行しないでください。
```

回答が `apricot-731` と `3` であることに加え、チャット内のツール実行記録を展開し、`pair-check-a.txt` を実際に読んだことを確認します。回答だけが合っていても読み取り記録がなければ未確認です。読み取りツールの許可と、モデル・チャットテンプレート・エンジンのツール対応を確認してください。

### 自分で2セッションを開始する

別の端末から同じ開発フォルダでOpenCodeをもう1つ起動します。両方の `/models` でPAIR Codeを選び、一方でAファイル、完了前にもう一方でBファイルの値を依頼します。Bの期待値は `cobalt-946` と `5` です。両方の実行履歴でファイル読み取りと回答を確認し、PAIR Jobsでモデル、処理したノード、処理時間を照合します。すぐ終わって時間が重ならない場合は、より長い独立したコード調査に置き換えます。

2要求を送れたことと、複数GPUで同時に処理されたことは区別します。複数GPUを使ったと判断するには、Jobsで時間の重なりと異なる実行先ノードを確認してください。

## 2. 読み取り専用の子を作る

以下のファイルは**開発対象プロジェクト**に作ります。既存の同名設定は上書きせず統合します。モデルと役割は別なので、最初は2つの子が同じ `pair/code` を使います。画像モデルは後から追加できます。[エージェントファイルとmode](https://opencode.ai/docs/agents/#markdown)

`.opencode/agents/pair-map.md`：

```markdown
---
description: 指定されたコードの流れと関連ファイルを調べる
mode: subagent
model: pair/code
permissions:
  - action: "*"
    resource: "*"
    effect: deny
  - action: read
    resource: "*"
    effect: allow
  - action: glob
    resource: "*"
    effect: allow
  - action: grep
    resource: "*"
    effect: allow
---
指定されたコードを読み、処理の流れと関連ファイルを報告してください。
ファイルの変更、コマンド実行、別の子への委任はしないでください。
```

`.opencode/agents/pair-review.md` は上記をコピーし、descriptionと本文を次に変更します。mode、model、permissionsは保持します。

```text
description: コードの不具合と不足する確認を調べる

本文:
指定されたコードを読み、問題、影響、確認方法をファイル位置とともに返してください。
ファイルの変更、コマンド実行、別の子への委任はしないでください。
```

権限は広い拒否の後に必要な読み取りだけを許可する順序です。この例は外部ディレクトリへのアクセスも許可しません。必要な資料は開発プロジェクト内に置きます。

## 3. 親を作り、バックグラウンドで呼ぶ

`.opencode/agents/pair-coordinator.md`：

```markdown
---
description: 独立したコード調査を分担し、結果をまとめる
mode: primary
model: pair/code
permissions:
  - action: "*"
    resource: "*"
    effect: deny
  - action: read
    resource: "*"
    effect: allow
  - action: glob
    resource: "*"
    effect: allow
  - action: grep
    resource: "*"
    effect: allow
  - action: subagent
    resource: pair-map
    effect: allow
  - action: subagent
    resource: pair-review
    effect: allow
---
利用者は調査の目的だけを指定します。関連ファイルを探し、目的に必要な調査と分担を自分で決めてください。
処理の流れや関連ファイルの調査はpair-mapへ、入力検証や不具合の調査はpair-reviewへ委任してください。
独立して進められる調査は、subagentツールのbackground: trueで、片方の完了を待たずに開始してください。同時に動かす子は最大2件とします。
前の結果が必要な調査は、その結果を受け取ってから委任してください。小さな質問は親だけで回答して構いません。
利用者に子の名前や分割方法を指定させる必要はありません。
各子へ目的、実在する対象パス、読み取り専用の制約、必要な出力を渡してください。
委任したすべての子の完了を確認して結果を集め、確認済みの事実と未確認事項を分けてまとめてください。
親もファイルを変更せず、コマンドを実行しないでください。
```

調査するコードがまだない場合は、[共通の調査用サンプル](pair-client-sample.md)に従って `src/main.ts` と `src/config.ts` を作成してください。回答の確認ポイントも掲載しています。

新しいセッションで親のpair-coordinatorを選び、`/models` で親のモデルもPAIR Codeになっていることを確認します。既存セッションのモデル選択が残る場合があります。次は初回の動作確認用に、子と分担を明示した依頼文です。共通サンプルを使う場合はそのまま送れます。既存コードを調べる場合は、パスを実在するファイルへ置き換えます。

```text
pair-mapにsrc/main.tsの処理の流れを、
pair-reviewにsrc/config.tsの入力検証の問題を調べさせてください。
subagent呼出しをbackground: trueで行い、片方の完了を待たずに両方を開始してください。
子には目的、対象パス、読み取り専用の制約を渡してください。
全員ファイルを変更せず、両方の完了を確認してから結果をまとめてください。
```

初回は調査対象を上のA／Bファイルに置き換え、それぞれの値を子に確認させても構いません。子ごとに開始・完了、ファイル読み取り記録、期待する値の回答があることを確認します。

共通サンプルを使った場合は、[回答の確認ポイント](pair-client-sample.md#委任と回答の確認)と両方の子の結果を照合します。

`background: true` はモデルが呼ぶsubagentツールの引数です。opencode.jsoncのトップレベルへbackground設定を追加するものではありません。子の開始と完了を実行履歴で確認し、PAIR Jobsのモデル・ノード・実行時間と照合します。[subagentツール](https://opencode.ai/docs/tools/#subagent)

### 普段は短い依頼でよい

上記のCoordinatorを選んでいれば、もっと簡素に次のように書いても、Coordinatorが関連ファイルを探し、必要な仕事を自律的に分割して子エージェントへ渡します。毎回、子の名前、担当ファイル、分割方法を書く必要はありません。

```text
このプロジェクトの設定読み込みの流れと、入力検証の問題を調べてください。
```

共通サンプルでは、処理の流れをpair-map、入力検証をpair-reviewへ委任する想定です。分担やバックグラウンド呼び出しの方針はCoordinator定義の本文に保存され、エージェントのシステムプロンプトとして使われます。定義を変更した場合は、新しいセッションで選び直して確認します。この設定例は親子とも読み取り専用なので、調査結果をまとめるところまでを行います。[エージェント定義の本文と権限](https://opencode.ai/docs/agents/)

実際に分割・委任するかはモデルが判断します。小さな質問を親だけで処理する場合もあり、短い依頼だけで毎回の委任や並行実行が保証されるわけではありません。子が呼ばれない場合は、まず上の動作確認用の依頼でsubagent権限と呼び出し記録を確認してください。

## 4. 画像担当・並行編集へ広げる

画像担当はpair-mapを別名の `.opencode/agents/pair-image.md` として作り、modelを `pair/vision`、descriptionと本文を画像の配置・文言・操作の整理へ変更します。親のpermissionsへ、subagentの `pair-image` を許可する規則を追加します。

画像はまず単独のVisionセッションで確認し、次に子へプロジェクト内の画像パスを渡します。親への添付が子へ自動転送されるとは仮定しません。画像対応のモデル・適合するmmproj・エンジン設定が必要です。[画像ファイルを扱うread](https://opencode.ai/docs/tools/#read)

この例の親子は読み取り専用です。調査結果を使う実装は、その結果を確認してから編集可能な役割へ依頼します。編集を同時に進める場合は[共通準備](pair-client-setup.md)のとおりworktreeを分けます。

## 困った場合と確認範囲

| 状況 | 確認すること |
| --- | --- |
| モデルが見つからない | modelID、PAIRでそのモデルを提供するノード、エンジンの起動状態 |
| 子を呼べない | v2形式、mode、ファイル名から決まる子ID、親のsubagent権限 |
| 子が直列になる | background引数と開始記録を確認し、独立した作業を明示する |
| ファイルを読めない | プロジェクト内のパス、子のread/glob/grep権限、モデルのツール対応 |
| コンテキスト上限やcompactionで失敗する | 新しい会話と短い依頼で、サーバの1要求あたりの枠とlimitを照合する |

2026-11-09時点の公式v2資料に基づく設定例です。Desktop／CLIのUIや設定読込位置は利用する版で確認してください。USBイメージの再生成は不要です。[VS Code用の手順](vscode-pair-parallel-quick-start.md)もあります。確認済みの範囲と残っている確認項目は [TODO](../TODO.md) を参照してください。

確認用の `pair-check-a.txt` と `pair-check-b.txt` は、確認後に削除して構いません。
