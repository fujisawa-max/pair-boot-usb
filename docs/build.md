# 構築手順

LinuxまたはWSL2上で、版・URL・ハッシュを指定した外部資料を取得し、通常ファイルのUSBイメージを作ります。ホストへのGPUドライバ・kernel・bootloaderの導入や、実USBへの書込みは行いません。

構築のための最初の一手は `scripts/build-usb.py prepare` の実行です。`prepare` が外部資料の取得と検証、署名前のモジュールの構築、作業用ファイルの準備を行い、利用者が署名した後に `image` が組立と検査を行います。環境の準備から順に以下の手順を進めてください。現行は0.9.1試作版です。実機起動・GPU・PAIR参加・異常終了・root交換の実機確認は未完了です。ソース試験、ファイル構築・検査、実機確認は区別します。

## 1. 環境を用意する

x86_64（amd64）のLinux/WSL2、Python **3.11以上（Condaでは3.11を使用）**、HTTPS通信、64GiB以上の空き容量を用意します。ビルド先はLinuxのファイルシステム上に置き、bubblewrapが非特権user namespaceを利用できる環境を使います。隔離が使えない場合は停止し、隔離を外した構築へ自動で切り替えません。

リポジトリを取得し、そのルートで以後のコマンドを実行します。Anaconda/MinicondaをLinux側で利用できる場合は、専用環境を作成します。

```sh
conda env create -f environment.yml
conda activate pair-boot-build
python --version
```

既に `pair-boot-build` をPython 3.11以上で作成済みなら、再作成は不要です。`conda activate pair-boot-build` から進めてください。別の端末でも再度activateします。`environment.yml` はビルド用Python 3.11系列を選ぶ定義で、Conda環境全体の完全な固定lockではありません。構築に使う外部資料の版・URL・ハッシュは引き続き `inputs/` で管理します。

Condaを使わない場合は、`python3 --version` で3.11以上を確認し、以下の `python` を対応版の `python3` または `python3.11` に読み替えてください。`hashlib.file_digest` を使うため3.10では実行できません。OS標準の `/usr/bin/python3` やそのリンクは変更しません。署名・イメージ生成を含め、同じPythonを使います。`scripts/build-usb.py` が呼び出すPython処理にも選択した実行環境を引き継ぎます。

### OSツールを導入する

Ubuntu/Debian系では、利用者本人が次を実行します。先に対象一覧だけを確認できます。

```sh
PYTHON=python sh scripts/install-build-dependencies.sh --print
PYTHON=python sh scripts/install-build-dependencies.sh
```

このコマンドはsudoでAPTを更新し、bubblewrap（`bwrap`）、fakeroot、zstd、Git、GnuPG、OpenSSL、e2fsprogs、kmod等のホストツールを導入します。Ubuntu 22.04のOS標準Pythonが3.10のままでも、activate済みのConda Python 3.11で構築できます。sudoでPythonスクリプトを実行する必要はありません。

ホストツールはOSのAPT配布版を使用します。対象kernel、headers、コンパイラ、イメージ操作用ツール等は `prepare` が固定Debianパッケージから別のsysrootへ展開します。ホストにGPUドライバ、DKMS、kernel、bootloaderを導入しません。非APT環境では同等のコマンドを手動で用意する必要があり、すべてのディストリビューションでの構築を確認したものではありません。

### 環境確認とソース試験を行う

```sh
python scripts/check-environment.py
python scripts/run-tests.py
```

環境確認はPython版、Linux/x86_64、必要なコマンド、リポジトリを置いた領域の空き容量を読み取ります。取得前の空の作業環境で実行でき、target headersや `vendor/` は要求しません。不足するコマンドと対応するAPTパッケージを表示して終了1で停止します。`--json` で機械可読な結果も取得できます。`scripts/build-usb.py` の実行時にも同じ要件を検査します。隔離の実動作や外部資料の検証は後続の構築工程で行い、この確認だけで構築成功を保証しません。

`requirements.txt` は追加のpip導入が不要であることを記載したコメントのみのファイルです。`pip install -r requirements.txt` の実行は不要です。Fedora ISOの展開に使うpycdlibは、`inputs/fedora-tools-inputs.json` の固定wheelを `prepare` が取得・SHA検証して直接読み込みます。手動でpip導入する必要はありません。USB内のPAIR等の依存関係も、ホストのpip環境とは別に、指定された版の外部資料から準備します。

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

この工程で、以下を順に実行します。

