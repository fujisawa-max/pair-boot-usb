# 学生向け推論API（1.1.0試験版）

学生PCのVS CodeやOpenCodeから、学内DNS名を使ってOpenAI互換APIへHTTPSで接続します。PAIRに参加するのはGPUノードです。同じ時点では候補の1台が仮想IPv4アドレス（VIP）を担当し、そのノードのCaddyからローカルのPAIRへ中継します。モデルに応じたGPUノードの選択はPAIRが行います。通常版と貸出版は同じrootを使います。

## 1. 管理者がネットワークを準備する

- DHCP配布範囲の外でVIPを確保し、学内DNSの共通名のAレコードをVIPへ向けます。候補の有線インターフェースが同じIPv4サブネットとプレフィックス長を使うようにします。
- 候補間でVRRPv3のIPv4マルチキャスト（`224.0.0.18`、IPプロトコル112）、VIPの移動、ARP通知が通ることを確認します。同じVLANで使用中のVRIDを避けます。学生側からはVIP宛てTCP 443を許可し、候補のVRRP用VLANへのアクセスを管理します。候補側でTCP 443を入口専用とします。
- VRRPの通知が分断されると複数候補がVIPを持つ場合があります。マルチキャストやVIPの移動を保証できないVLANでは設定を有効化せず、学内管理者と既存のロードバランサーの利用を検討します。
- 学生の端末が信頼するCAから共通DNS名をSANに含む証明書を発行し、秘密鍵は候補ごとに用意します。起動署名用のMOKとは別に管理します。保守コンソールへの物理アクセスを管理できるPCに限り、入口を有効化します。

## 2. 管理者が候補ノードを設定する

Windowsから、登録済み保存先フォルダの直下（既定では `GPU-Linux`）にUTF-8の `pair-gateway.json` を配置します。次は架空の値です。各候補で同じ設定を使い、変更時は `generation` を更新して配布の照合に使います。

```json
{
  "schema": 1,
  "generation": 1,
  "enabled": true,
  "virtual_ipv4": "192.0.2.240/24",
  "dns_name": "pair-lab.example",
  "virtual_router_id": 42,
  "client_ipv4_networks": ["198.51.100.0/24"],
  "readiness_model_ids": ["PAIR-Code", "PAIR-Vision"]
}
```

- `client_ipv4_networks` は候補から見える学生側のIPv4送信元をCIDRで列挙します。
- `readiness_model_ids` は候補自身のPAIRの `/v1/models` に表示される授業用モデルIDです。うち1つ以上を確認できると候補に入ります。学生に返される一覧はPAIRが提供する全モデルです。授業から利用できるモデルだけをクラスタに登録してください。
- 有線インターフェースが複数条件を満たす場合は、同じフォルダに `pair-gateway-node.json` を配置して `{"interface":"enp1s0"}` のように指定します。候補は物理デバイスを持つ有線インターフェースに限定されます。
- 入口を有効にするノードだけに設定と資格情報を配置します。設定ファイルがない場合、無効化した場合、または検証に失敗した場合も、従来のPAIR起動を利用できます。

GPUノードをNormalまたはUnlockで起動した後、管理者が保守コンソールから `/run/pair-gateway-credentials/` に以下の4ファイルを配置します。ディレクトリはroot所有でモード0700、各ファイルはroot所有の通常ファイルで0600とします。管理下の保管先からRAM上へ投入します。

| ファイル | 内容 |
| --- | --- |
| `fullchain.pem` | サーバー証明書と必要な中間証明書。サーバー証明書が先頭 |
| `key.pem` | ノードのサーバー秘密鍵。自動起動用のパスフレーズなしPEM |
| `ca.pem` | サーバー証明書を検証するための信頼するCAのPEM |
| `token.json` | `sha256` と `expires_at` の2項目だけを持つJSON |

