# 署名鍵とSecure Boot

MOK登録に使う公開鍵（証明書）およびモジュール署名に使う秘密鍵のペアは、**利用するPCの管理者（あるいは管理者の許諾の元で利用者）が作成**してください。秘密鍵はPCのセキュリティ上極めて重要です。USB媒体やリポジトリ、共有ストレージ等には絶対に配置せず、適切なアクセス制限を設けた安全な場所で**適切に管理**してください。構築ツールはMOK登録を自動実行しません。

構築時と同じPython 3.11以上を使います。以下は `python3` の例です。Conda環境ではactivateして `python` に読み替えてください。

## 署名鍵を用意する

OpenSSLのあるLinuxで、repoとUSBの外にある管理された保管先を選びます。次の絶対パスは自分の保管先に変更してください。親ディレクトリは事前に用意し、まだ存在しない鍵専用ディレクトリを指定します。**このコマンドは利用者本人が端末で実行します。**

```sh
python3 scripts/create-signing-key.py \
  --directory /path/to/controlled-storage/pair-boot-signing
```

OpenSSLが秘密鍵のパスフレーズを尋ねます。秘密鍵・パスフレーズをGit、チャット、USB、root.imgに置かないでください。証明書の有効期間と更新・失効・バックアップは運用に合わせて決めます。

スクリプトはRSA 3072bit、有効期間3650日、SHA-256の証明書を作ります。秘密鍵はOpenSSLの既定の暗号化を使い、保存ディレクトリを0700、作成時umaskを077とします。既存ディレクトリやリンクを拒否します。失敗時は途中の出力を保持するので、再試行前に利用者が保管先を確認してください。USB上かどうかの自動判定は行わないため、保管先の選択は利用者が確認します。

公開証明書を表示する例：

```sh
openssl x509 -inform DER -in /path/to/controlled-storage/pair-boot-signing/module-signing.cert.der \
  -noout -subject -dates -fingerprint -sha256
```

秘密鍵は所有者だけが読める通常ファイルとします。`scripts/sign-gpu-modules.py` はrepo外の秘密鍵とDER証明書を指定して、構築済みmoduleのコピーに署名します。暗号化鍵のパスフレーズは所有者が安全な入力方法で `KBUILD_SIGN_PIN` に渡します。コマンドラインやログへ直接書きません。署名前のモジュールの準備は [build.ja.md](build.ja.md) に従います。

## 構築したmoduleへ署名する

6つの未署名moduleとDebianのsign-fileが揃った後、利用者がリポジトリのルートで実行します。対話端末でパスフレーズを入力します。コマンドの引数やログへパスフレーズを書かないでください。

```sh
python3 scripts/sign-gpu-modules.py \
  --key /path/to/controlled-storage/pair-boot-signing/module-signing.key.pem \
  --cert /path/to/controlled-storage/pair-boot-signing/module-signing.cert.der \
  --purpose production --prompt --output build/signed-modules
python3 scripts/verify-signed-module-bundle.py build/signed-modules
```

`--prompt` は対話端末を要求し、パスフレーズを署名ツールの `KBUILD_SIGN_PIN` として一時的に渡します。署名スクリプト終了時に環境変数を消去または元の状態へ戻します。パスフレーズはファイルへ保存しません。安全に設定した既存の `KBUILD_SIGN_PIN` を使う場合は `--prompt` を省略できます。

署名処理は元moduleを保持し、コピーへ署名します。名前・kernel/driver版、元payloadの不変、選択した証明書によるCMS署名を検証します。確認済みの公開証明書と署名manifestは出力束に含め、秘密鍵はコピーしません。利用者による署名から最終イメージ生成・検査までの実行を確認済みです。署名束だけではUSB生成に必要な他のファイルは揃いません。

## 対象PCへ登録する

通常版USBでは、MOK登録用の公開証明書を `GPU-LOGS/signing-cert.der` に置きます。貸出版を使うPCのMOK登録も通常版で行います。秘密鍵はUSBへコピーしません。

Fedoraの管理GUIで証明書と登録状態を確認し、未登録なら登録申請します。利用者が決める一時パスワードは次のMokManagerでの承認用で、Linuxのログインパスワードではありません。再起動後にMokManagerで承認し、その後Fedoraで登録済みか確認します。

moduleに署名しただけでは対象kernelが信頼する状態にはなりません。PCごとに公開証明書の登録が必要です。証明書を変更すると再登録が必要になる場合があります。共有鍵を使う場合はその鍵を信頼する全PCへ影響するため、管理者が更新・失効を管理します。

参照：https://docs.kernel.org/admin-guide/module-signing.html 、https://wiki.debian.org/SecureBoot

## 1.1.1向けの起動成果物の署名・照合

