# 部分更新用manifestと管理ファイルの生成

[English](update-bundle.md)

構築時に、更新契約 **1.1** に対応する更新束を生成できます。全体イメージ、`manifest.json`、明示された管理ファイル、検証レポートをまとめます。この処理は実USBへ書き込みません。適用には、この契約に対応するupdaterと、別途信頼して導入するLOGS加工helperが必要です。適用側の実装はupdaterプロジェクトで行います。

## 構築時に生成する

[構築手順](build.ja.md)に従って準備とモジュールへの署名を済ませ、次を実行します。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1
```

通常の全体イメージは `build/usb-image/gpu-linux-usb.img` に作成します。追加の出力は次のとおりです。

- `build/usb-image-source.json`：実際のGit基準commit、公開ソースの一覧、未コミット変更の識別情報。
- `build/usb-image-inspection-tools/`：固定したDebian検査ツールの展開先。
- `build/usb-image-update-bundle/manifest.json`：更新契約と各ファイルのハッシュ。
- `build/usb-image-update-bundle/images/`：選択区画の読取り元として使う全体イメージ。
- `build/usb-image-update-bundle/payload/`：明示された個別の管理ファイル。
- `build/usb-image-update-bundle/reports/verification.json`：ソース、外部データ、署名、ファイルシステムの検証記録。

出力先はすべて新規の、互いに重ならない場所を指定します。`--plan` で実行予定を確認できます。`--update-bundle` を付けなければ、従来どおり全体イメージだけを生成します。

更新束には全体イメージをもう1部保存し、検査中はroot等も一時展開します。更新元を追加するとその検査にも空き容量が必要です。失敗時の出力と検査用ファイルは調査のため保持するので、ログを確認し、新しい出力先で再試行します。

新規ソースは構築前にコミットするか、`--source-extra PATH` を繰り返して未追跡の公開ソースを明示します。追跡済みファイルの未コミット変更はレポートで識別します。鍵、非公開メモ、cache、取得した第三者ソース、生成物は公開ソース一覧へ含めません。

## 対応する更新元を指定する

既定では、その束で実検査した同じ版の初期媒体を識別します。古いUSBや未知のUSBまで自動的に対応するわけではありません。以前の初期構築媒体からの更新に対応する場合は、その検査済みイメージのディレクトリを明示します。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1 \
  --accepted-image-directory build/previous-image
```

全体イメージと `shared-media-verification.json` が必要です。対応する区画イメージがあれば、全体イメージ内の範囲とハッシュを照合して読み取り専用で利用します。配置、内部ファイル、kernel/module、公開証明書、root/stateの情報、旧管理ファイルの所有一覧を実検査します。ラベルや版名だけで対応とは判断しません。旧版は `0.9.1-shared`・root revision 3・台帳形式1を対象とし、実物を検査できた場合だけ列挙します。欠けている旧成果物を合成fixtureのハッシュで補いません。

既知の更新束を適用した媒体からさらに更新する場合は、そのmanifestを指定し、sequenceを増やします。

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 2 \
  --accepted-bundle build/previous-update-bundle/manifest.json
```

元のイメージ、payload、レポート、版の識別情報、論理ファイル一覧を再検査します。`update_sequence` は配布系統内で重複しない単調増加の番号にします。版の降格と、未対応の配置・証明書・boot ABI・state形式の変更は拒否します。

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
