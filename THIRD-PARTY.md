# 外部資料とライセンス

このソースツリーは第三者のISO・バイナリ・ソースチェックアウトを同梱しません。利用者が指定された版を取得し、検証してローカルで構築する方式です。取得した外部資料と生成したUSBをGitHubのGit/LFS/Releaseへ自動アップロードしません。

リポジトリ直下の [MIT License](LICENSE) は、このプロジェクトの独自ソース、設定、テスト、文書に適用します。取得する第三者ソフトウェアやその生成物のライセンスを変更するものではありません。取得した各ソフトウェアや資料の著作権表示、ライセンス、必要なNOTICEを保持します。

| 外部ソフトウェア・資料 | 取得元・確認事項 |
| --- | --- |
| gpu-unlock | https://github.com/Zagitta/gpu-unlock 。固定基準は `af28cbbfd3d922a659227ecfb7369adfd924cf03`。モジュールとfirmwareの条件は別。上流のlicensesとfirmware/NOTICE.mdを保存する |
| NVIDIA open modules | https://github.com/NVIDIA/open-gpu-kernel-modules 。使用するドライバの版は615.71.10。ソースのCOPYINGと実行ライブラリ・firmwareの条件を分けて確認する |
| NVIDIA/CUDA | https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/ 。inputs/nvidia-driver-inputs.jsonと公式署名メタデータで検証する |
| firmware生成用の外部資料 | inputs/firmware-inputs.jsonに版・URL・ハッシュを指定。NVIDIAの610.43.03ソースと https://archive.cachyos.org/ の指定版のnvidia-utilsパッケージを取得し、gpu-unlockの指定版のgeneratorで生成する。生成物とNVIDIA-LICENSE・上流NOTICEを保持する |
| PAIR | https://github.com/NVIDIA/Personal-AI-Router 。inputs/pair-headless-input.jsonの固定releaseを取得。現在の上流はApache-2.0だが、取得版のLICENSE/NOTICEと同梱物を確認する |
| llama.cpp | https://github.com/ggml-org/llama.cpp 。inputs/pair-llamacpp-inputs.jsonのb11146/CUDA12.8。MIT本体と同梱CUDA等の条件を区別する |
| Fedora Workstation Live | https://fedoraproject.org/workstation/download/ 。現行抽出処理は44-1.7の固定ISO用。公式CHECKSUMの署名とISOのハッシュを検証する |
| Debian | https://deb.debian.org/ 。inputs/debian-build-inputs.json、debian-runtime-inputs.json等。公式パッケージの各ライセンスを保持する |
| pycdlib | https://pypi.org/project/pycdlib/1.14.0/ 。inputs/fedora-tools-inputs.jsonのwheelをSHA照合してFedora ISOの展開に使用。wheel本体は同梱しない |
| PowerShell（任意の開発用試験） | https://github.com/PowerShell/PowerShell/releases/tag/v7.5.4 。inputs/powershell-test-input.jsonのLinux x64 archiveをサイズ・SHA照合し、合成fixtureの実行に使用。MIT本体と同梱物のLICENSE・ThirdPartyNoticesを展開先に保持し、USBへは含めない |

ダウンロード用の公開鍵は公式配布元でfingerprintを照合して取得します。作者のMOK証明書や共通秘密鍵は提供しません。

公式公開鍵のURL・SHA・fingerprintは `inputs/public-keys.json`、APT配布元は `inputs/repositories.json` に記録します。署名済みshim/GRUBとgzip/公開CAは `inputs/debian-support-inputs.json` の固定パッケージを展開し、ホストへインストールしません。

このリポジトリでは完成USBイメージを配布しません。利用者が構築したUSBには外部ソフトウェアが含まれます。利用者が別途USBイメージや媒体を再配布する場合は、ソース公開とは別に条件を確認してください。特にgpu-unlockの生成firmwareは再配布承認済みと扱いません。

参照：

- https://github.com/Zagitta/gpu-unlock/blob/af28cbbfd3d922a659227ecfb7369adfd924cf03/firmware/NOTICE.md
- https://github.com/NVIDIA/Personal-AI-Router/blob/85f523d3624cf1596bdda6e36c28eb8ad3969737/LICENSE
- https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/COPYING
- https://docs.nvidia.com/cuda/eula/index.html
- https://fedoraproject.org/security/
