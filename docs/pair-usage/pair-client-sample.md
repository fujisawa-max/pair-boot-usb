# VS Code／OpenCode共通の調査用サンプル

エディターのモデル接続と親子エージェントの設定を済ませた後、並行委任の確認に使います。調査するコードがまだない場合は、エディターで開いている開発対象のフォルダに `src` フォルダを作り、次の2ファイルを保存してください。既存の同名ファイルがあれば上書きせず、別の練習用フォルダを使います。

ファイルを保存すれば、実行環境の導入なしに読み取り調査を試せます。

## 作成するファイル

`src/config.ts`：

```typescript
export function readConfig(portText: string): { port: number } {
  return { port: Number(portText) };
}
```

`src/main.ts`：

```typescript
import { readConfig } from "./config";

function main(portText: string): void {
  const config = readConfig(portText);
  console.log(`Configured port: ${config.port}`);
}

main("8080");
```

入力検証を調べる練習として、`config.ts` には意図的に検証不足を残しています。

## 委任と回答の確認

ファイルを保存したら、利用するエディターの手順へ戻って親から2つの子を呼びます。依頼文は各手順に掲載しています。

- [VS Code：「4. 親から並行委任する」](vscode-pair-parallel-quick-start.md#4-親から並行委任する)
- [OpenCode：「3. 親を作り、バックグラウンドで呼ぶ」](opencode-pair-parallel-quick-start.md#3-親を作りバックグラウンドで呼ぶ)

| 担当 | 確認する回答 |
| --- | --- |
| Map（処理の流れ） | `main("8080")` → `readConfig`で数値化 → `console.log`で表示、という流れと `config.ts` への依存を説明できる。表示する文字列は `Configured port: 8080`。 |
| Review（入力検証） | `"abc"` が `NaN` になることや、`"-1"`・`"65536"` も受け入れることを指摘できる。整数かつ1〜65535というポート番号の検証が不足している。 |

回答に加え、子ごとの開始・完了と、該当ファイルを実際に読んだツール実行記録を確認します。親が両方の結果をまとめたかも確認してください。

このサンプルは短いため、すぐに処理が終わる場合があります。GPUでの並行処理を確認するときは、PAIR Jobsの実行先ノードと処理時間の重なりを照合し、必要なら長い独立したコード調査に置き換えます。