1. `inputs/` のURL・版・SHA-256に従って、パッケージ、archive、Fedora ISO、公式公開鍵、抽出ツールを `.cache/` へ取得する。
2. fingerprintを照合した公式鍵で署名付きDebian/NVIDIA APT情報を検証し、固定したパッケージの版・SHA・公開パス等を照合する。ホストのAPT設定・鍵・インストール状態は変更しない。
3. `vendor/gpu-unlock` に固定commitを取得し、commit・作業ツリー・Gitオブジェクトを検査する。
4. Debianのビルド用sysrootとruntime baseを別々に展開し、対象kernel用の6つの未署名moduleを隔離環境で構築・検査する。
5. NVIDIA、PAIR、llama.cpp、ネットワーク、gzip/公開CA、署名済みshim/GRUBを展開する。ホストの私有CAやネットワーク設定はコピーしない。
6. 固定した上流generatorでfirmwareを生成し、出力のハッシュと帰属表示を確認する。
7. Fedoraの公式CHECKSUM署名とISOのSHAを確認し、固定したpycdlib wheelでLiveツリーを展開する。
8. 空のPC登録状態で使うruntime中間ツリーを `build/runtime-root` へまとめる。

kernelとheadersは版名付きのパッケージで固定し、追従用のkernel headersメタパッケージには依存しません。上流のinstallターゲットは実行しません。

検証済みcacheは再利用できます。破損cacheは自動上書きせず、取得失敗や固定版の消失時には停止します。途中のダウンロードは再実行時に最初から取得し直します。途中まで生成された出力は自動で削除・再使用しません。`prepare` が既存出力を理由に停止した場合はログを保持し、新しいソース作業環境で再試行してください。

### 検証の範囲

Debian/NVIDIAパッケージには、公式署名付きAPT情報からパッケージSHAへ至る照合を行います。Fedoraには公式鍵・CHECKSUM署名・ISOの照合を行います。これらの鍵の取得元とfingerprintは `inputs/public-keys.json` に記録しています。

gpu-unlockのソースは指定されたcommit、PAIR/llama.cpp・firmware生成用の外部資料・抽出wheelは指定されたSHAとHTTPS配布元で確認します。これらすべてに作者の電子署名を検証している、という意味ではありません。APT情報は有効期限を確認するためネットワークで更新します。指定された版が署名付きindexから消えた場合も、別版への自動更新は行いません。

