# 開発ツールから複数のPAIRノードを使う準備

VS CodeやOpenCodeで複数の仕事を子エージェントへ分担させ、PAIRクラスタの推論ノードを利用する手順です。開発ツールの設定は開発者PCに置きます。USBや推論ノードへエージェント設定をコピーする必要はありません。

| 担当 | 行う処理 |
| --- | --- |
| VS Code／OpenCode | ファイルを読む、子へ作業を渡す、モデルへ要求を送る、結果をまとめる |
| 開発者PCのPAIR | クラスタに参加し、モデル要求を受け付ける |
| 推論ノードのPAIR・エンジン | 提供するモデルで要求を処理する |

子エージェントの人数とGPUの台数は別です。子を2つ作っても、各子が別のGPUに固定される設定ではありません。複数ノードに同じモデルを用意した場合も、実際の振り分け先はPAIRのJobsで確認します。1要求の推論を複数GPUに分割する機能の説明ではありません。

## 1. PAIRクラスタを準備する

1. 各推論PCを起動し、PAIRを開始します。GPUなしTESTでは推論できません。
2. 提供するモデルを用意し、PAIRのエンジンを開始します。[モデルと起動・終了](user-guide.md)も参照してください。
3. 開発者PCでもPAIRを起動し、同じクラスタに参加します。
4. 開発者PCのPAIRからモデルとノードが見えることを確認します。

同じモデルの要求を複数ノードで処理したい場合は、そのモデルを提供するエンジンが複数ノードで利用可能な状態にします。モデル名やルーティングが一致するかも確認してください。異なるモデルを使う場合は、それぞれを提供できるノードが必要です。

## 2. 開発者PCからAPIを確認する

以下は開発者PCのPAIR APIが `http://127.0.0.1:8080/v1` にある場合の例です。設定したアドレスが異なる場合は読み替えます。

Windows PowerShell：

```powershell
(Invoke-RestMethod 'http://127.0.0.1:8080/v1/models').data |
    Select-Object -ExpandProperty id
```

Linux／macOS：

```sh
curl --fail --silent --show-error http://127.0.0.1:8080/v1/models
```

返された `id` を控えます。以降の設定例の `YOUR_CODE_MODEL_ID` はコード調査に使うモデル、`YOUR_VISION_MODEL_ID` は画像入力対応モデルの正確なIDに置き換えてください。GGUFファイル名や画面の表示名と同じとは限りません。画像を使わない場合、画像モデルの登録は省略できます。

WSL、Remote SSH、コンテナでは `127.0.0.1` が指す環境が異なります。要求を送るプロセスからPAIRへ到達できることを確認し、最初は開発者PCと同じ環境で試します。疎通のために認証なしAPIをインターネットへ公開しないでください。

## 3. 小さい要求から確認する

ここでは、VS Code／OpenCodeを設定する前に、端末からPAIR APIを直接呼んで応答を確認します。エディターの設定は次の「4」で行います。

### APIへ短い質問を送る

開発者PCの端末で実行します。`YOUR_CODE_MODEL_ID` を前節で取得した正確なIDに置き換えます。

Windows PowerShell：

```powershell
$body = @{
    model = 'YOUR_CODE_MODEL_ID'
    messages = @(@{ role = 'user'; content = 'Reply with only PAIR_OK.' })
    max_tokens = 128
    stream = $false
} | ConvertTo-Json -Depth 5
$reply = Invoke-RestMethod -Method Post `
    -Uri 'http://127.0.0.1:8080/v1/chat/completions' `
    -ContentType 'application/json' -Body $body
$reply.choices[0].message.content
```

Linux／macOS：

```sh
curl --fail --silent --show-error \
  http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"YOUR_CODE_MODEL_ID","messages":[{"role":"user","content":"Reply with only PAIR_OK."}],"max_tokens":128,"stream":false}'
```

PowerShellでは表示された本文、curlではJSONの `choices[0].message.content` に `PAIR_OK` があることを確認します。HTTPエラー、空の応答、モデル不明のエラーなら、エディター設定へ進む前にAPIのアドレス、モデルID、PAIRのエンジン起動状態を確認します。応答が長さ制限で途切れた場合は、エンジンの上限内で `max_tokens` を増やして再確認します。

PAIRの画面でも **Jobs** を開き、この要求のモデルと処理したノードを確認します。API応答だけでは、どのGPUが処理したかは判断できません。

ファイル読み取り、手動の2セッション、親から子への委任は、エディターの接続設定を済ませた後に確認します。具体的な操作と期待する結果は、次のエディター別手順に記載しています。

モデルが文章を返すことと、ツールを正しく呼べることは別です。設定のツール対応や画像対応をtrueにするだけでは、実際の機能は追加されません。モデル・チャットテンプレート・エンジン設定の組合せで確認してください。

以下のエディター別手順のトークン数は、**1要求に16384トークンを割り当てられる場合の例**です。依頼文、応答、会話履歴、ツール定義、画像を含めてサーバ側に収まる必要があります。エディターの設定だけでエンジンの上限を増やすことはできません。同時要求数を増やすと待ち時間やVRAM使用量も変わります。

## 4. エディター別に設定する

- [VS Code：Local harnessとサブエージェント](vscode-pair-parallel-quick-start.md)
- [OpenCode：v2のモデル登録とバックグラウンド委任](opencode-pair-parallel-quick-start.md)

まずはファイルを変更しない調査を2件だけ実行し、PAIRのJobsでモデル・ノード・実行時間の重なりを確認します。子の呼び出しが2つ見えるだけでは、GPUで同時処理された証拠にはなりません。キューで待つ場合もあります。

並行編集へ進む場合は、作業ごとに別のGit worktreeを使い、変更を確認して統合します。同じフォルダに複数のチャットを開くだけでは編集対象は分離されません。

これらの設定変更だけならUSBイメージの再生成は不要です。各エディターとPAIRを組み合わせた並行実行・画像受渡し・負荷分散の実機検証は未完了です。
