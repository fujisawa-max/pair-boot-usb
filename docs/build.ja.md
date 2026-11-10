# 構築手順

[English](build.md)

この構築手順によりLinuxまたはWSL2上で、USBから起動するためのイメージファイルを作ります。構築を行うPCにGPUドライバ・kernel・bootloaderをインストールすることはありません。また、ここのツールでは実USBへの書込みは行いませんので別途書き込みツールが必要です。

構築のための最初の一手は `scripts/build-usb.py prepare` の実行です。引数 `prepare` を指定することで外部資料の取得と検証、署名前のモジュールの構築、作業用ファイルの準備を行います。そして、構築した6つのGPUドライバ用モジュールに利用者が署名した後に、`image` を引数として `scripts/build-usb.py` を実行することでイメージの構築と検査を行います。実際の構築は、環境の準備から順に以下の手順で進めてください。

## 1. 環境を用意する

x86_64のLinux/WSL2、Python **3.11以上（Condaでは3.11を使用）**、HTTPS通信、64GiB以上の空き容量を用意します。ビルド先はLinuxのファイルシステム上に置きます。構築にはbubblewrapというツールを使い、構築処理からホスト環境へのアクセスを制限します。この実行環境を一般ユーザーの権限で用意できない場合は、構築を停止します。

リポジトリを取得し、そのルートで以後のコマンドを実行します。Anaconda/MinicondaをLinux側で利用できる場合は、専用環境を作成します。

```sh
conda env create -f environment.yml
conda activate pair-boot-build
python --version
```

既に `pair-boot-build` をPython 3.11以上で作成済みなら、再作成は不要です。`conda activate pair-boot-build` から進めてください。別の端末でも再度activateします。`environment.yml` はビルド用Python 3.11系列を選ぶ定義で、Condaが導入する全パッケージのバージョンを細かく固定するものではありません。

Condaを使わない場合は、`python3 --version` で3.11以上を確認し、以下の `python` を対応版の `python3` または `python3.11` に読み替えてください。`hashlib.file_digest` を使うため3.10以下では実行できません。署名・イメージ生成を含め、同じPythonを使います。

### 開発ツールの導入

Ubuntu/Debian系では次を実行します。先に対象一覧だけを確認できます。

```sh
PYTHON=python sh scripts/install-build-dependencies.sh --print
PYTHON=python sh scripts/install-build-dependencies.sh
```

このコマンドはsudoでAPTのパッケージ一覧を更新し、bubblewrap（`bwrap`）、fakeroot、zstd、Git、GnuPG、OpenSSL、e2fsprogs、kmod等の構築用ツールを導入します。Ubuntu 22.04のOS標準Pythonが3.10のままでも、activate済みのConda Python 3.11で構築できます。

構築用のLinux／WSL側で実行するツールは、Ubuntu／DebianがAPTで提供するパッケージを使用します。USBで使うカーネル、そのヘッダーファイル、構築用コンパイラ、イメージ操作用ツール等は、`prepare` が指定版のDebianパッケージを取得し、`build/` 内の専用フォルダへ展開して使います。構築を行うPCにGPUドライバ、DKMS、カーネル、bootloaderをインストールすることはありません。

### 環境確認とソース試験を行う

```sh
python scripts/check-environment.py
python scripts/run-tests.py
```

環境確認（`check-environment.py`）はPythonのバージョン、OSとCPUの種類、必要なコマンド、リポジトリを置いた領域の空き容量を確認します。要件を満たさない場合は理由を表示し、コマンドが不足する場合は対応するAPTパッケージも示して、終了コード1で停止します。`--json` で機械可読な結果も取得できます。`scripts/build-usb.py` の実行時にも同じ要件を検査します。

`requirements.txt` は追加のpip導入が不要であることを記載したコメントのみのファイルです。`pip install -r requirements.txt` の実行は不要です。FedoraのISOファイルから必要なファイルを取り出すために、構築スクリプトがPythonライブラリのpycdlibを自動で取得します。指定されたバージョンとSHA-256を確認し、取得したファイルから読み込むため、手動でのインストールは不要です。USB内のPAIR等の依存関係も、ホストのpip環境とは別に、指定されたバージョンを外部から準備します。

