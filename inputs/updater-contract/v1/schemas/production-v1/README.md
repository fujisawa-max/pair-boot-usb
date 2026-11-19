# 実媒体更新契約 1.1

この文書と同梱schema・配置設定を、構築側と更新側のデータ契約の正本とする。
`contract_version:1` / `contract_revision:1`。合成manifest v1/v2とは別形式である。
**契約の確定は実媒体対応の完成を意味しない。現在の0.5.0エンジンはこのmanifestを拒否する。**
この版の対象は旧0.9.1-r3（構築基準commit `1d9fc63`）から統一版番号の0.99.0以降への移行と、
同じ配置の既知媒体の更新。新しい区画配置、ABI/state schema変更、証明書交換は別契約とする。

## ファイルと版の固定

- `manifest.schema.json`: producerが生成する更新manifest。
- `layout.schema.json`: 配置・役割・更新方法・組合せ・順序。
- `helper-request.schema.json` / `helper-receipt.schema.json`: 通常ファイル加工の入出力。
- `inputs/layouts/production-v1/pair-r3-gpt512-v1.json`: 配置の正本。
- `inputs/production-contract-v1.lock.json`: 上記、本文、検証器の生バイトSHA256。
- `tests/fixtures/production-v1/`: 架空のメタデータ例。イメージや実台帳は含めない。

manifest/request/receiptはlockファイル自体の生バイトSHAを保持する。
JSONはUTF-8、重複キー・非有限数・未知フィールドを拒否する。SHAは小文字hex、ファイルの生バイトを対象とする。
schemaはJSON Schema 2020-12。`validate.py`は同梱schemaの閉じた語彙と追加意味制約を検査する。
外部schema取得・コード実行・mount・デバイスアクセスは行わない。
既存の契約を双方で別々に編集せず、意味変更は新revisionまたは新versionとして発行する。
SHAは破損検知であり、配布者の真正性を保証しない。初版は所有者のローカル構築物を明示選択する。

## producerの出力

検査済みの全体イメージ、manifest、管理対象の通常ファイルpayload、構築検証レポートを新規出力先へ生成する。
manifestの`image`はサイズ・SHAを、`partitions`は置換役割の全区画サイズ・SHAを保持する。
オフセット・番号・方式はハッシュ固定したlayoutから得る。イメージは直接書くのではなく選択した区画の範囲読取り元とする。
イメージ内の初期LOGSはあっても適用しない。台帳・hosts・候補・観測・利用者ファイル・空の初期台帳をpayloadへ含めない。
`managed_files`はファイルごとの配置先・payloadパス・サイズ・SHAであり、ディレクトリ丸ごと置換の指示ではない。
payloadからコードや任意コマンドを実行しない。

`logical_inventory`にはEFI/MASTER/Fedoraの全論理ファイルを列挙する。
producerとhelperの実ファイルシステム検査で、EFI/GRUBのMASTER UUID参照、master.json、root.img、state.img、
kernel/initramfs、root内とUSB側の終了処理archive、Fedora initrd/管理ツール、公開証明書とtrust pin、MEDIA-INFOの整合を確認する。
MASTER上のUUIDが変わることは許すが、EFIと一緒に適用する。証明書は移行元と同一、boot ABI/state schemaも同一とする。
kernel変更は同じ束内のkernel/module/initramfs/rootの整合が実検査できる場合だけ許す。
`producer.commit`は完全な40桁の実commit、`worktree_sha256`は構築入力ツリーの識別SHAであり、
旧r3 commitを新0.99.0の出所と偽装しない。検証レポートに入力一覧とその集計方法・未commit差分の識別を記録する。
生成済み成果物の存在を新規構築の前提にしない。
worktree_sha256は構築に使ったrepo内の追跡済み通常ファイルと明示追加したソースについて、
`{path,bytes,sha256}`の配列をpath順に並べ、UTF-8/ensure_ascii=False/sort_keys/compact/末尾LFで符号化したSHAとする。
git管理情報、鍵、cache/vendor/build出力をこのソース一覧へ入れない。
外部入力と署名済み構築入力は別途検証レポートで取得定義・サイズ・SHAに結び付ける。

## 対応元と更新方向

`accepted_sources`は実検査可能な既知の配布元だけを列挙する。版文字列・ラベルだけの一致では許可しない。
各元にmaster.jsonとroot.imgのSHA、kernel/ABI/state、証明書、MASTER UUID、旧managedファイル一覧を持たせる。
helperが対象退避からその実体を照合する。実ハッシュを未採取の旧媒体を「対応済み」としない。

