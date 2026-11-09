# Third-party notices — SceneLab v1.0

確認日: 2026-11-07

SceneLab本体のライセンスは[`LICENSE`](../LICENSE)（MIT）です。
外部由来の部分に対する元の権利表示を置き換えるものではありません。
本書および `LICENSES/` は再配布時にも含めてください。

## 同梱ワークフロー（4ファイル）

出所: [Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates)

Copyright (c) 2023-present Comfy Org

ライセンス: MIT。全文は `LICENSES/Comfy-Org-workflow_templates-MIT.txt`。

参照元のバージョン：`21d7cc4fb9afd983957f2d2d6b1d583860ccb40b`（2026-11-07確認）
UI形式をAPIグラフへ展開し、SceneLabで必要な部分を抽出・変更しています。

| 同梱ファイル | 参照元（固定リビジョン） | SceneLabでの変更概要 |
|---|---|---|
| `workflows/image_anima_base_v1.json` | [image_anima_base_v1.json](https://github.com/Comfy-Org/workflow_templates/blob/21d7cc4fb9afd983957f2d2d6b1d583860ccb40b/templates/image_anima_base_v1.json) | 公式ワークフローのサブグラフを展開し、生成に必要なノードを抽出してAPI形式に変換。 |
| `workflows/image_qwen_image_2_1_turbo_image_edit.json` | [image_qwen_image_2_1_turbo_image_edit.json](https://github.com/Comfy-Org/workflow_templates/blob/21d7cc4fb9afd983957f2d2d6b1d583860ccb40b/templates/image_qwen_image_2_1_turbo_image_edit.json) | 公式ワークフローのサブグラフを展開し、API形式に変換。 |
| `workflows/image_qwen_image_2_1_turbo_t2i.json` | [image_qwen_image_2_1_turbo_t2i.json](https://github.com/Comfy-Org/workflow_templates/blob/21d7cc4fb9afd983957f2d2d6b1d583860ccb40b/templates/image_qwen_image_2_1_turbo_t2i.json) | 公式ワークフローのサブグラフを展開し、API形式に変換。 |
| `workflows/image_z_image_turbo.json` | [image_z_image_turbo.json](https://github.com/Comfy-Org/workflow_templates/blob/21d7cc4fb9afd983957f2d2d6b1d583860ccb40b/templates/image_z_image_turbo.json) | 公式ワークフローのサブグラフを展開し、API形式に変換。 |

ワークフロー内のサンプル画像名は文字列であり、該当画像ファイルは同梱していません。
プレビュー画像・モデル重み・フォント・外部JavaScriptライブラリはこの配布物に含めていません。

## 別途利用する外部ソフト（非同梱）

- [ComfyUI](https://github.com/Comfy-Org/ComfyUI): ライセンスはGPL-3.0。
  SceneLabは別プロセスのComfyUIへHTTP要求を送ります。
  ComfyUIのPython実装・フロントエンド・実行ファイルは同梱していません。
- [Strata](https://github.com/Niko1221/Strata): LLMの接続先として選択する場合に利用します。ライセンスはMIT。
  HTTP APIで接続し、サーバー・推論エンジン・モデルは同梱していません。
- Ollama、LM Studio、クラウドAPI: LLMの接続先として選択する場合に、別途導入・契約して利用します。
  利用する接続先の利用条件・モデル条件・料金が適用されます。
- Pythonランタイムも同梱していません。

## モデルのライセンス（非同梱）

モデル定義JSONにファイル名を書いていることは、モデルのライセンス付与を意味しません。
SceneLab本体のMITは、モデルや生成物についての権利を付与・保証しません。
以下は確認日現在の情報です。実際に取得する重み・派生版・エンコーダー・VAEそれぞれの条件を確認してください。

| 系列 | 確認内容・参照先 |
|---|---|
| Qwen-Image-2.1 / Turbo | 公式LICENSEはQwen RESEARCH LICENSE AGREEMENT。研究・評価向けの非商用に限定し、商用は別許諾を求めています。[通常版](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) / [Turbo](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE)。[Comfy-Org再梱包版](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)も元ライセンスを参照しています。 |
| Anima Aesthetic / Turbo | CircleStone Labs Non-Commercial License v1.2。モデル本体と出力画像を区別し、モデルカードでは画像販売等を許容する例と有料API等の制限例を挙げています。[ライセンス](https://huggingface.co/circlestone-labs/Anima/blob/main/LICENSE.md) / [モデルカード](https://huggingface.co/circlestone-labs/Anima)。NVIDIA由来の適用条件も参照してください。 |
| Z-Image-Turbo | 公式モデルカードの指定はApache-2.0。[配布元](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)。併用するテキストエンコーダー・VAE等は別途確認してください。 |
