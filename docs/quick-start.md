# USBの利用開始

検査済みイメージを利用者がUSBへ書き込んだ後の手順です。構築は [構築手順](build.md) に従います。生成USBからの実機起動確認の報告はありますが、登録・MOK・Normal/Unlock・PAIR・終了の動作は対象PCごとに確認してください。

1. WindowsでUSBの `start-windows-gui.cmd` を開き、環境確認を行う。
2. 保存先のドライブとフォルダを指定し、候補JSONをGPU-LOGSへ保存する。既定はD:のGPU-Linux。保存成功はLinuxからの書込み許可ではない。
3. USBのFedoraを起動し、管理GUIでWindows候補から保存先・GPUを登録する。表示されたPC、領域、GPU、フォルダを確認する。検証に失敗したら表示理由を確認し、安全性の値を手動で偽って進めない。
4. 必要に応じて [MOK登録](secure-boot.md) を行う。保存先登録とMOK申請は同じFedora起動中に行えるが、MokManagerの最終承認後に登録状態を確認する。
5. Normalを選んで起動する。rootイメージの初回コピー完了を待ち、メニューからPAIRを開始する。
6. モデルは登録したフォルダのModelsへ置く。[運用手順](user-guide.md)に従ってモデルと終了操作を確認する。

UnlockはNormalの成功後に、対応GPUの検証として別に試します。解除による速度向上はモデルや処理によって異なります。

終了時はPAIRをqで閉じ、メニューからPCの再起動または電源断を選びます。起動USBを抜くのはUSB CAN BE REMOVED NOWと表示された場合だけです。rootやModelsを置いた保存用USBは終了まで接続します。
