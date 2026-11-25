# 部分更新用manifestと管理ファイルの生成

[English](update-bundle.md)

構築時に、更新契約 **1.1** に対応する更新束を生成できます。全体イメージ、`manifest.json`、明示された管理ファイル、検証レポートをまとめます。この処理は実USBへ書き込みません。適用には、この契約に対応するupdaterと、別途信頼して導入するLOGS加工helperが必要です。適用側の実装はupdaterプロジェクトで行います。

## 構築時に生成する

[構築手順](../build.ja.md)に従って準備とモジュールへの署名を済ませます。保管済みの旧版がない場合は、sequence 1から始めます。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1
```

構築時に全体イメージを作業先に生成します。更新束の検証が完了したら、書き込みにも更新にも、保管先の `images/gpu-linux-usb.img` を使います。

- `build/usb-image-source.json`：実際のGit基準commit、公開ソースの一覧、未コミット変更の識別情報。
- `build/usb-image-inspection-tools/`：固定したDebian検査ツールの展開先。
- `build/update-bundles/<version>/standard/sequence-1/manifest.json`：更新契約と各ファイルのハッシュ。
- `build/update-bundles/<version>/standard/sequence-1/images/`：選択区画の読取り元として使う全体イメージ。
- `build/update-bundles/<version>/standard/sequence-1/payload/`：明示された個別の管理ファイル。
- `build/update-bundles/<version>/standard/sequence-1/reports/verification.json`：ソース、外部データ、署名、ファイルシステムの検証記録。
- 同じ `reports/` の `shared-media-verification.json`：初回書き込み媒体を更新元に指定する際に使う読み戻し記録。

更新束はプロジェクトの `build/update-bundles/<version>/<profile>/sequence-N/` に保存します。別の作業コピーで構築する場合、`--source-repository` で指定したリポジトリの `build/` が基準です。`--update-output` で新しい出力先を指定することもできます。

更新束を今後も更新元として使う場合は、フォルダ全体を残すか、別のディスクへ移してください。manifestだけでは再検査できず、`images/`・`payload/`・`reports/` が必要です。保存した束は、その版を初回書き込みした媒体の検査にも `--accepted-image-directory` で指定できます。

出力先はすべて新規の、互いに重ならない場所を指定します。`--plan` で実行予定を確認できます。`--update-bundle` を付けなければ、従来どおり全体イメージだけを生成します。

同じファイルシステム上ではイメージを複製せずに保管し、検証成功後に作業先のファイル名を取り除きます。別のファイルシステムへの出力ではコピーを検証してから作業先のイメージを取り除きます。検査中はroot等を一時展開します。失敗した場合は作業先のイメージと途中の出力を保持します。

新規ソースは構築前にコミットするか、`--source-extra PATH` を繰り返して未追跡の公開ソースを明示します。追跡済みファイルの未コミット変更はレポートで識別します。鍵、非公開メモ、cache、取得した第三者ソース、生成物は公開ソース一覧へ含めません。

## 対応する更新元を指定する

新しいイメージに加え、既定では保管先から同じ種類の最新旧版を選びます。同じ旧版に複数の更新束がある場合は、完成済みのうち最大のsequenceを使います。通常版では、その旧版へ部分更新済みの媒体を更新元にします。貸出版では、その旧版のイメージを初回書き込みした媒体を更新元にします。新しいsequenceには、選ばれた更新束より大きな値を指定します。`--plan` で使う更新元を確認できます。

`--accepted-bundle` や `--accepted-image-directory` で更新元を指定すると、既定の選択に代わります。旧版を更新元に含めず構築するには `--no-previous-source` を指定します。以前の初期構築媒体を明示する例は次のとおりです。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1 \
  --accepted-image-directory build/previous-image
```

全体イメージと `shared-media-verification.json` が必要です。対応する区画イメージがあれば、全体イメージ内の範囲とハッシュを照合して読み取り専用で利用します。配置、内部ファイル、kernel/module、公開証明書、root/stateの情報、旧管理ファイルの所有一覧を実検査します。ラベルや版名だけで対応とは判断しません。旧版は `0.9.1-shared`・root revision 3・台帳形式1を対象とし、実物を検査できた場合だけ列挙します。欠けている旧成果物を合成fixtureのハッシュで補いません。

既知の更新束を適用した媒体からさらに更新する場合は、そのmanifestを指定し、sequenceを増やします。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 4 \
  --accepted-bundle build/update-bundles/0.99.1/standard/sequence-3/manifest.json