参照：[Debianの署名鍵](https://ftp-master.debian.org/keys.html)、[Fedoraの検証方法と署名鍵](https://fedoraproject.org/security/)。

## 3. 利用者の鍵で署名する

[署名鍵の作成とmodule署名](secure-boot.md)を、利用者自身の端末で実行します。

- 初回はrepo・USBの外に暗号化秘密鍵と公開DER証明書を作る。
- 既存鍵を使う更新では、鍵を作り直さず署名する。
- 署名先は既定で `build/signed-modules`。署名前のモジュールは保持する。

エージェントへ秘密鍵やパスフレーズを渡す必要はありません。

## 4. USBイメージを生成・検査する

署名が終わったら次を実行します。

```sh
python scripts/build-usb.py image
```

別名の署名束を使う場合：

```sh
python scripts/build-usb.py image --signed-modules build/signed-modules-update
```

署名束と元moduleの整合性、payloadの不変、選択した証明書によるCMS署名を検査してから、GUI・空の共有台帳・MOK操作ツール、6GiBのroot.img、初期状態用の1GiBのstate.img、initramfs、署名済みEFIを組み合わせます。同じ公開証明書を署名束とGUI・MOKツールで使用します。秘密鍵はイメージ生成に使うデータにもコピー対象にも含めません。

最終出力は `build/usb-image/gpu-linux-usb.img` と `SHA256SUMS`、検査レポートです。GPT、FAT/ext4、initramfs、状態テンプレートのSHAと初期内容、イメージ内部の内容、パーティションの読戻しを検査します。ログは `build/image-01.log` 等です。

現在の開発ツリーでは、`config/usb-layout.json` でGPU-MASTERを4608MiB（4.5GiB）に設定し、生成イメージは約8.4GBです。0.9.1 revision 2までの従来の構成ではGPU-MASTERは8GiB、イメージ全体は約12.1GBです。実USBへ書くときは、使用するイメージ以上の実容量を持つ媒体を利用者が選びます。`scripts/build-usb.py` は実デバイスへの書込みを行いません。最終イメージができた後、[利用開始](quick-start.md)に進みます。

GPU-MASTERは配布元の領域です。root.imgの論理サイズ6GiBとstate.imgの1GiBは維持し、未使用部分を疎ファイルとして格納します。構築前にデータ領域の格納量を見積もり、穴の位置を取得できないファイルシステムでは論理サイズ全体で判定します。容量不足時は停止します。作成後はファイルシステムを検査し、予約ブロックを除いて512MiB以上の空きを確認します。結果は出力先の `master-capacity.json` に保存します。

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

合成fixtureとシェル構文を検査します。スキップは成功と区別し、環境不足による未検証範囲として扱います。構築・署名検査の成功だけでは、対象PCのMOK登録、Secure Boot起動、GPUのNormal/Unlock、PAIR参加、正常終了の実機確認にはなりません。

WindowsツールのPowerShell処理もLinux/WSL上で試験する場合は、任意の開発用ランナーを一度だけ準備します。

```sh
python scripts/prepare-powershell-tests.py
python scripts/run-tests.py
```

`inputs/powershell-test-input.json` のPowerShell 7.5.4をHTTPSで取得し、サイズ・SHAを照合して `build/powershell-7.5.4` へ展開します。既存出力は保持して停止するため、準備済みならテストだけを実行してください。x86_64 Linux/WSL用で、ホストへインストールせずUSBにも含めません。試験はその実行ファイルを自動検出し、WindowsストレージAPI等を合成fixtureで代用します。実WindowsのUSB列挙、FAT32書込み、GUI表示やUACの確認は別途必要です。

秘密鍵、生成日時、ファイルシステムUUID等が異なるため、別の利用者の完成USBと全体SHAが一致することは合格条件にしません。外部資料の版とハッシュ、処理、署名、内部内容、動作を検証します。新規インストールは過去のrootイメージに依存せず、回復が必要なときは元を保持したコピー上でfsckする設計です。

`.cache/`、`vendor/`、`build/`、鍵・証明書やPC登録情報は公開Gitへ追加しません。

## 0.9.1試作の確認

NVIDIA GPUのないPCで起動画面・root/stateの分離を調べる場合は、[GPUなし確認起動](no-gpu-test.md)を使用します。同じ版のrootテンプレートを保持して起動部分だけを更新する手順もあります。通常起動の代わりに自動実行するモードではありません。

0.8以前の保存先を上書きせず、登録時に `GPU-Linux-0.9` などの新しいフォルダを選びます。初回起動時にrootとstateのテンプレートをコピーし、そのPCの識別情報を初期化します。既存のrootしかない保存先では停止します。署名鍵は従来の管理済み鍵を使えますが、対象kernel/moduleや公開証明書の整合性は通常どおり検査します。

起動バナー、Normal/Unlock表示、PAIRの起動・終了・再起動、PC再起動後の設定保持、正常終了を順に確認します。異常終了後にプロセスが残る場合は次のPAIR起動を止めます。全対象GPU・全故障条件を確認済みとはしていません。

root交換は0.9以降の同じstate schemaを使う更新に限ります。0.9.1の現在の構築はroot revision 3で、独立したリビジョンを使い、0.9.0の既存rootはrevision 0として扱います。revision 3は、終了時に解除済みのstate loopを再解除して電源断を止める問題を修正します。終了用initramfsもroot内に含まれるため、この修正にはroot更新が必要です。実機での更新確認には、別の対応版のUSBと、その版に合わせた正規のPC登録が必要です。版番号や登録情報だけを書き換えて更新試験を通さないでください。詳しくは[運用と回復](user-guide.md)を参照します。

## 構成情報とrootリビジョン

`config/release.json` がプロジェクト版、root release、root revision、boot ABI、state schemaの定義です。rootを変更するときはrevisionを増やします。同じrevisionで異なるSHAを公開すると既存環境は停止します。完成rootを再生成するとUUID等でもSHAが変わるため、同じリビジョンのrootを作り直して既存配布物と混在させないでください。

`GPU-LOGS/MEDIA-INFO.txt` とFedoraパーティション直下に、使用した外部資料の版などをまとめた構成一覧を配置します。Debian、kernel、gpu-unlock commit、NVIDIA、PAIR、llama.cpp、Fedoraの版を確認できます。これは媒体の情報であり、起動中rootの情報とは区別します。バナーはroot内の同じ形式のメタデータから版・revisionを、起動状態からNormal/Unlock/TESTを表示します。

boot ABIはinitramfs・root・stateの取り決めです。kernelだけでなくGPUドライバとgpu-unlockの版も照合します。起動・終了・設定保存の互換性を変える開発では、ABIとstate schemaを見直して、互換性のない組合せを許可しないでください。