- `legacy-r3`: project `0.9.1`、root `0.9.1-shared`、revision `3`、台帳schema `1`。既存更新receiptなし。
- `built-media`: 統一版番号の初期構築媒体、台帳schema `2`、既存更新receiptなし。
- `updated-media`: 既知の過去更新manifest SHAと正の`update_sequence`をLOGS上のreceiptと照合する。

既存receiptなしの元では`update_sequence:0`、`prior_manifest_sha256`は64個の`0`（未存在の番兵値）。
統一版の`root_version`は`version`と同じ、`root_revision:0`はrevisionを使用しない番兵値とする。
新更新の`update_sequence`は配布者が一意・単調増加で割り当て、元updated-mediaより大きくする。
初回は1から始める。版の降格は拒否する。
同一manifest再適用は別の冪等性判定とし、既存receiptのmanifest SHAだけで成功にせず、全選択範囲の実内容も検証する。
自分自身のmanifest SHAをaccepted_sourcesへ埋め込まない。

## GPT・配置・選択

対応配置の論理セクタは512bytes。EFI64MiB、MASTER4608MiB、LOGS256MiB、Fedora3072MiB。
モデル領域は対象に存在するNTFS区画であり、元の4区画イメージから位置・長さ・GUIDを推測しない。
profileのモデル開始/長さ`0`とlabel `target-preserved`は現地検出の番兵値であり、実際に0へ変更する意味ではない。
既知の5区画だけを受け付け、重複・未知の追加区画・不足・GPT両コピー不整合・範囲外・誤type/FSを拒否する。
全GPT/MBR、disk GUID、各区画のunique GUID/attributes/geometry、区画外の全バイト、モデル全内容を保存する。
モデルのラベルやGUIDは固定値に置換しない。他区画のラベルはFS内で照合する。
本profileは既存の合成runtime用indexへ登録せず、raw worker対応までは無効のまま保持する。

利用者はcheck前に役割を選択する。required_rolesとcoherence_groupsを強制する。
EFI/MASTER/LOGSは必須、Fedoraは選択可能だが、省略時は対象の全区画SHAが更新束のFedora区画SHAと一致する場合だけ許可する。
一致しなければ選択を求めて再checkする。どの配置でもprotected役割は選択不可。
順序はlayoutのwrite_order。複数区画の原子的切替は保証しない。

## LOGS helperの固定操作

helperは別途ローカルに導入・ハッシュ固定した既知版を通常権限で実行する。更新束に同梱されたhelperを実行しない。
Windows高権限workerからWSLを起動しない。requestのhelper ID/版/SHAは、ローカルの信頼済み取得定義とも照合する。
helperにはデバイス名を渡さず、Windowsが退避したEFI/MASTER/LOGS/Fedoraの通常ファイルだけを渡す。
対象tokenはworker発行の接続同一性情報のSHA、GPT snapshot SHAと組み合わせる。disk番号だけで代用しない。

1. manifest/requestと全入力SHA、サイズ、source fingerprint、FAT/ext4構造と内部整合を照合する。
2. 元LOGSを保持したまま新規出力へコピーし、FAT UUIDと容量を維持して加工する。
3. 管理ファイルはlayoutの許可ルート/個別パスかつmanifestの明示一覧だけを更新する。
   既存ファイルの置換/削除は、accepted_sourceの旧所有一覧にあり旧SHAも一致する場合だけ許す。
   未所有ファイルと新payloadの衝突、旧管理ファイルの利用者変更は停止し、勝手に上書きしない。
   未知ファイルはtools/gui内でも保存する。remove_managed_filesは同じ旧所有照合後だけ削除する。
4. usb-config.jsonは既存の検証済みmarkerからroot版とregistry_schemaだけを更新する固定処理とする。
   新規媒体用markerで丸ごと置換しない。不明フィールドは保存する。
5. 台帳を下記規則で変換する。登録済みhostの派生.envだけを固定ロジックで再生成し、他のhostsファイルも保存する。
6. `gpu-config/updater-receipt.json`へmanifest生バイトSHA・sequence・releaseを記録する。
   この固定パスが既存の未所有ファイルと衝突する場合も停止する。
7. 出力FSと全ファイルを再検査し、前後inventoryとreceiptを新規生成する。