```

元のイメージ、payload、レポート、版の識別情報、論理ファイル一覧を再検査します。`update_sequence` は配布系統内で重複しない単調増加の番号にします。版の降格と、未対応の配置・証明書・boot ABI・state形式の変更は拒否します。

Updaterはリリース指紋と旧管理ファイルのパス・サイズ・SHA-256で更新元を選んだ後、更新記録を検査します。同じ指紋と管理ファイルを持つ初回書き込み媒体と更新済み媒体を併記すると候補を一意に選べません。配布対象に合わせ、初回媒体か既知の更新記録を持つ媒体のどちらか一方を指定してください。

## 0.9.3のWindows worker保守修正版を追加する

0.9.3の起動用ファイルとrootを保持し、台帳マージで保存先USBを指定しない場合の修正だけを `gui/windows-worker.ps1` へ反映した媒体を、追加の更新元として指定できます。元の0.9.3を残し、保守修正版には `source-0-9-3-r2-worker-merge-fix` という別のIDを付けます。project/rootの版は0.9.3のままです。

元の0.9.3イメージと、保守修正版について受領した `accepted-source.json` を明示します。例えば、構築時のソース記録と検査ツールを保持している既存イメージでは、次を実行します。出力先は新規の場所、sequenceは配布済みの値より大きい値にしてください。

```sh
python3 scripts/prepare-update-bundle.py \
  --image-directory build/usb-image \
  --source-record build/usb-image-source.json \
  --source-root . --signed-modules build/signed-modules \
  --accepted-image-directory build/usb-image-0.9.3-official \
  --accepted-worker-merge-fix build/source-variant-0.9.3-r2-worker-merge-fix/accepted-source.json \
  --output build/update-bundle-worker-merge-fix --update-sequence 2
```

新規構築の `scripts/build-usb.py image --update-bundle` にも同じ指定を渡せます。原イメージを実検査し、`inputs/updater-worker-merge-fix.json` に固定した修正前後のworkerを実在するGitコミットと照合します。受領レコードが、元の情報からIDとそのworkerだけを変更したものと一致する場合だけ追加します。任意のファイル・ハッシュの上書きには使えません。必要なGitコミットが取得されていない場合や、原イメージがない場合は停止します。

**この更新束の適用にはupdater 1.0.7以降が必要です。** 同じ起動情報を持つ更新元を、LOGSの宣言された旧管理ファイル全件の存在・サイズ・SHAで一意に識別するためです。旧ツールは曖昧な更新元として停止します。契約1.1の `minimum_updater` は1.0.0固定なので変更せず、実際の利用条件をこの手順と検証レポートに記録します。

配布する0.99.0イメージやpayloadに変更がなければ、rootやUSBイメージを再構築する必要はありません。既存の更新束を保持し、sequenceを増やして新しい出力先へ生成します。`accepted_sources` への追加は、未知の利用者変更まで許可するものではありません。

## 既存イメージについて生成する

署名済みの既存イメージについて、rootを再構築せずに生成できます。構築時のソース記録、実際のソースツリー、外部データのcache、署名済みモジュール、イメージの検証レポートを保持しておきます。検査ツールは最初に一度だけ準備します。

```sh
python3 scripts/prepare-updater-inspection.py
```

元の構築情報と新しい出力先を指定します。

```sh
python3 scripts/prepare-update-bundle.py \
  --image-directory build/usb-image \
  --source-record build/usb-image-source.json \
  --source-root . --signed-modules build/signed-modules \
  --output build/update-bundle --update-sequence 1
```

ソース記録は、そのイメージの構築に使った実ファイルに対応している必要があります。`scripts/record-build-source.py` で実際のGit基準commitとソースのハッシュを記録できます。`--source-hashes` は明示して保持していた旧来のパスとSHA-256の一覧を、実ファイルと照合して取り込むための指定です。`--commit` には実際の基準commitを指定します。版名だけから失われた構築情報を復元するものではありません。

## 保持する範囲

EFI・MASTER・LOGSは必須の更新対象です。Fedoraは契約の選択規則に従います。第5のNTFS区画は利用者の領域として保持し、実際の位置と容量はupdaterが検出します。4区画の構築イメージから第5区画の情報を推測しません。

管理ファイルのpayloadには、台帳、hostの派生ファイル、候補・観測情報、署名証明書、利用者データを含めません。`tools/` や `gui/` を丸ごと置き換えず、所有を確認した個別ファイルを列挙します。台帳の移行や、不明な利用者ファイル・日時・属性・設定の保持は、別途検証するLOGS加工helperの担当です。この生成処理では台帳を移行しません。

完成した更新束、証明書、PC情報、検査ログはGitや公開Releaseへ登録しません。SHA-256はバイト列の識別であり、配布者の真正性を保証しません。利用者が信頼するローカルの構築物を明示して使います。

固定したschemaと検証規則は、リポジトリ内の `inputs/updater-contract/v1/schemas/production-v1/README.md` を参照してください。通常ファイルの検査や契約試験の成功は、Windowsの実デバイスへの適用や実機起動の確認とは区別します。

## 既存USBに部分更新を適用する

1. 対象USBに必要なデータがあれば、別の場所へバックアップします。更新束の中のファイルは、フォルダ構成を保ったまま使います。
2. Windowsで、使う媒体の種類と更新契約に対応した `pair-boot-usb-updater` のGUIを開きます。更新元イメージに `images/gpu-linux-usb.img` を選ぶと、updaterが同じ更新束の `manifest.json` を探します。
3. 対象USBを選び、型番・容量と、表示された更新対象を確認してから書き込みを開始します。最終検証の完了を確認し、安全に取り外して起動を試します。

インストールや中断後の再開・復元に関する詳しい手順は、使用するupdaterに付属する `docs/usb-update.md` を参照してください。