授業用トークンには暗号学的乱数32バイトをパディングなしBase64urlで符号化した43文字を使います。 `sha256` はその43文字のASCIIバイト列を改行なしでSHA-256にかけた、小文字の16進64文字です。 `expires_at` はUTC Unix epoch秒の整数です。候補ノードの時計を合わせ、同じハッシュと期限を配ります。トークン原文は別経路で学生へ渡し、公開文書、USB、登録済みNTFS保存先、モデルフォルダ、台帳、ログ、更新束へ記録しません。

### 試験用の証明書とトークンを生成する

管理者のLinux環境で、Bash、OpenSSL、Python 3を用意し、リポジトリのルートから実行します。出力先にはアクセスを制限した既存の親ディレクトリの下に新しいディレクトリを指定します。以下は架空の設定例です。

```sh
scripts/make-pair-test-credentials.sh --tokens 30 "$HOME/pair-test-credentials" pair-lab.example gpu-a gpu-b
```

出力先の `nodes/<ノード名>/` に各ノードへ配置する4ファイルが生成されます。公開CA証明書は出力先直下の `ca.pem`、学生ごとのトークン原文は `students/student-001.txt` 以降です。学生には各自のファイルの値と公開CA証明書を配ります。暗号化されたCA署名鍵 `ca-signing.key.pem` は管理者の手元に置き、OpenSSLのプロンプトでパスフレーズを入力します。ノード証明書の有効期間は30日、トークンは7日です。

1.1.0用には `--tokens 30` を省略します。その場合は従来の単一トークン形式が生成され、原文は出力先直下の `student-token.txt` に保存されます。複数トークン形式は1.1.1用です。

### 1.1.1の複数トークン設定

1.1.1では従来の1件形式に加えて、次のように `tokens` 配列に学生ごとのハッシュと期限を登録できます。以下の値は書式例です。

```json
{
  "tokens": [
    {"sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "expires_at": 1893456000},
    {"sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "expires_at": 1893542400}
  ]
}
```

各学生には自分のトークン原文を渡し、すべての入口候補には同じ `token.json` を配置します。個別に失効させるときは該当する項目を配列から取り除き、すべての入口候補の設定を差し替えて入口サービスに反映します。有効期限はトークンごとに判定します。すべて期限切れになると入口候補から外れます。1.1.0のノードを含む構成では、全候補の更新が完了してから配列形式に切り替えます。

## 3. PAIRを開始し、学生PCを接続する

1. 管理者は起動メニューからPAIRを開始し、GPUノード同士をペアリングします。入口サービスはCaddy経由の未認証拒否と、ローカルのPAIR APIに授業用モデルが見えることを確認します。連続2回の成功でVRRPの選出に参加し、連続2回の失敗で離脱します。復旧した候補は稼働中の担当からVIPを奪いません。
2. 入口だけが起動しない場合は、管理者が `systemctl status gpu-pair-gateway.service` と `journalctl -u gpu-pair-gateway.service` で状態を確認します。設定値、証明書、時計、有線接続、PAIRのモデル一覧を確認します。PAIR稼働中に設定を適用し直すには `systemctl restart gpu-pair-gateway.service` を実行します。資格情報の更新時は待機側と学生端末を同じ世代へ揃えてから担当を交代します。
3. 学生はOpenAI互換プロバイダーを選び、Base URLに `https://pair-lab.example/v1` に相当する授業用URL、APIキーに配布されたトークン、モデルに授業で指定されたIDを設定します。学内CAが必要な場合は管理者の手順で端末の信頼ストアに登録し、証明書検証を有効のまま使います。拡張機能やクライアントの設定欄は [開発環境との連携](pair-client-setup.md) を参照してください。

公開するのは `GET /v1/models` と `POST /v1/chat/completions` です。接続中の推論ストリームが切れたら、切替後に同じURLで再送します。

## 4. 停止と試験

入口だけを停止する場合は、管理者が `systemctl stop gpu-pair-gateway.service` を実行します。PAIRの終了時にも入口が停止し、VIPを解放して生成済み設定と資格情報の一時コピーを破棄します。投入元の `/run/pair-gateway-credentials/` は管理者が停止後に回収し、再起動でも消去されます。電源操作とUSBの取り外しは従来のメニューと画面案内に従います。

