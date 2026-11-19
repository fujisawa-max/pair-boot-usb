# pair-boot-usb-updaterへの貸出版対応依頼

pair-boot-usb 0.99.1では通常版と貸出版を構築引数で選択します。通常版は従来の契約1.1を維持します。貸出版について、構築側は以下の**契約1.2案**と更新束を生成します。現行updaterで適用できると扱わず、updater側で固定契約として採用・実装・検証してから利用可能にしてください。既存契約の受領ファイルは変更していません。

## 受け渡すファイル

- 貸出版の固定ファイル一覧：`inputs/updater-contract/loan-v1/CONTENTS.json`
- 契約lock：`inputs/updater-contract/loan-v1/inputs/production-contract-v1.lock.json`
- 領域構成：`inputs/updater-contract/loan-v1/inputs/layouts/production-v1/pair-loan-gpt512-v1.json`
- manifest schema：`inputs/updater-contract/loan-v1/schemas/production-v1/manifest.schema.json`
- 固定validator：`inputs/updater-contract/loan-v1/schemas/production-v1/validate.py`
- 構築側の固定情報：`inputs/updater-loan-contract-input.json`

この提案一式を信頼できる経路で受け取り、updaterへ同梱してハッシュを固定します。更新束に入ったコードを実行したり、manifestの宣言だけで新しいprofileを信頼したりしないでください。必要な契約調整がある場合は、構築側と整合させてから更新束を再生成します。

## 貸出版の構成

`contract_version=1`、`contract_revision=2`、`helper_protocol=1`、layout IDは `pair-loan-gpt512-v1` です。通常版ID `pair-r3-gpt512-v1` と区別します。全体版とroot版は0.99.1、boot ABI 1・state schema 1・台帳形式2です。

| 役割 | 番号 | 開始sector | sector数 | 操作 |
| --- | --- | --- | --- | --- |
| EFI | 1 | 2048 | 131072 | replace |
| MASTER | 2 | 133120 | 9437184 | replace |
| LOGS | 3 | 9570304 | 524288 | merge |
| 利用者のNTFS領域（任意） | 4 | 実媒体による | 実媒体による | preserve |

論理sectorは512 bytes。Fedora領域はありません。GPTの両コピーと領域配置を検証し、既存のパーティション表は変更しません。追加のユーザ領域と更新対象外の範囲を保持してください。通常版にある第5領域を前提に、貸出版の第4領域をFedoraとして上書きしないでください。

EFI・MASTERは同時更新が必要です。write orderはMASTER → LOGS → EFIです。manifestの置換区画とlogical inventoryはEFI・MASTERのみです。LOGSは既存内容を基にhelperで加工し、全面上書きしません。

## LOGSの内容とhelper

貸出版のLOGSには管理GUI、tools、起動スクリプト、利用者向け文書を置きません。今回、管理する置換ファイルは `MEDIA-INFO.txt` のみで、`managed_roots=[]` です。schemaは空のmanaged_rootsを許可します。

台帳 `gpu-config/registry.json`、起動用 `gpu-config/hosts/*.env`、`usb-config.json`、ログ、data等を保持します。更新時は従来と同じ整合性検証・台帳移行・起動用hosts生成を行います。markerの `media_profile=loan` を保持し、root_version・registry_schemaを正しく更新します。receiptは従来の `gpu-config/updater-receipt.json` を利用します。

新しい貸出版のLOGSには `signing-cert.der` と `signing-cert-info.txt` を同梱しません。署名と証明書の識別にはEFIの `EFI/BOOT/signing-cert.der` を使います。貸出版ではLOGSのコピーを必須にせず、更新時にも追加しないでください。契約の保護対象に残る両パスは、既存ファイルがある場合の保護を表します。

LOGSにツールがなくても、PC側へ信頼して導入したhelperでprepareできます。helperやWindows workerをUSB上へ追加しないでください。通常版のtools/guiを貸出版へ戻してはいけません。

## 実装・検証の依頼

- 構成IDと契約lockから、同梱済みの通常版／貸出版profileを選び、manifest・helper request/receipt・GPT検証・容量計算・更新対象表示へ一貫して渡す。
- 現在1種類に固定されたPROFILEとlockの読込みを、明示的な対応一覧へ変更する。未知の契約・構成は拒否する。
- 貸出版の更新ではFedoraを要求しない。第4領域はユーザデータとして扱う。標準契約の検証を緩めて代用しない。
- accepted_sources・区画SHA・内部root/state/module署名・証明書・ABI・世代の確認と、所有ファイルの照合を維持する。未宣言の媒体や未知の利用者変更を許可しない。
- 通常版から通常版、貸出版から貸出版だけを許可する。通常版と貸出版の相互変換は拒否する。
- 通常ファイルで、データ保持・空のmanaged_roots・管理ツールなし・追加NTFS領域の保持・破損・別構成・別証明書・中断/再開/回復を試験する。その後、利用者が実USBで確認する。
- 対応を実装したupdaterの版と受入結果を構築側へ知らせる。契約案が一致しない間は、貸出版更新を利用可能と表示しない。

manifest schemaに残る `minimum_updater=1.0.0` は従来形式の固定値であり、現行1.0.xが貸出版へ対応しているという意味ではありません。契約lock・構成IDによる対応確認を必須とし、正式採用時に必要なら版指定の契約も調整してください。

構築側の生成例：

```sh
python3 scripts/build-usb.py image --profile loan \
  --reuse-appliance build/appliance-standard \
  --output build/usb-loan --appliance-output build/appliance-loan \
  --tools-output build/config-loan \
  --update-bundle --update-sequence 1
```

成果物は `build/usb-loan-update-bundle/manifest.json`、images、payload、reportsです。検証レポートには契約案への対応が必要な旨を記録します。秘密鍵、PC登録情報、実機ログは引継ぎ資料へ含めません。
