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

秘密鍵は所有者だけが読める通常ファイルとします。`scripts/sign-gpu-modules.py` はrepo外の秘密鍵とDER証明書を指定して、構築済みmoduleのコピーに署名します。暗号化鍵のパスフレーズは所有者が安全な入力方法で `KBUILD_SIGN_PIN` に渡します。コマンドラインやログへ直接書きません。署名前のモジュールの準備は [build.md](build.md) に従います。

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

公開証明書だけがUSBの `GPU-LOGS/signing-cert.der` に配置される設計です。秘密鍵はUSBへコピーしません。

Fedoraの管理GUIで証明書と登録状態を確認し、未登録なら登録申請します。利用者が決める一時パスワードは次のMokManagerでの承認用で、Linuxのログインパスワードではありません。再起動後にMokManagerで承認し、その後Fedoraで登録済みか確認します。

moduleに署名しただけでは対象kernelが信頼する状態にはなりません。PCごとに公開証明書の登録が必要です。証明書を変更すると再登録が必要になる場合があります。共有鍵を使う場合はその鍵を信頼する全PCへ影響するため、管理者が更新・失効を管理します。

参照：https://docs.kernel.org/admin-guide/module-signing.html 、https://wiki.debian.org/SecureBoot
