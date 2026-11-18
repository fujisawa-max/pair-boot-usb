# OpenCodeからサブエージェントでPAIRノードを使う

この文書のエージェント定義は、[コピー用サンプルと配置方法](pair-agent-samples.md)にも用意しています。OpenCode用の10ファイルから、調査用・UI用・開発全体の統括用を選んで使えます。普段の開発を1つの親へ任せる場合は、[pair-developer](#5-開発全体をpair-developerに任せる)を使います。

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

## 4. ポンチ絵から画面を実装する

ここからは編集も行う別の親を用意します。調査用のpair-coordinatorはそのまま残します。先に[共通手順](pair-sketch-workflow.md)を読み、開発対象プロジェクトへ次の4ファイルを追加してください。既存の同名設定がある場合は統合します。言語・UIフレームワーク・対象OSは依頼文または既存プロジェクトから決め、仕様へ保存します。共通手順にはPython／PySide6の例と、C#／WPF等への読み替えがあります。

| 役割 | モデル | 担当 |
| --- | --- | --- |
| pair-image | pair/vision | ポンチ絵や完成画面から仕様や相違点を返す |
| pair-ui-layout | pair/code | 指定したUI環境の画面構造・部品配置を担当worktreeで編集する |
| pair-ui-style | pair/code | 指定したUI環境の外観・テーマを担当worktreeで編集する |
| pair-ui-coordinator | pair/code | 画像解析、仕様の保存、実装の分担、結果確認を行う |

### 画像担当

`.opencode/agents/pair-image.md`：

```markdown
---
description: ポンチ絵から画面仕様を読み取り、完成画面との違いも調べる
mode: subagent
model: pair/vision
permissions:
  - action: "*"
    resource: "*"
    effect: deny
  - action: read
    resource: "design/*.png"
    effect: allow
---
親が指定したdesign/内のPNGをreadで画像として実際に開いてください。
design/sketch.pngやdesign/result.pngは例です。design/newimage.pngなど、許可範囲内の別名の画像も指定されたパスを使ってください。
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

`read`はPNG等の画像もモデルへ渡します。子の記録で、画像の読み取りとPAIR Visionの使用を確認してください。親への添付が子へ自動転送されるとは仮定しません。PAIR側にも画像対応モデル・必要なmmproj・画像を処理できるエンジン設定が必要です。[画像を扱うread](https://opencode.ai/docs/tools/#read)

### 画面構造担当と外観担当

`.opencode/agents/pair-ui-layout.md`：

```markdown
---
description: 指定された言語とUI環境で画面構造・部品配置を実装する
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
  - action: edit
    resource: ".pair-worktrees/layout/*"
    effect: allow
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

`.opencode/agents/pair-ui-style.md`：

```markdown
---
description: 指定されたUI環境で配色・余白・テーマなどの外観を実装する
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
  - action: edit
    resource: ".pair-worktrees/style/*"
    effect: allow
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

V2の`edit`権限はedit／write／patchに適用されます。許可パスは元の開発フォルダからの相対パスです。例では言語ごとの配置に対応できるよう、各子のworktree内を許可し、本文と仕様で編集するファイルを指定しています。`*` は `/` も含めて照合するため、配下のファイルも対象です。Pythonの例でファイル単位に絞るなら、構造担当のedit resourceを `.pair-worktrees/layout/src/ui/window.py`、外観担当を `.pair-worktrees/style/src/ui/theme.py` に変更します。worktreeの位置や担当ファイルを変えたらpermissionsも合わせてください。子は親とは別の権限を使うため、各子にも必要な許可を定義します。[パスと権限の照合](https://opencode.ai/docs/permissions/#matching)

### 画像から実装まで進める親

`.opencode/agents/pair-ui-coordinator.md`：

```markdown
---
description: ポンチ絵を画面仕様にし、指定した言語とUI環境で実装を分担する
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
  - action: edit
    resource: "design/ui-spec.md"
    effect: allow
  - action: subagent
    resource: pair-image
    effect: allow
  - action: subagent
    resource: pair-ui-layout
    effect: allow
  - action: subagent
    resource: pair-ui-style
    effect: allow
---
画像を参照する依頼では、以下の画像委任ルールを最優先してください。
- 「画像を参照」「この絵に合わせて」、PNG等のファイル名、添付画像に基づく配置・色・文言の調整は、短い追加依頼でもpair-imageの担当です。
- 最初の解析操作としてsubagentツールを実際に呼び、子のIDに正確にpair-imageを指定してください。子のmodel: pair/visionを使い、親のモデルで上書きしないでください。
- 子へプロジェクト基準の画像パス、利用者が調整したい項目、既知のUI環境を渡します。画像をreadで開いて対象部分の配置・色・文言を報告するよう依頼し、ファイル編集はさせません。
- 画像担当が読める範囲はdesign/*.pngです。別の場所の画像なら、利用者へこの範囲への配置または対象パスの権限設定を案内してください。相対パスの基準は作業するプロジェクトです。
- 子の呼び出しはバックグラウンドで始まります。sessionIDが返っただけでは画像解析の完了とせず、子の完了通知と解析結果を確認してから編集担当へ渡してください。
- 親自身は画像をreadで開いて解析しません。子を呼ぶ前に「画像が見えない」と回答したり、画像全体の説明や座標を利用者へ求めたりしないでください。
- 子を呼べない場合や子が画像を読めなかった場合は、呼び出し・ファイルの所在・権限・画像の読み取りのどこで失敗したかを報告します。内容を推測して編集せず、必要なパスや設定だけ確認してください。

利用者は画像、目的、必要なら言語・UIフレームワーク・対象OSを指定します。
既存のプロジェクト設定と画面コードを調べ、指定された環境を保ってください。
未指定なら既存構成を使い、新規で決められない項目だけ確認してください。
必要な調査と分担は自分で決めてください。
1. pair-imageへ画像のパス、目的、使用するUI環境を渡し、解析結果を待ってください。
   画像解析の返答に含まれる仕様文を使い、親自身が画像を見たとは報告しないでください。
2. 画面仕様をdesign/ui-spec.mdへ保存し、利用者の確認を受けてください。
   言語・UI環境の版、対象OS、担当ファイル、共有する部品名・参照方法、サイズ変更時の表示、
   プロジェクトのビルド・起動・確認方法を記録してください。
   構造と外観を別ファイルで編集できるか確認し、同じファイルを触る場合は順番に進めてください。
3. 利用者が仕様を確認し、必要なworktreeを用意したら実装を始めてください。
   pair-ui-layoutとpair-ui-styleへ確定した仕様、言語・UI環境、担当パス、変更範囲、必要な報告を渡してください。
   独立して編集できる2件は、片方の完了を待たずにsubagentで開始してください。
   両方が完了してから結果を集めてください。
4. 各worktreeの変更を読み、仕様と担当範囲を確認して利用者へ統合手順を案内してください。
5. 統合後の担当ファイルを読み直して整合性を確認し、対象環境でのビルド・起動・画面確認を案内してください。
   完成画面のPNGが渡されたらpair-imageへ比較を委任してください。
親が編集してよいのはdesign/ui-spec.mdだけです。実装ファイルは担当の子へ依頼してください。
不明な要件だけを利用者へ確認し、子の選び方や分担を毎回指定させないでください。
画像解析と実装など依存する仕事は順番に進めてください。同時に動かす子は最大2件です。
コマンド実行、Git操作、worktreeの作成は行わず、必要な操作を利用者へ案内してください。
コードを読んだ確認、画像の比較、対象環境でのビルド・実行確認を分けて報告してください。
```

新しいセッションで **pair-ui-coordinator** を選び、`/models`で親もPAIR Codeにします。子は定義した`pair/vision`／`pair/code`を使うか記録で確認します。画像を `design/sketch.png` に保存したら、普段の依頼は次のように書けます。解析結果の確認、worktreeの準備と統合、対象アプリでの確認方法は[共通手順](pair-sketch-workflow.md)を参照してください。担当の分割は親が行います。

```text
design/sketch.pngをもとに、Python 3／PySide6でWindows向けの画面を実装してください。
```

既存アプリなら「design/sketch.pngをもとに、このプロジェクトの言語・UIフレームワークで画面を実装してください」でも構いません。C#／WPF、Flutter等を使う場合も、その環境を指定します。設定済みの役割名はそのまま使えます。

## 5. 開発全体をpair-developerに任せる

親を切り替えずに調査・画像解析・実装・テストまで進めたい場合は、**pair-developer** を選びます。元の2種類の親は、用途を絞ったサンプルとしてそのまま使えます。

| 親 | 向いている依頼 |
| --- | --- |
| pair-developer | 不具合修正、機能追加、UIと一般コードの変更、テストとレビューまで進める |
| pair-coordinator | コードを変更せず、処理の流れや問題点を調べる |
| pair-ui-coordinator | ポンチ絵からの画面仕様・UI実装だけに絞る |

[Developer用サンプル](examples/pair-agents/opencode/agents/pair-developer.md)と、pair-map・pair-review・pair-image・pair-ui-layout・pair-ui-style・[pair-implementer](examples/pair-agents/opencode/agents/pair-implementer.md)・[pair-tester](examples/pair-agents/opencode/agents/pair-tester.md)を `.opencode/agents/` へコピーします。新しいセッションでpair-developerを選び、`/models` で親のモデルもPAIR Codeにしてください。

```text
設定ファイルが空の場合にアプリが落ちる原因を調べ、修正してテストしてください。
```

Developerは専門の子を直接呼び、調査だけの依頼なら変更せずに報告します。実装は編集担当、検証コマンドはpair-testerへ委任します。Testerの `shell` 権限は `ask` なので実行時に承認します。UIでは既存と同じworktree準備・統合手順を使います。詳細は[開発全体の進め方](pair-development-workflow.md)を参照してください。

### アプリで親を選ぶ

OpenCodeアプリでは、開発対象プロジェクトを開き、入力欄付近のエージェント選択から `pair-developer` または `pair-ui-coordinator` を選びます。選択欄が見えない場合は **Settings → General → Show agent** をオンにしてください。独自エージェントを配置しても選択欄が表示されない版があります。[表示設定の報告と回避方法](https://github.com/anomalyco/opencode/issues/50269)

`pair-developer` が一覧にない場合は、そのプロジェクトの `.opencode/agents/pair-developer.md` を確認します。選んだ役割と親のモデルは別なので、モデル選択でもPAIR Codeを確認してください。ターミナル版では `/agents` で親、`/models` でモデルを選びます。[ターミナル版の操作](https://opencode.ai/docs/cli/tui/#models)

### 短い依頼でも画像担当へ委任する

画像は `design/` 内のPNGとして用意します。このサンプルの画像担当は `design/*.png` だけを読み取れる設定です。別のフォルダや形式を使う場合は、利用者が画像担当のread権限も合わせて変更してください。

```text
LifeやHiScoreの位置について、design/newimage.pngを参照して調整してください。
```

DeveloperとUI Coordinatorの冒頭に、短い追加依頼でも最初に `pair-image` へ渡す指示を入れています。親はパスと着目点を子へ渡し、子が `pair/vision` で画像を読んだ結果を確認してから、編集担当を決めます。OpenCodeの `subagent` はバックグラウンドで実行されるので、開始時のsessionIDだけで解析済みとはしません。[子の実行と完了通知](https://opencode.ai/docs/tools/#subagent)

以前の定義を使っている場合は、[Developer](examples/pair-agents/opencode/agents/pair-developer.md)、[UI Coordinator](examples/pair-agents/opencode/agents/pair-ui-coordinator.md)、[Image](examples/pair-agents/opencode/agents/pair-image.md)を、開発対象プロジェクトの `.opencode/agents/` へ反映してください。独自のモデル指定や権限は保持して比較・統合し、アプリでプロジェクトを開き直して新しいセッションで親を選びます。

親が「画像が見えない」と答えた場合は、子の呼び出しがあるかを確認します。呼ばれていれば子の使用モデル、readの対象パスと結果を確認してください。返答だけで、未委任・モデル違い・読み取り失敗のどれかを断定しないでください。これはモデルへの指示であり、短い依頼でも委任されたかは利用環境で確認します。

## 困った場合と確認範囲

| 状況 | 確認すること |
| --- | --- |
| モデルが見つからない | modelID、PAIRでそのモデルを提供するノード、エンジンの起動状態 |
| 子を呼べない | v2形式、mode、ファイル名から決まる子ID、親のsubagent権限 |
| 子が直列になる | 子の開始・完了記録を確認し、独立した作業を明示する |
| ファイルを読めない | プロジェクト内のパス、子のread/glob/grep権限、モデルのツール対応 |
| コンテキスト上限やcompactionで失敗する | 新しい会話と短い依頼で、サーバの1要求あたりの枠とlimitを照合する |

2026-11-10時点の公式v2資料に基づく設定例です。Desktop／CLIのUIや設定読込位置は利用する版で確認してください。USBイメージの再生成は不要です。[VS Code用の手順](vscode-pair-parallel-quick-start.md)もあります。確認済みの範囲と残っている確認項目は [TODO](../TODO.md) を参照してください。

確認用の `pair-check-a.txt` と `pair-check-b.txt` は、確認後に削除して構いません。
