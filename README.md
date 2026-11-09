# pair-boot-usb

USBから起動し、対応するNVIDIA GPUをPAIRの推論ノードとして使うための構築ツールです。RTX 30シリーズのうち、Unlockに対応するGPUではgpu-unlockによる高速化を選択できます。Normal起動とUnlock起動は区別します。

## 手順

1. [構築環境と外部資料の準備](docs/build.md)
2. [署名鍵とSecure Boot](docs/secure-boot.md)
3. [USBの利用開始](docs/quick-start.md)
4. [運用・モデル・終了・回復](docs/user-guide.md)：[Windowsでの台帳回収・統合・配布](docs/windows-fleet.md)。
5. [開発ツールからPAIRを利用する準備](docs/pair-client-setup.md)：[VS Code](docs/vscode-pair-parallel-quick-start.md)／[OpenCode](docs/opencode-pair-parallel-quick-start.md)のサブエージェント設定。

動作確認の状況と残っている確認項目は [TODO](TODO.md) にまとめています。

必要な構築環境はx86_64 Linux/WSL2、Python 3.11以上、空き容量64GiB以上です。Conda用の [environment.yml](environment.yml) ではPython 3.11を使うようになっています。[requirements.txt](requirements.txt) に依存関係の扱いを記載しています。開発ツール導入・環境確認・テストを済ませてから構築に進んでください：[環境準備の手順](docs/build.md#1-環境を用意する)。

## 内容

- `runtime/`：起動、選択した保存先の検証、root.imgの展開・回復、PAIRの操作と終了。
- `toolkit/`、`gui/`：Windowsでの候補情報作成とFedoraでのPC・保存先登録。
- `scripts/`：外部資料の展開、モジュール構築・署名、イメージ組立・検査の部品。
- `inputs/`：外部資料の取得元URL・バージョン情報・SHA-256などの検証情報。第三者ファイル本体は含みません。
- `config/`：GPUの種類ごとのNormal／Unlockの実行許可設定と空の初期プロファイル。
- `tests/`：テスト用に作成したデータやファイルを使い、実デバイスに書き込まずに動作を確認するテスト。

## 対応範囲

現行の許可設定はRTX 30シリーズを対象とします。機種ごとにNormal/Unlockの可否が異なり、一覧への掲載は全機種の実機試験完了を意味しません。RTX 40以降やすべてのCUDA GPUに対応済みとはしていません。v1.0.0のころにはRTX-30シリーズ以外にも対応していく予定です。

保存先は明示的に選択したNTFS領域の `GPU-Linux` フォルダです。ドライブとフォルダは登録時に変更できます。安全条件を確認できなければ起動・登録を停止します。実USBへの書込みは利用者が対象を確認して行います。

## 含まないもの

ISO、完成USB、rootイメージ、上流ソース一式、ドライバ、モデル、鍵・証明書、PC登録データ、開発日誌、実機のログや写真は同梱していません。ダウンロードした外部資料は `.cache/` と `vendor/`、生成物は `build/` で管理します。

## ライセンス

このリポジトリの独自ソース、設定、テスト、文書は [MIT License](LICENSE) で公開します。著作権表示とライセンス文を保持して、利用・改変・再配布できます。

取得する外部ソフトウェアには、それぞれのライセンスが適用されます：[外部資料とライセンス](THIRD-PARTY.md)。このリポジトリでは構築用ソースを公開し、完成USBイメージは配布しません。
