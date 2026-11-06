# 外部入力とライセンス

このソースツリーは第三者のISO・バイナリ・ソースチェックアウトを同梱しません。利用者が固定版を取得し、検証してローカルで構築する方式です。入力本体と生成したUSBをGitHubのGit/LFS/Releaseへ自動アップロードしません。

| 入力 | 取得元・確認事項 |
| --- | --- |
| gpu-unlock | https://github.com/Zagitta/gpu-unlock 。固定基準は `af28cbbfd3d922a659227ecfb7369adfd924cf03`。モジュールとfirmwareの条件は別。上流のlicensesとfirmware/NOTICE.mdを保存する |
| NVIDIA open modules | https://github.com/NVIDIA/open-gpu-kernel-modules 。現行ドライバ入力は615.71.10。ソースのCOPYINGと実行ライブラリ・firmwareの条件を分けて確認する |
| NVIDIA/CUDA | https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/ 。inputs/nvidia-driver-inputs.jsonと公式署名メタデータで検証する |
| firmware生成用入力 | inputs/firmware-inputs.jsonに固定。NVIDIAの610.43.03ソースと https://archive.cachyos.org/ の固定nvidia-utilsパッケージを取得し、gpu-unlockの固定generatorで生成する。生成物とNVIDIA-LICENSE・上流NOTICEを保持する |
| PAIR | https://github.com/NVIDIA/Personal-AI-Router 。inputs/pair-headless-input.jsonの固定releaseを取得。現在の上流はApache-2.0だが、取得版のLICENSE/NOTICEと同梱物を確認する |
| llama.cpp | https://github.com/ggml-org/llama.cpp 。inputs/pair-llamacpp-inputs.jsonのb11146/CUDA12.8。MIT本体と同梱CUDA等の条件を区別する |
| Fedora Workstation Live | https://fedoraproject.org/workstation/download/ 。現行抽出処理は44-1.7の固定ISO用。公式CHECKSUMの署名とISOのハッシュを検証する |
| Debian | https://deb.debian.org/ 。inputs/debian-build-inputs.json、debian-runtime-inputs.json等。公式パッケージの各ライセンスを保持する |
| pycdlib | https://pypi.org/project/pycdlib/1.14.0/ 。inputs/fedora-tools-inputs.jsonのwheelをSHA照合してFedora ISOの展開に使用。wheel本体は同梱しない |

ダウンロード用の公開鍵は公式配布元でfingerprintを照合して取得します。作者のMOK証明書や共通秘密鍵は提供しません。

公式公開鍵のURL・SHA・fingerprintは `inputs/public-keys.json`、APT配布元は `inputs/repositories.json` に記録します。署名済みshim/GRUBとgzip/公開CAは `inputs/debian-support-inputs.json` の固定パッケージを展開し、ホストへインストールしません。

生成USBには外部ソフトウェアが含まれます。完成USBを再配布する場合は、ソース公開とは別に条件を確認してください。特にgpu-unlockの生成firmwareは再配布承認済みと扱いません。

参照：

- https://github.com/Zagitta/gpu-unlock/blob/af28cbbfd3d922a659227ecfb7369adfd924cf03/firmware/NOTICE.md
- https://github.com/NVIDIA/Personal-AI-Router/blob/85f523d3624cf1596bdda6e36c28eb8ad3969737/LICENSE
- https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/COPYING
- https://docs.nvidia.com/cuda/eula/index.html
- https://fedoraproject.org/security/