次の手順は、組み立て前の通常版・貸出版それぞれの `usb-tree` を管理者が検証するためのものです。EFI起動ファイル、GRUB設定、kernel、initramfs、MASTERのメタデータと `root.img` を署名対象にします。媒体ごとに起動メニューやMASTERのUUIDが異なるため、両版は個別に署名します。

```sh
python3 scripts/boot-integrity-manifest.py prepare \
  --tree build/workspace-1.1.1/appliance-standard/usb-tree \
  --version 1.1.1 --profile standard \
  --output build/workspace-1.1.1/boot-integrity-standard.json
```

管理者が、repoや媒体の外で管理する承認用鍵を使って署名します。以下のパスは例です。署名先には未使用のファイル名を指定してください。OpenSSLのパスフレーズ入力は管理者の端末で行います。

```sh
openssl cms -sign -binary -md sha256 -nocerts -outform DER \
  -signer /path/to/controlled-storage/release-signer.pem \
  -inkey /path/to/controlled-storage/release-signer.key.pem \
  -in build/workspace-1.1.1/boot-integrity-standard.json \
  -out build/workspace-1.1.1/boot-integrity-standard.sig
```

信頼する署名者の公開証明書は、検査対象ツリーとは別の経路で管理します。検査時には、承認する版と媒体種別も指定します。署名または成果物が一致しなければ停止します。[OpenSSL CMSの署名と検証](https://docs.openssl.org/3.5/man1/openssl-cms/)

```sh
python3 scripts/boot-integrity-manifest.py verify \
  --tree build/workspace-1.1.1/appliance-standard/usb-tree \
  --version 1.1.1 --profile standard \
  --manifest build/workspace-1.1.1/boot-integrity-standard.json \
  --signature build/workspace-1.1.1/boot-integrity-standard.sig \
  --trusted-signer /path/to/controlled-storage/release-signer.pem
```

この検査の対象は構築時の成果物です。起動時の認証、稼働中の書き換え防止、端末内の `state.img`・設定・TPMポリシーの保護は別工程です。TPMを使う無人運用を有効化する前に、これらを起動・更新経路に組み込んで確認します。

### 構築用の試験構成でMASTERの承認を必須にする

署名済みの本番用モジュール束を使って `scripts/build-root-appliance.py` で共通rootを構築する際に、`--boot-integrity-signer /path/to/controlled-storage/release-signer.pem` を追加します。この公開証明書がinitramfsに組み込まれます。承認に使う秘密鍵は引き続き管理者の保管先に置きます。

構築後に、上記の `prepare`、管理者によるCMS署名、`verify` を実行します。照合に成功したmanifestと署名だけを、組み立て前の `usb-tree/gpu-linux/` にそれぞれ `boot-integrity.json`、`boot-integrity.sig` として配置してから、`scripts/validate-appliance.py` で検証します。貸出版にrootを再利用しても、貸出版の起動メニューとMASTERメタデータに対して個別の承認が必要です。

この試験構成では、initramfsがMASTER上の署名と承認されたメタデータを確認し、rootとstateの初期イメージを照合してからNTFSの書き込み準備へ進みます。端末側で選択した `root.img` も、マウント前に署名済みメタデータのサイズとSHA-256で照合します。現在のrootは書き込み可能であり、通常の稼働による変更も次回の照合では拒否されるため、この構成は試験用です。継続運用には、起動中のinitramfs自体を先に認証する経路、rootの読み取り専用化と稼働中の整合性検査、設定保護、TPMの復号条件が必要です。

### dm-verityの構築時検証

`veritysetup` を使える構築環境では、次のコマンドでMASTERの `root.img` に対応する検証用ハッシュイメージとルートハッシュを生成できます。出力先には新しいディレクトリを指定します。

```sh
python3 scripts/boot-integrity-manifest.py prepare-verity \
  --tree build/workspace-1.1.1/appliance-standard/usb-tree \
  --output build/workspace-1.1.1/verity-standard
```

dm-verityで起動する試験媒体では、生成した `root.verity` と `root.verity.json` を公開署名者を組み込んだ試験用 `usb-tree/gpu-linux/` に置き、上記の `prepare`、署名、照合を実行します。manifest生成時にrootとハッシュイメージをユーザー空間で照合し、両ファイルを署名対象に含めます。

起動時にinitramfsは署名済みのルートハッシュとハッシュイメージを照合し、ハッシュイメージを一時領域に保持します。端末側の `root.img` を読み取り専用のdm-verityデバイス経由でマウントします。PAIRの一時ファイルはtmpfsに置き、ホームと推論キャッシュは従来のstateを使います。一連の起動・推論・停止は実機での検証が必要です。initramfs自体を先に認証する経路とTPMによる自動復号は引き続き開発中です。
