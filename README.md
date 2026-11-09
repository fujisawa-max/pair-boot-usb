# pair-boot-usb

USBから起動し、対応するNVIDIA GPUをPAIRの推論ノードとして使うための構築ツールです。対応GPUではgpu-unlockによる高速化を選択できます。Normal起動とUnlock起動は区別します。

**0.9.1は試作版です。`root.img` と設定用の `state.img` を分離し、rootリビジョンによる更新・ダウングレード選択、構成一覧、BitLocker管理者確認GUIを備えます。起動バナーは実行中rootの版・リビジョン・モードを表示します。構築入口は `scripts/build-usb.py prepare` → 利用者による鍵作成・署名 → `scripts/build-usb.py image` です。0.9.1の実機起動・異常終了・root交換の実機検証は未完了です。0.8以前からの設定移行は行わず、新しい保存先フォルダを指定してください。**

## 内容

- `runtime/`：起動、選択した保存先の検証、root.imgの展開・回復、PAIRの操作と終了。
- `toolkit/`、`gui/`：Windowsでの候補情報作成とFedoraでのPC・保存先登録。
- `scripts/`：固定入力の展開、モジュール構築・署名、イメージ組立・検査の部品。
- `inputs/`：外部入力のURL・版・ハッシュ。第三者ファイル本体は含みません。
- `config/`：機種ポリシーと空の初期プロファイル。
- `tests/`：実デバイスに書き込まずに使う合成テスト。

## 手順

1. [構築環境と入力の準備](docs/build.md)
2. [署名鍵とSecure Boot](docs/secure-boot.md)
3. [USBの利用開始](docs/quick-start.md)
4. [運用・モデル・終了・回復](docs/user-guide.md)：[Windowsでの台帳回収・統合・配布](docs/windows-fleet.md)。
5. [開発ツールからPAIRを利用する準備](docs/pair-client-setup.md)：[VS Code](docs/vscode-pair-parallel-quick-start.md)／[OpenCode](docs/opencode-pair-parallel-quick-start.md)のサブエージェント設定。

構築環境はx86_64 Linux/WSL2、Python 3.11以上、空き容量64GiB以上です。Conda用の [environment.yml](environment.yml) はPython 3.11を選びます。追加のpip導入は不要で、[requirements.txt](requirements.txt) に依存関係の扱いを記載しています。OSツール導入・環境確認・テストを済ませてから構築に進んでください：[環境準備の手順](docs/build.md#1-環境を用意する)。

## 対応範囲

現行ポリシーはRTX 30シリーズを対象とします。機種ごとにNormal/Unlockの可否が異なり、一覧への掲載は全機種の実機試験完了を意味しません。RTX 40以降やすべてのCUDA GPUに対応済みとはしていません。モデルの速度はGPU、量子化、コンテキスト長で変わります。

保存先は明示的に選択したNTFS領域の `GPU-Linux` フォルダです。ドライブとフォルダは登録時に変更できます。安全条件を確認できなければ起動・登録を停止します。OSの再インストール、パーティション変更、Windowsの設定変更を自動で行う構築ツールではありません。実USBへの書込みは利用者が対象を確認して行います。

## 含まないもの

ISO、完成USB、rootイメージ、上流ソース一式、ドライバ、モデル、鍵・証明書、PC登録データ、開発日誌、実機のログや写真は同梱していません。ダウンロードした入力は `.cache/` と `vendor/`、生成物は `build/` で管理します。

独自部分のライセンスは選定中です。公開前にLICENSEと必要な帰属表示を確定します。外部ソフトウェアはそれぞれの条件に従います：[外部入力とライセンス](THIRD-PARTY.md)。