導入時は、通常版と貸出版の起動・root更新、GPUノードの参加と再参加、学生側からの一覧・テキスト・画像付き推論とストリーミングを確認します。許可外送信元、期限切れトークン、異なるホスト・経路が拒否されることも確認します。担当の正常停止・電源断・リンク断・PAIR停止ごとに切替時間を記録し、復旧後も同じURLで使えることを確認します。学生20人相当ではテキストと画像を別々に計測し、入口のCPU・通信量・同時接続数、最初のトークンまでの時間、失敗率を記録します。

参照 [PAIRのルーティング](https://github.com/NVIDIA/Personal-AI-Router/blob/v1.0.0/docs/architecture.mdx#inference-data-flow) · [Caddyの認証連携](https://caddyserver.com/docs/caddyfile/directives/forward_auth) · [Keepalivedの設定](https://www.keepalived.org/documentation/keepalived-conf/) · [VRRP仕様](https://www.rfc-editor.org/rfc/rfc9568.html)

## 1.1.1向けの証明書発行と照合

管理者が管理するLinux環境で、端末用の鍵とCSRを生成できます。新規構築した1.1.1通常版USBでは、Fedora管理環境を起動し、以下のコマンドのスクリプトパスを `/run/initramfs/live/tools/issue-pair-gateway-certificate.py` に置き換えて実行できます。出力先は管理者だけがアクセスできる別の保護領域を指定します。Fedoraの一時領域は再起動で消えるため、再起動をまたぐ作業には管理者が用意した永続領域を使います。

```sh
python3 scripts/issue-pair-gateway-certificate.py request \
  --dns-name pair-lab.example \
  --output /path/to/controlled-storage/node01
```

Fedoraの管理GUIでは「学生向けAPIの端末登録」から「端末鍵と公開CSRを生成」を選びます。管理GUIの実行ユーザーだけがアクセスできる保存先を選ぶと、その下の新しい `pair-gateway-registration` フォルダに出力します。署名担当者へ渡すのは `node.csr.pem` だけです。`key.pem` は生成元の保護領域で管理します。

管理者が演習用の自己署名CAを管理し、各GPU端末から受け取ったCSRに署名します。署名する前に、スタッフが登録した端末とCSRの対応を確認してください。このコマンドはスタッフの管理PCで実行します。CA秘密鍵は同PCのリポジトリ外で管理し、GPU端末、通常版・貸出版USB、rootやstateへコピーしません。証明書のCNと唯一のDNS SANには、管理者が指定した共通DNS名を設定します。

```sh
python3 scripts/issue-pair-gateway-certificate.py issue \
  --dns-name pair-lab.example \
  --csr /path/to/verified-requests/node01.csr.pem \
  --ca-cert /path/to/controlled-ca/ca.pem \
  --ca-key /path/to/controlled-ca/ca-signing.key.pem \
  --output /path/to/issued/node01 \
  --days 30
```

出力先には新規ディレクトリを指定します。生成されるのは `fullchain.pem` と `ca.pem` です。ここでは演習用CAが直接署名するため `fullchain.pem` にはサーバー証明書1枚が入ります。学生が信頼設定に使うのは選定済みの公開CA証明書です。

返却されたサーバー証明書は、同じメニューの「返却されたサーバー証明書を照合」から確認できます。生成済みの `pair-gateway-registration` フォルダ、事前に信頼したCA証明書、返却されたサーバー証明書の順に選びます。CLIでも照合できます。秘密鍵を保持しない署名担当者は `--node-key` を省略して公開情報だけを照合できます。

```sh
python3 scripts/issue-pair-gateway-certificate.py verify \
  --dns-name pair-lab.example \
  --csr /path/to/controlled-storage/node01/node.csr.pem \
  --ca-cert /path/to/controlled-ca/ca.pem \
  --certificate /path/to/issued/node01/fullchain.pem \
  --node-key /path/to/controlled-storage/node01/key.pem
```

現行1.1.0の試験でこのツールを使う場合、CSRに対応する端末鍵と共通の `token.json` を用意し、上記の1.1.0手順に従って配置します。
