# GPUなしで0.9.0の起動を確認する

対応NVIDIA GPUのないPCで、起動バナー、3択メニュー、rootとstateの分離、再起動後の状態保持を確認するための専用起動です。GPU推論・Unlock・PAIR参加の試験にはなりません。実機動作の確認は未完了です。

`root.img`、初期 `state.img`、`master.json` とバージョン `0.9.0-shared` を保持し、initramfs・GRUB設定・Fedoraの登録ツールを更新します。既存のrootを新版と判定して交換する処理は発生しません。確認起動で使用するrootとstateには、通常と同じ初期化・設定保存・回復処理による書込みが発生します。「root.imgを変更しない」はUSB内の配布テンプレートを再生成しないという意味です。

## イメージを作る

既に構築・検査した、署名済みの0.9.0共有USB用applianceを入力にします。以下の `build/appliance` は、その既存の出力ディレクトリに読み替えます。公開証明書を利用しますが、新しい秘密鍵や再署名は不要です。ホストや実デバイスは変更しません。

```sh
python3 scripts/refresh-boot-only.py \
  --source build/appliance --output build/appliance-no-gpu
python3 scripts/validate-appliance.py build/appliance-no-gpu/usb-tree \
  --output build/appliance-no-gpu/validation.json
python3 scripts/prepare-shared-usb-tools.py \
  --fedora-source build/fedora-live \
  --certificate build/signed-modules/signing-cert.der \
  --output build/tools-no-gpu --fedora-output build/fedora-no-gpu
python3 scripts/assemble-usb-image.py \
  --tree build/appliance-no-gpu/usb-tree \
  --sysroot build/appliance-no-gpu/root-tree \
  --config-tree build/tools-no-gpu --fedora-tree build/fedora-no-gpu \
  --data-mib 8192 --log-mib 256 --output build/usb-image-no-gpu
python3 scripts/verify-shared-media.py \
  --tree build/appliance-no-gpu/usb-tree \
  --sysroot build/appliance-no-gpu/root-tree \
  --config-tree build/tools-no-gpu --fedora-tree build/fedora-no-gpu \
  --image-directory build/usb-image-no-gpu
```

出力先はすべて新しい名前を使用します。失敗時の途中出力は保持し、既存出力を削除して再実行しません。`boot-refresh.json` にrootとstateの変更前後で一致したSHAを記録します。入力の破損、不適合版、既存出力、シンボリックリンクを検出した場合は停止します。

## 登録して起動する

1. 利用者が完成イメージを試験用USBへ書き込みます。書き換えるUSBの既存情報は失われるため、必要なデータは先に退避します。
2. 対象PCのWindowsで、USBのNTFSデータ領域を明示的に選択して候補JSONを作ります。既存の0.8以前の保存先は使用せず、新しいフォルダを選びます。
3. 同じPCでFedora Liveを起動し、保存先NTFSが自動マウントされていれば、使用中のファイルを閉じてそのNTFS領域だけを安全にアンマウントします。GPU-LOGS・GPU-FEDORAやUSB全体は取り外しません。管理GUIの「GPUなし確認用PC・USB保存先を登録（TEST）」を選び、候補とBitLocker確認情報を選択し、登録内容を確認して確定します。GPU BDFの入力は不要です。PC識別、Secure Boot、暗号化、Fast Startup、パーティション属性、USBの実体・予約領域との重複の確認は通常どおり行います。BitLockerのUnknownは許可しません。
4. 再起動し、GRUBで **TEST / no NVIDIA GPU / USB storage only** を明示的に選びます。自動選択はされません。Normal/Unlockを選んでも確認モードへ自動で切り替わりません。
5. バナーがバージョン0.9.0と `Mode: TEST / no GPU` を示すこと、3択メニュー、再起動・停止を確認します。PAIR開始を選ぶと、試験用に推論サービスを無効化しているため開始を拒否し、メニューに戻ります。

同じPCでも通常登録と確認用登録は区別します。既存の登録を自動で書き換えません。確認用登録をNormal/Unlockで使うこと、通常登録をTESTで使うこと、NVIDIA機器のあるPCをTESTで起動すること、内蔵領域を確認用保存先にすることは許可しません。

## WindowsのShell観測がUnknownになる場合

管理者の `manage-bde -status F:` で完全な復号・暗号化0%・保護オフを確認できても、通常権限のShell観測がUnknownになることがあります。候補JSONの値は書き換えず、管理者として開いたWindows PowerShellで、追加の確認JSONを作成します。`F:` は保存先NTFS、`X:` はGPU-LOGSの現在のドライブ文字に読み替えます。

```powershell
& 'X:\tools\save-admin-bitlocker.ps1' -Volume F: -OutputFile 'X:\bitlocker-admin.json'
```

このスクリプトは状態照会のみを行います。WMIの復号状態・暗号化率・保護状態を確認し、PC識別とNTFS UUIDに結び付けて保存します。取得失敗・不明・暗号化中などの場合はJSONを作らず停止します。既存出力は上書きしません。暗号化設定や鍵は変更・取得しません。

Fedoraの登録画面で従来の候補JSONを選び、BitLocker確認情報で「別の観測JSONを選択する」から `bitlocker-admin.json` を指定します。FedoraはPCとNTFS UUID・期限を照合し、保存先の確認も通常どおり行います。候補のShell観測はUnknownのまま保持します。Windowsの版や権限によって状態を取得できない場合は停止します。

スクリプトがまだ媒体にない場合は、このリポジトリの `toolkit/save-admin-bitlocker.ps1` だけをGPU-LOGSの `tools` へコピーできます。既存媒体のFedora側はこの確認JSONを受け取れるため、rootやUSBイメージを作り直す必要はありません。

## 保存領域を確認する

保存先フォルダ内に `root.img` と `state.img`、それぞれのreceiptが作成されます。`/home/gpu` はstate内のhomeを使用し、共通実行ファイルはroot内の `/opt` に残ります。3択メニューだけではPAIR設定変更の保持を試験できません。

状態保持をファイルで確認する場合は、PCを正常停止してからFedora等で保存先を開きます。root・stateが使用中でないことを確認したうえで、利用者が `state.img` の `home/gpu` に試験用のファイルを置き、正常にアンマウントします。再びTEST起動・正常停止し、そのファイルが残ることを確認します。使用中イメージへの二重マウントやfsckは行いません。

TEST表示の補助ファイルとサービス設定はRAM上に置きます。バナーの差替えはコンソールサービス内だけの読み取り専用bindで行い、root内のプログラムは上書きしません。通常起動のバナー・PAIR動作を変更するものではありません。
