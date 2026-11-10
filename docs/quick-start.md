# USBの利用開始

作成したブータブルUSBを使って、対象PCでPAIR BOOTを起動するまでの手順です。イメージの構築は [build.md](build.md) を参照してください。

ここでは、PC本体のNTFS領域を保存先にする基本の使い方を説明します。USBを保存先にする場合は、[USBにroot.imgやModelsを保存する場合](user-guide.md#usbにrootimgやmodelsを保存する場合)を参照してください。

### 鍵の作成と管理について
Secure Boot環境で利用する場合、モジュール署名に使う秘密鍵およびMOK登録に使う公開鍵（証明書）のペアは、**利用するPCの管理者（あるいは管理者の許諾の元で）作成**してください。
秘密鍵はPCのセキュリティ上極めて重要です。USB媒体やリポジトリ、共有ストレージ等には絶対に含めず、安全な保管場所で**適切に管理**してください（公開鍵のみをUSBの `GPU-LOGS/signing-cert.der` に配置します。詳細は [secure-boot.md](secure-boot.md) を参照）。

---

## 全体の流れ

1. **Windowsで情報取得**：対象PCのWindows上でGPUや保存先（NTFS）の情報を取得し、候補情報をUSBへ保存します。
2. **FedoraでMOK登録**：USBから同梱のFedora Liveを起動し、Secure Boot用のMOK登録を申請・承認します。
3. **Fedoraで確認とUSB登録**：Windowsで取得した情報をFedora上で確認し、USBへ登録します。
4. **PAIR BOOTの起動**：USBからPAIR BOOTを起動し、推論環境を利用します。

---

## 手順

### 1. WindowsでGPU・保存先情報を取得する
1. 対象PCのWindowsを起動し、USBを接続します。
2. USB内の `start-windows-gui.cmd` を実行して管理GUIを起動します。
3. 推論モデルや状態を保存するドライブ・フォルダ（既定は `D:\GPU-Linux`）を指定し、GPU情報を確認します。

   ※ PC本体にC:以外の領域がなく、USBにroot.img等を保存する場合は、「USBをroot保存先に指定」にチェックします。詳しい手順は[USBに保存する場合](user-guide.md#usbにrootimgやmodelsを保存する場合)を参照してください。

4. 候補情報（JSON）をUSB（`GPU-LOGS`）へ保存します。
   - ※ BitLocker状態が不明（Unknown）と表示される場合は、画面の指示に従い管理者権限でBitLocker確認JSONを保存してください。

### 2. 同梱FedoraでSecure BootのMOK登録を行う
※ Secure Boot有効環境で初めて起動する場合に実施します。
1. PCを再起動し、ブートメニューからUSBを選択して同梱の「Fedora Live」を起動します。
2. 管理GUIで証明書と登録状態を確認し、未登録の場合はMOK登録を申請します。その際、承認用の一時パスワードを設定します。
3. PCを再起動すると「MokManager」青色画面が表示されるので、「Enroll MOK」から先ほどの一時パスワードを入力して承認を完了します。
4. 再度Fedoraを起動し、管理GUIでMOKが「登録済み」になっていることを確認します。

### 3. Windowsで取得した情報の確認とUSBへの登録
1. Fedora Liveの管理GUIを開きます。
2. 手順1でWindowsから出力した候補JSONを読み込み、表示されるPC識別情報、保存先領域、GPU型番、保存フォルダを確認します。

   ※ 上の手順でBitLocker状態が不明（Unknown）と表示され、別途管理者権限でBitLocker確認JSONを取得した場合は、「別の観測JSONを選択する」から、その確認JSONを指定してください。

3. 内容に問題がなければ登録を実行し、USB内の台帳へ反映します。

### 4. PAIR BOOTを起動する
1. PCを再起動し、ブートメニューから「Normal」を選択して起動します。
2. 初回起動時はrootイメージおよびstateイメージの準備が自動で行われます。完了後、起動メニューが表示されます。
3. メニューからPAIRを開始します。
4. 推論モデル（GGUF形式等）は、登録した保存先の `Models` フォルダへ配置して利用します（モデル配置や運用手順の詳細は [user-guide.md](user-guide.md) を参照）。
   - 対応GPUでの高速化（Unlock）は、Normalでの正常起動と動作を確認した後に試してください。

---

## 終了とUSBの取り外し
- PAIRを終了するときは、画面上で `q` を入力してメニューに戻り、PCの再起動または電源断を選択します。
- 起動USBを抜く際は、画面に `USB CAN BE REMOVED NOW` と表示されたことを必ず確認してから取り外してください。
- rootやModelsを置いた別ドライブ／USBは、PCの電源が完全に切れるまで接続したままにしてください。
