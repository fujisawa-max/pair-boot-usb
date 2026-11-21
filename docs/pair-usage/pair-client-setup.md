# 開発ツールから複数のPAIRノードを使う準備

VS CodeやOpenCodeで複数の仕事を子エージェントへ分担させ、PAIRクラスタの推論ノードを利用する手順です。開発ツールの設定は開発者PCに置きます。

| 担当 | 行う処理 |
| --- | --- |
| VS Code／OpenCode | ファイルを読む、子へ作業を渡す、モデルへ要求を送る、結果をまとめる |
| 開発者PCのPAIR | クラスタに参加し、モデル要求を受け付ける |
| 推論ノードのPAIR・エンジン | 提供するモデルで要求を処理する |

子エージェントの人数とGPUの台数は別です。子を2つ作っても、各子が別のGPUに固定される設定ではありません。複数ノードに同じモデルを用意した場合も、実際の振り分け先はPAIRのJobsで確認します。1要求の推論を複数GPUに分割する機能の説明ではありません。

## 1. PAIRクラスタを準備する

1. 各推論PCを起動し、PAIRを開始します。GPUなしTESTでは推論できません。
2. 提供するモデルを用意し、PAIRのエンジンを開始します。[モデルと起動・終了](../user-guide.md)も参照してください。
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

## モデルIDを役割名で固定する

複数人が使うクラスタでは、APIに公開するIDをコーディング用の `PAIR-Code`、画像用の `PAIR-Vision` のような役割名にすると、利用者が用途を選びやすくなります。同梱のllama.cppでは、`Models` 直下のサブフォルダ名が、その中にあるモデルのIDになります。[llama.cppのモデル検出処理](https://github.com/ggml-org/llama.cpp/blob/b11146/common/preset.cpp#L360-L429)

```text
Models/
├── llama-presets.ini
├── PAIR-Code/
│   └── coding-model.gguf
└── PAIR-Vision/
    ├── vision-model.gguf
    └── mmproj-vision-model.gguf
```

GGUFを置く代わりにシンボリックリンクも使えます。リンク先を推論ノードのLinuxから読み取れることを確認してください。画像モデルを差し替えるときは、対応する `mmproj` も一緒に差し替えます。INIを使う場合は、セクション名を `[PAIR-Code]` と `[PAIR-Vision]` にそろえます。

VS Codeの `id` とOpenCodeの `modelID` にこの役割名を登録します。モデルを入れ替えたらエンジンを再起動し、開発者PCのPAIR APIの `/v1/models` で同じIDが返ることを確認します。画像入力やツール呼び出しに対応し、既存のトークン上限にも収まるモデルなら、利用者はエディター設定を変えずに使い続けられます。

役割名からは実際のモデルが分かりにくくなるため、管理者はノードごとに、役割ID・GGUFのファイル名や版・実効コンテキスト長を記録しておくと、トラブル時の切り分けや切り戻しがしやすくなります。複数ノードで同じ役割を提供する場合は、差し替えたノードで必要な機能を試し、PAIRのJobsで実行先を確認します。

## コンテキスト長の決め方

まず**推論ノードでモデルが安定して使える長さ**を決め、次に**1要求で使える実測値**をVS CodeやOpenCodeに反映します。依頼文のほかに、会話履歴、システム指示、ツール定義・応答、画像なども入力の枠を使います。出力分も同じコンテキストに収まるようにします。

### 推論ノードで調整する

1. PAIR BOOT 0.99.2以降では、登録先の `Models/llama-presets.ini` で対象モデルの `ctx-size` を指定します。初めは `parallel = 1` にして、用途に必要な範囲で小さめの値から始めます。たとえば16,384で試し、足りなければ32,768に上げます。変更後は推論エンジンを再起動します。[INIの配置先と設定例](../user-guide.md#モデルごとにllamacppの設定を変える)
2. 普段使う長さの会話やツール付きの依頼を試します。画像モデルでは実際に使う程度の大きさと枚数の画像も試します。推論しながらノードで `nvidia-smi` を実行し、空きVRAMや、別のモデルもロードしたときの変化を見ます。メモリ不足や大幅な速度低下があれば、一段小さい値に戻します。
3. 最後に、推論ノードのllama.cppで**1要求あたりの実効値**を確認します。モデルをロードしたときのログに出る `n_ctx_per_seq`、またはモデルを処理するllama-serverの `/slots` に出る `n_ctx` が目安です。並列枠を増やしたら実効値とVRAMを測り直します。モデルの `n_ctx_train` はモデルに記録された値です。[llama.cppのスロット情報](https://github.com/ggml-org/llama.cpp/blob/b11146/tools/server/README.md#get-slots-returns-the-current-slots-processing-state)

`/slots` を見るには、対象モデルへ一度推論を送り、ノードの保守コンソールで `pgrep -af llama-server` を実行します。次の `12345` は、対象モデルを処理するプロセスの実際のポートに置き換えます。PAIRのAPIポートとは別です。保守コンソールにcurlがなくてもPython 3で確認できます。

```sh
python3 - 12345 <<'PY'
import json
import sys
from urllib.request import urlopen

with urlopen(f"http://127.0.0.1:{sys.argv[1]}/slots", timeout=5) as response:
    for slot in json.load(response):
        print(f"slot {slot['id']}: n_ctx = {slot['n_ctx']}")
PY
```

### VS Code・OpenCodeの値に換算する

出力枠は普段の依頼に必要な分を先に確保します。下の例では出力を2,048トークンにしています。長いコードを生成するために出力枠を増やす場合は、その分だけ入力に使える枠が小さくなります。コンテキスト超過が起きるときは、ツールや画像を含む実際の要求で試しながら、エディター側の上限に余裕を持たせます。

| llama.cppで確認した1要求の実効値 | VS Codeの `maxInputTokens` / `maxOutputTokens` | OpenCodeの `limit.context` / `limit.output` |
| ---: | ---: | ---: |
| 65,536 | 63,488 / 2,048 | 65,536 / 2,048 |
| 131,072 | 129,024 / 2,048 | 131,072 / 2,048 |

VS Codeの入力上限は **実効値 − 出力枠** を基準にします。OpenCodeの `limit.context` には**入力と出力を合わせた実効値**を設定します。この表の数字は各クイックスタートの例と同じです。使うモデルとノードで確認した値に置き換えてください。[VS Codeの設定仕様](https://code.visualstudio.com/docs/agent-customization/language-models#custom-endpoint-configuration-reference)・[OpenCodeのモデル設定](https://opencode.ai/docs/models/#aliases)

PAIRが同じモデルIDを複数ノードへ振り分ける場合は、**振り分け先のうち最も小さい実効値**にエディターを合わせます。確認に使った要求の実行先はPAIRのJobsで照合できます。

## 4. エディター別に設定する

- [VS Code：Local harnessとサブエージェント](vscode-pair-parallel-quick-start.md)
- [OpenCode：v2のモデル登録とバックグラウンド委任](opencode-pair-parallel-quick-start.md)

まずはファイルを変更しない調査を2件だけ実行し、PAIRのJobsでモデル・ノード・実行時間の重なりを確認します。子の呼び出しが2つ見えるだけでは、GPUで同時処理された証拠にはなりません。キューで待つ場合もあります。

並行編集へ進む場合は、作業ごとに別のGit worktreeを使い、変更を確認して統合します。同じフォルダに複数のチャットを開くだけでは編集対象は分離されません。

確認済みの範囲と残っている確認項目は [TODO](../../TODO.md) を参照してください。