パスは相対forward slash、managedパスはASCIIで正規化済みとする。
リンク・絶対パス・`..`・ADS・DOSデバイス名・NFC/大文字小文字衝突・区画外参照・既存出力を拒否する。
未知のUnicode利用者名は読み書き対象に変換せず、前後inventoryで実名を保存する。
inventoryは実名、entry種別、ファイルサイズ/SHA、属性、FAT日時を含むJSON配列とし、パス順に並べる。
各entryのフィールドは`path`（実名の相対forward slash文字列）、`kind`（`file`または`directory`）、
`bytes`、`sha256`、`attributes`、`created_date`、`created_time`、`created_tenths`、
`modified_date`、`modified_time`、`accessed_date`だけとする。
数値はFATディレクトリentryの符号なし整数値をそのまま使い、UTCへの変換や丸めを行わない。
directoryはbytes=0/sha256=64個の0、fileは空ファイルも含め実サイズと生バイトSHAを使う。
volume label、`.`/`..` entry、削除済みentryは一覧に入れず、通常の下位directoryは含める。
未知ファイルの内容・属性・日時は一致を必須とする。FAT内の空き領域や割当て順の同一性は要求しない。
preserved_inventory_sha256は変更を許さないentryだけを同じ正規化（UTF-8、ensure_ascii=False、sort_keys、compact、末尾LF）で集計したSHA。
子の追加・変更で親directoryの日時が変わる場合、その親だけを保持集計から外し、attributesと名前は別途一致を確認する。
更新側はhelperのtrueフラグだけを信じず、入力binding、出力SHA/サイズ、実FSの内容と保持inventoryを独立照合する。

## 台帳schema 1→2（初版必須）

envelope/payloadのschemaとchecksumを検査し、破損・欠落・不明schema・重複登録・不正保存先を拒否する。
既存の有効な0件台帳は0件のまま保存してよい。初期空台帳を注入して既存台帳を消すことは禁止する。
確認日が未来・不正型、安全値が不正/不明なら停止し、既存の収集/確認手順で利用者が解決する。
現行fleet規則に合わせ、確認日の経過日数だけで失効させない。過去の観測を現在の安全確認済みと偽装しない。
Fast Startupの観測値は既存のbooleanをそのまま保存し、falseへ書き換えない。
更新処理は確認日・BitLocker/Fast Startup/Secure Boot等の安全値を更新・補完しない。

schema 1→2ではenvelope/payload schemaを2へ上げ、欠けている`windows_computer_name`にJSON nullを追加する。
既存PC名、全登録、hardware/NTFS UUID、保存先、boot mode、確認日、安全値、root_version由来情報、不明フィールドを保持する。
generationはちょうど1増やす。レコード順だけの違いを除き、上記以外の意味変更を拒否する。
既存convert/mergeが不明フィールドを落とす場合、単純流用せず保存を実装するか停止する。
schema 2→2では台帳ファイルを生バイトで保存し、generationも変えない。
OSのリリース更新だけを理由に台帳payloadのroot_versionを更新しない。
`preserved_records_sha256`はschema/generationと追加したnullフィールドを除いた
台帳payloadを、recordsをhardware_uuid順に整列し、上記正規化で集計した前後共通SHAである。
追加nullの除去は元レコードに存在しなかったフィールドだけに限る。元から存在するnullや不明フィールドも保持検査に含める。

## Windows側の適用条件と復旧

checkは形状と入力の事前検査、prepareは外部退避とhelperによる内部構造・移行の確定検査を行う。
applyを可能にするのはprepare完了後だけとする。prepare開始時点の退避と対象が一致しなければ停止する。
適用直前に接続token/GPT/全退避範囲SHA/安全属性を再検査し、全必要lockを取得する。
system/boot/pagefile/使用中/不明volume、外部退避先が対象USB内、同一性不明、lock失敗は拒否する。
高権限workerは許可範囲の固定I/Oのみ。GPT/保護区画への書込み、全体imgへのfallback、強制アンマウントは禁止する。
区画ごとに同期・読戻しSHAを確認し、全区画完了確認まで成功と表示しない。
外部退避とtransactionを保持し、再開/復元でも同一機器・入力SHA・工程状態を照合する。
中断後は再開/復元完了まで対象から起動しない。更新側の独立検査実装がなければ適用不可とする。

## 検証の境界

`python3 schemas/production-v1/validate.py manifest tests/fixtures/production-v1/manifest.json`
はメタデータ適合だけを検査する。fixtureのサイズ/SHA/UUIDは架空で、配布や適用には使用できない。
producer・FAT helperの実装、相互適合、実容量通常ファイル、Windows raw API、実USB/起動成功は別の完了条件である。
