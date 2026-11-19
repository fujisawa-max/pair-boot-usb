# 通常版・貸出版とPC用管理ツール

構築時に `scripts/build-usb.py image --profile standard` または `--profile loan` を指定し、1回につき一方を生成します。既定は通常版です。どちらにも `--update-bundle --update-sequence N` を付けます。構築・署名は [構築手順](build.ja.md) に従ってください。

| 構成 | 通常版 standard | 貸出版 loan |
| --- | --- | --- |
| EFI / GPU-MASTER / GPU-LOGS | 64 / 4608 / 256 MiB | 同じ |
| Fedora領域と起動メニュー | 3072 MiB・あり | なし |
| LOGS上の管理GUI・スクリプト・利用者向け文書 | あり | なし |
| LOGS上の台帳・起動設定・媒体情報・ログ保存先 | あり | あり |
| LOGS上のMOK登録用公開証明書と指紋ファイル | あり | なし（署名検証用の証明書はEFIに保持） |
| 初期イメージサイズ | 約8.39GB | 約5.17GB・8GB USB向け |

貸出版でも実行用root.img・state.img・モデルは登録したNTFS領域へ置きます。USBだけで全保存容量を賄う構成ではありません。初期イメージは未登録です。管理者が通常版で対象PCの登録と必要なMOK登録を済ませ、台帳を貸出版へ配布してください。

## 同じrootから両方を作る

最初の通常版を生成・検証した後、貸出版の構築時に `--reuse-appliance` でそのapplianceを指定します。rootとstateテンプレートのサイズ・ハッシュと版を検査し、同じ内容を再利用します。別の全体版のrootは再利用できません。新しいMASTERのUUIDとGRUB設定だけを作成します。

```sh
python3 scripts/build-usb.py image --profile standard \
  --output build/usb-standard --appliance-output build/appliance-standard \
  --tools-output build/config-standard --fedora-output build/fedora-standard \
  --update-bundle --update-sequence N

python3 scripts/build-usb.py image --profile loan \
  --reuse-appliance build/appliance-standard \
  --output build/usb-loan --appliance-output build/appliance-loan \
  --tools-output build/config-loan \
  --update-bundle --update-sequence M
```

`N` と `M` は配布系統ごとに増やす整数へ置き換えます。すべて新しい出力先を指定します。通常版・貸出版で完成したrootを別々に再生成して、同じ版に異なるrootを混在させないでください。

## 管理ツールをPCへコピーする

イメージの出力先が `build/usb-standard` の場合、既定では `build/usb-standard-manager/` に共通の管理ツールセットも生成します。`--manager-output` で別の新規フォルダを指定できます。貸出版の構築でも同様に生成します。このセットは貸出版USBへコピーしません。

生成されたフォルダ全体をWindows管理PCへコピーし、直下の `start-windows-gui.cmd` を起動します。GUI単体ではなく、`gui/`・`tools/`・`manager-config.json`・起動ファイルを一緒に保持してください。PCへのソフトウェア導入処理やMOK登録を自動実行するものではありません。

- `data/`：候補情報などの既定の保存先。
- `backups/`：USBから回収した台帳の既定の保存先。新しいファイル名で保存し、過去の台帳を保持します。
- `gpu-config/`：マージ・形式変換・PC名更新の出力台帳と、配布する台帳の既定の選択先。

管理PCは未登録・NVIDIA GPUなしでも回収・マージ・配布できます。新しい推論PCの登録は、その対象PC上で通常版のFedoraを使います。

通常版から台帳をPCへ回収した後、通常版を安全に外し、貸出版を1本接続して配布します。配布先を選び、PC上の台帳を指定してください。配布先USBに管理ツールは不要です。registry.jsonの手動コピーだけではなく、管理ツールでhosts/*.envも整合させます。配布後は別名で再回収して世代・登録数を確認します。[台帳管理の詳細](windows-fleet.md)

同名のGPU-LOGSだけで配布先を判断せず、ドライブ文字・容量・媒体種別を確認します。同じイメージの複製はGUIDやUUIDも共有するため、複数USBの同時接続に依存しない運用を基本とします。

## 部分更新

通常版には従来の契約1.1の更新束を生成します。貸出版には別ID `pair-loan-gpt512-v1` の契約1.2案を使います。**現行updaterは貸出版の契約に未対応であり、その対応と検証が済むまで貸出版の更新束を適用できません。** イメージ全体を新規USBへ書く用途とは区別してください。

[updaterへの対応依頼](updater-loan-contract-request.md) と [更新束の生成手順](update-bundle.ja.md) を参照してください。通常版と貸出版を部分更新で相互変換することはできません。