参照：[Condaの環境ファイルと切替](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html)、[Pythonのfile_digest仕様](https://docs.python.org/3.11/library/hashlib.html#hashlib.file_digest)。

## 2. 外部資料を取得して署名前まで構築する

空の `build/` から次を実行します。通常ファイルの検査用ログは `build/prepare-01.log` 等へ保存されます。

```sh
python scripts/build-usb.py prepare
```

実行するコマンドだけを見る場合：

```sh
python scripts/build-usb.py prepare --plan
```

このスクリプトの中で、以下を順に実行します。

1. `inputs/` の取得元URL・バージョン情報・SHA-256などの検証情報に従って、パッケージ、archive、Fedora ISO、公式公開鍵、抽出ツールを取得し `.cache/` へ格納する。
2. fingerprintを照合した公開鍵で署名付きDebian/NVIDIA APT情報を検証し、固定したパッケージの版・SHA・公開パス等を照合する。
3. gpu-unlockの指定されたcommitを `vendor/gpu-unlock` へ取得し、commit・作業ツリー・Gitオブジェクトを検査する。
4. Debianのビルド用sysrootとruntime baseを別々に展開し、対象kernel用の6つの未署名moduleを隔離環境で構築・検査する。
5. NVIDIA、PAIR、llama.cpp、ネットワーク、gzip/公開CA、署名済みshim/GRUBを展開する。
6. 固定した上流generatorでfirmwareを生成し、出力のハッシュと帰属表示を確認する。
7. Fedoraの公式CHECKSUM署名とISOのSHAを確認し、固定したpycdlib wheelでLiveツリーを展開する。
8. 空のPC登録状態で使うruntime中間ツリーを `build/runtime-root` へまとめる。

カーネルと、GPUドライバの構築に必要なヘッダーファイルは、使用するバージョンを指定して取得します。また、取得したソースに付属するインストール用コマンド（make installなど）は実行しません。

ダウンロード済みのファイルは、検証に合格していれば再利用できます。破損が見つかった場合は自動で上書きせず、処理を停止します。ダウンロードに失敗した場合や、指定したバージョンが配布元から取得できなくなった場合も停止します。ダウンロードが途中で止まったファイルは、再実行時に最初から取得し直します。

構築途中で作成されたファイルやフォルダは、自動で削除したり、次の構築に使い回したりしません。 `prepare` が「出力先が既に存在する」という理由で停止した場合は、調査用のログを残し、リポジトリを別のフォルダへ新しくcloneして、構築をやり直してください。

### 検証の範囲

Debian/NVIDIAパッケージには、公式署名付きAPT情報からパッケージSHAへ至る照合を行います。Fedoraには公式鍵・CHECKSUM署名・ISOの照合を行います。これらの鍵の取得元とfingerprintは `inputs/public-keys.json` に記録しています。

gpu-unlockのソースは指定されたcommit、PAIR/llama.cpp・firmware生成用の外部資料・抽出wheelは指定されたSHAとHTTPS配布元で確認します。これらすべてに作者の電子署名を検証している、という意味ではありません。APT情報は有効期限を確認するためネットワークで更新します。指定された版が署名付きindexから消えた場合も、別版への自動更新は行いません。

参照：[Debianの署名鍵](https://ftp-master.debian.org/keys.html)、[Fedoraの検証方法と署名鍵](https://fedoraproject.org/security/)。

## 3. 利用者の鍵で署名する

[署名鍵の作成とmodule署名](secure-boot.md)を、利用者自身の端末で実行。

- 初回に、このリポジトリおよびUSBの外に暗号化秘密鍵と公開DER証明書を作成。
- 既存鍵を使う更新では、鍵を作り直さず署名する。
- 署名先はデフォルトで `build/signed-modules` とする。署名前のモジュールは保持する。

エージェントへ秘密鍵やパスフレーズを渡す必要はありません。

## 4. USBイメージを生成・検査する

署名が終わったら次を実行。

```sh
python scripts/build-usb.py image
```

別名の署名束を使う場合：

```sh
python scripts/build-usb.py image --signed-modules build/signed-modules-update
```

署名束と元moduleの整合性、payloadの不変、選択した証明書によるCMS署名を検査してから、GUI・空の共有台帳・MOK操作ツール、6GiBのroot.img、初期状態用の1GiBのstate.img、initramfs、署名済みEFIを組み合わせます。同じ公開証明書を署名束とGUI・MOKツールで使用します。

最終出力は `build/usb-image/gpu-linux-usb.img` と `SHA256SUMS`、検査レポートです。GPT、FAT/ext4、initramfs、状態テンプレートのSHAと初期内容、イメージ内部の内容、パーティションの読戻しを検査します。ログは `build/image-01.log` 等です。

USBに同梱する利用者向けMarkdown手順書は、当面は日本語版を使います。コピー先では`.ja`を外し、日英版の切替リンクを除いて、文書間のリンクも同梱ファイル名へ変更します。GUIから案内する`QUICK-START.md`や`DETAILS.md`等の名前は維持し、リンク先の手順書も一緒に`GPU-LOGS/tools/`とFedoraの`tools/`へ配置します。リポジトリ内の原稿と、第三者のライセンス表示は変更しません。

現在の開発ツリーでは、`config/usb-layout.json` でGPU-MASTERを4608MiB（4.5GiB）に設定し、生成イメージは約8.4GBです。0.9.1 revision 2までの従来の構成ではGPU-MASTERは8GiB、イメージ全体は約12.1GBです。実USBへ書くときは、使用するイメージ以上の実容量を持つ媒体を利用者が選びます。`scripts/build-usb.py` は実デバイスへの書込みを行いません。最終イメージができた後、[利用開始](quick-start.ja.md)に進みます。

GPU-MASTERは配布元の領域です。root.imgの論理サイズ6GiBとstate.imgの1GiBは維持し、両ファイルをスパースファイル（未使用部分の保存容量を節約する形式）として格納します。構築前にデータ領域の格納量を見積もり、未使用部分の位置を取得できないファイルシステムでは論理サイズ全体で判定します。容量不足時は停止します。作成後はファイルシステムを検査し、予約ブロックを除いて512MiB以上の空きを確認します。結果は出力先の `master-capacity.json` に保存します。

余りの領域をモデルの手動配布用に使う場合、利用者がUSB上に追加のNTFS領域を作成してモデルをコピーします。構築ツールはその追加領域を自動作成しません。配布用モデルの置き場と、起動時に使用する登録済みのroot・state・Models保存先は別に扱います。

既存出力を保持して再組立する場合は、すべて新しい名前を指定します。

```sh
python scripts/build-usb.py image \
  --appliance-output build/appliance-next \
  --tools-output build/shared-usb-tools-next \
  --fedora-output build/fedora-live-tools-next \
  --output build/usb-image-next
```

これらの出力は `build/` 内の、互いに重ならない新規ディレクトリに限定します。

## ソース試験と実機試験

```sh
python scripts/run-tests.py
```

テスト用に作成したデータやファイルを使って動作を確認し、シェルスクリプトの構文も検査します。スキップは成功と区別し、環境不足による未検証範囲として扱います。構築・署名検査の成功だけでは、対象PCのMOK登録、Secure Boot起動、GPUのNormal/Unlock、PAIR参加、正常終了の実機確認にはなりません。

WindowsツールのPowerShell処理もLinux/WSL上で試験する場合は、任意の開発用ランナーを一度だけ準備します。

```sh
python scripts/prepare-powershell-tests.py
python scripts/run-tests.py
```

`inputs/powershell-test-input.json` のPowerShell 7.5.4をHTTPSで取得し、サイズ・SHAを照合して `build/powershell-7.5.4` へ展開します。既存出力は保持して停止するため、準備済みならテストだけを実行してください。x86_64 Linux/WSL用で、ホストへインストールせずUSBにも含めません。試験はその実行ファイルを自動検出し、Windows固有のストレージ操作等をテスト用の処理に置き換えて確認します。

秘密鍵、生成日時、ファイルシステムUUID等が異なるため、別の利用者の完成USBと全体SHAが一致することは合格条件にしません。外部資料の版とハッシュ、処理、署名、内部内容、動作を検証します。新規インストールは過去のrootイメージに依存せず、回復が必要なときは元を保持したコピー上でfsckする設計です。

`.cache/`、`vendor/`、`build/`、鍵・証明書やPC登録情報は公開Gitへ追加しません。

## 構築後の起動確認

NVIDIA GPUのないPCで起動画面・root/stateの分離を調べる場合は、[GPUなし確認起動](no-gpu-test.md)を使用します。同じ版のrootテンプレートを保持して起動部分だけを更新する手順もあります。通常起動の代わりに自動実行するモードではありません。

本システムでは、Linux本体を含むrootイメージと、設定情報などrootのバージョンが変わっても維持する情報を入れるstateイメージからなります。初回起動時にはrootとstateのテンプレートをコピーし、そのPCの設定情報を初期化します。署名鍵は従来の管理済み鍵を使えますが、対象kernel/moduleや公開証明書の整合性は通常どおり検査します。

起動バナー、Normal/Unlock表示、PAIRの起動・終了・再起動、PC再起動後の設定保持、正常終了を順に確認します。異常終了後にプロセスが残る場合は次のPAIR起動を止めます。

root交換は0.9以降の同じstate schemaを使う更新に限ります。0.9.1の現在の構築はroot revision 3で、独立したリビジョンを使い、0.9.0の既存rootをrevision 0として扱います。revision 3では、終了時に解除済みのstate loopを再解除して電源断を止める問題を修正しました。詳しくは[利用方法](user-guide.md)を参照します。

## 構成情報とrootリビジョン

`config/release.json` がプロジェクト版、root release、root revision、boot ABI、state schemaの定義です。rootを変更するときはrevisionを増やします。同じリビジョンで配布元のSHA-256が異なる場合は、起動を停止します。完成rootを再生成するとUUID等でもSHAが変わるため、rootを作り直す場合もリビジョンを増やし、既存配布物と区別してください。

`GPU-LOGS/MEDIA-INFO.txt` とFedoraパーティション直下に、使用した外部資料の版などをまとめた構成一覧を配置します。Debian、kernel、gpu-unlock commit、NVIDIA、PAIR、llama.cpp、Fedoraの版を確認できます。これは媒体の情報であり、起動中rootの情報とは区別します。バナーはroot内の同じ形式のメタデータから版・revisionを、起動状態からNormal/Unlock/TESTを表示します。

boot ABIはinitramfs・root・stateの取り決めです。kernelだけでなくGPUドライバとgpu-unlockの版も照合します。起動・終了・設定保存の互換性を変える開発では、ABIとstate schemaを見直して、互換性のない組合せを許可しないでください。

確認済みの範囲と残っている確認項目は [TODO](../TODO.md) にまとめています。
