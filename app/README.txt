SceneLab
=========================
日本語で相談しながら画像生成・編集。LLMとComfyUIをつなぐローカルアプリ。

SceneLab自体は自分のPCで動き、ブラウザーで操作します。

フォルダー構成:
SceneLab/
  README.md      概要・はじめ方
  LICENSE        本体のMITライセンス
  Start-SceneLab.bat  起動用（ダブルクリック）
  app/           本体・詳細説明書・外部ライセンス（更新時に置き換え）
  data/          設定・画像・キャラクター・探索・ひらめき（保持）

README.txtなどの説明書はappフォルダー内にあります。
通常は起動ファイルだけを使い、設定と作品の操作はブラウザーで行います。
appフォルダーは削除・分割せず、Start-SceneLab.batとセットで保持してください。
画像生成は別途起動したComfyUIが担当し、プロンプト作成は接続先のLLMが担当します。
LLMはローカルサーバーとクラウドAPIのどちらも利用できます。

1. 用意するもの
---------------
・WindowsとPython。開発時の確認環境はPython 3.12です。
  Start-SceneLab.batが使う「python」コマンドを実行できるようにしてください。
・ブラウザー。
・使う画像モデルと必要なノードが導入されたComfyUI。
・ローカルLLM（Ollama、LM Studio、Strataなど）またはOpenAI互換API対応のクラウドサービス。

SceneLab本体はPython標準ライブラリのみを使用し、追加のpipインストールは不要です。
ComfyUI・LLMサーバー・モデルの準備はそれぞれ別途必要です。
モデルは同梱していません。GPU/VRAM/RAMの必要量は利用モデル・画像サイズ等に依存します。
APIキーの保存にはWindowsのDPAPIを使用します。

2. 最初の起動
-------------
(1) ComfyUIを起動します。
(2) ローカルLLMを使う場合は、そのサーバーとモデルを起動します。
    クラウドLLMの場合は、API URL・モデルID・必要なAPIキーを用意します。
(3) SceneLab直下のStart-SceneLab.batを実行します。
(4) 自動で開くブラウザーから「設定」を開きます。
    開かない場合は起動時に表示されるコンソールウィンドウのURLを開いてください。
    通常は http://127.0.0.1:8765/ です。使用中なら8774までの別ポートを使います。
(5) LLMとComfyUIの接続先を設定し、使用する画像モデルを確認します。
(6) 最初は画像1枚・短いテーマで動作を確認してください。

終了時はSceneLabのコンソールウィンドウを閉じてください。
SceneLabは127.0.0.1で待ち受けます。インターネットへの公開用サーバーではありません。

3. LLMの接続設定
----------------
「設定 → LLMの接続先」でAPI URLとモデルIDを指定します。
URLは通常 /v1 までを指定し、/chat/completions は付けません。
初期URLは http://127.0.0.1:11434/v1、モデルIDは空欄です。

接続例（実際のポート・URLは各サーバー側の表示を優先）:
・Ollama:    http://127.0.0.1:11434/v1
・LM Studio: http://127.0.0.1:1234/v1
・Strata:    http://127.0.0.1:8080/v1
・クラウド: サービスが案内するOpenAI互換APIのベースURL

「LLMのモデル一覧を取得」から選択するか、モデルIDを手入力します。
一覧を取得できても、すべてのモデルがSceneLabの要求するJSONを返せる保証はありません。
設定は自動保存されます。APIキーはHTTPS接続先ごとにWindowsで暗号化して保存します。
別PC・別Windowsユーザーへキー設定をコピーする運用には対応しません。
クラウド利用時は会話・テーマ・材料・キャラクターのメモ等のLLM入力が接続先へ送信されます。

4. ComfyUIの接続とモデル配置
---------------------------
「設定 → ComfyUIの接続先」の初期URLは http://127.0.0.1:8188 です。
「ComfyUIに接続・一覧を更新」でノードとモデルの一覧を取得します。
設定画面で使いたい画像モデルのファイルを指定し、対応確認の結果を確認してください。
全部のモデルを導入する必要はありません。使用するモデル分を用意します。

標準の配置先:
・diffusion:    ComfyUI/models/diffusion_models/
・text_encoder: ComfyUI/models/text_encoders/
・vae:          ComfyUI/models/vae/
別のモデル検索パスを設定している場合は、ComfyUIで認識される場所を使用してください。
以下はSceneLab v1.0の標準定義のファイル名です。
同じ系列の別ファイルは設定画面から選択できますが、任意のモデルが互換とは限りません。

Anima Aesthetic v1.1
  diffusion: anima-aesthetic-v1.1.safetensors
  text_encoder: qwen_3_06b_base.safetensors
  vae: qwen_image_vae.safetensors
  対応: 新規生成・全体編集・範囲編集
  必要ノード: CLIPLoader, CLIPTextEncode, EmptyLatentImage, KSampler, SaveImage, UNETLoader, VAEDecode, VAELoader, LoadImage, VAEEncode, ImageToMask, VAEEncodeForInpaint

Anima Turbo v1.1
  diffusion: anima-turbo-v1.1.safetensors
  text_encoder: qwen_3_06b_base.safetensors
  vae: qwen_image_vae.safetensors
  対応: 新規生成・全体編集・範囲編集
  必要ノード: CLIPLoader, CLIPTextEncode, EmptyLatentImage, KSampler, SaveImage, UNETLoader, VAEDecode, VAELoader, LoadImage, VAEEncode, ImageToMask, VAEEncodeForInpaint

Qwen-Image-2.1
  diffusion: qwen_image_2.1_int8_convrot.safetensors
  text_encoder: qwen3vl_8b_int8_convrot.safetensors
  vae: qwen_image_2.1_vae_bf16.safetensors
  対応: 新規生成・全体編集・範囲編集
  必要ノード: UNETLoader, CLIPLoader, VAELoader, KSampler, VAEDecode, SaveImage, LoadImage, VAEEncode, QwenImage21Cache, TextEncodeQwenImage21, EmptyLatentImage

Qwen-Image-2.1-Turbo
  diffusion: qwen_image_2.1_turbo_int8_convrot.safetensors
  text_encoder: qwen3vl_8b_int8_convrot.safetensors
  vae: qwen_image_2.1_vae_bf16.safetensors
  対応: 新規生成・全体編集・範囲編集
  必要ノード: UNETLoader, CLIPLoader, VAELoader, KSampler, VAEDecode, SaveImage, LoadImage, VAEEncode, QwenImage21Cache, TextEncodeQwenImage21, EmptyLatentImage, ManualSigmas, KSamplerSelect, RandomNoise, SamplerCustomAdvanced, CFGGuider, SaveImageAdvanced

Z-Image Turbo
  diffusion: z_image_turbo_bf16.safetensors
  text_encoder: qwen_3_4b.safetensors
  vae: ae.safetensors
  対応: 新規生成・全体編集・範囲編集
  必要ノード: UNETLoader, CLIPLoader, VAELoader, KSampler, VAEDecode, SaveImage, LoadImage, VAEEncode, CLIPTextEncode, ConditioningZeroOut, EmptySD3LatentImage, ModelSamplingAuraFlow, ImageToMask, VAEEncodeForInpaint

Qwen系の範囲編集は、輪郭線を付けた元画像と指示で編集します。
範囲外をピクセル単位で固定する方式ではなく、編集強度スライダーはありません。
Z-Image / Anima系は全体編集・マスク編集に対応しています。全体編集では編集強度を指定できます。
両系列とも新規生成用JSONを1ファイル読み込み、編集時に画像・マスク用のノードを追加します。
参照画像によるキャラクター指定は、新規生成のQwen系でのみ使用できます。

ノードについて：
使用するモデルに対応したComfyUIが必要です。
ノードやモデルが見つからない場合は、ComfyUIの更新・導入状況を確認し、再起動後にSceneLabで一覧を更新してください。

ユーザー登録ワークフロー:
設定画面からComfyUIのAPI形式JSONを登録できます。
プロンプト等の入力先と出力ノードを指定します。登録ワークフローは新規生成用です。
必要なカスタムノードとモデルは、そのワークフローに合わせて別途用意してください。

5. 主な使い方
-------------
・チャット・画像生成：日本語でプロンプトを作成して画像を生成できます。生成枚数を複数枚にすると、同じ最終プロンプトでシードだけを変えて生成します。最終プロンプト（画像生成モデルに直接渡す指示文）を入力して、そのまま画像を生成することもできます。生成した画像は画面右側のギャラリーに保存・表示され、画像を選んで編集したり、プロンプトを確認したりできます。
・画像編集：画像全体、または範囲を指定して編集できます。
・キャラクター：名前・外見メモ・参照画像を登録できます。参照画像による新規生成はQwen系に対応しています。
・探索：テーマに沿って複数のプロンプトから様々な画像を生成します。つけたハート評価やコメントは、次のラウンドの生成に反映されます。
・ひらめき：テーマから発想の材料を用意し、組み合わせ・連想・自由な発想で様々な画像を作ります。

探索・ひらめきは、JSON不正・件数不一致・必須要素の欠落・重複等の出力不備を、
初回を含む最大10回まで再試行します。画面には「出力を再試行中 2/10」のように現在の試行回数を表示します。
詳しい失敗理由と回数はコンソールウィンドウに出ます。

LLM通信のタイムアウトは1リクエスト300秒です。
LLM待ちの中止はアプリ側の待機を止める方式で、接続先の計算が続く場合があります。
画像生成開始後の中止は、現在の1枚が完了してから反映します。
探索では過去の生成済み候補を全件送るため、ラウンドを重ねると必要コンテキストが増えます。
ひらめきは直近40案のタイトルと材料を参照し、過去プロンプト全文は送りません。

6. VRAMの使い分け（任意設定）
----------------------------
設定画面で、LLMと画像生成のVRAMの使い分けを設定できます。
まず「画像生成前のLLMのVRAM解放」は「しない」で試せます。
同じGPUでLLMと画像生成を使う場合、必要に応じてOllama / LM Studio /
Strata（完全アンロード）/ Strata（VRAM Elastic）を選びます。
この操作は127.0.0.1・localhostなど、このPC上のLLM接続先に限ります。

Ollama: keep_alive: 0による解放を使用します。
LM Studio: Just in Time Model Loadingをオンにすると、次のLLM要求時に再ロードできます。
Strata（完全アンロード）: 対応APIと自動ロードを確認してから解放します。
  VRAMを大きく空けられる一方、次の要求でモデル全体のロード時間がかかります。

Strata（VRAM Elastic）:
・対応するStrataの起動設定JSONに "vram_elastic": true を追加し、Strataを再起動してください。
・指定値はGPU全体の目標空き容量です（初期値16000 MiB）。モデルの一部はVRAMに残ります。
・画像生成前にキャッシュを調整し、次のLLM要求前に復元を試みます。
  目標量まで空かない・全量を復元できない場合も処理を続けます。

「画像生成後にComfyUIのモデルをアンロード」は別の設定で、初期状態はオフです。
オンの場合は連続生成終了後に画像モデルを解放し、次のLLMが使えるVRAMを増やします。
次回の画像生成では画像モデルの再ロード時間がかかる場合があります。

7. 保存・更新・バックアップ
------------------
設定と作品はSceneLab/dataフォルダー内に保存されます。
以下のパスはdataフォルダーからの相対パスです。
・llm_connection.local.json: LLM接続・暗号化したAPIキー
・comfy_connection.local.json: ComfyUI接続
・model_settings.local.json: モデル・モード別のパラメータ
・custom_models.local.json: 追加モデル・登録ワークフロー
・gallery/: 画像と付随データ
・characters/: キャラクターと参照画像
・exploration_data/ / inspiration_data/: テーマ・候補・評価等

更新: これまでのdata/フォルダーを新しい版のフォルダーへコピーすると、設定や作品を引き継げます。
バックアップ: data/を丸ごと別の場所へコピーしてください。
別PC・別WindowsユーザーではAPIキーを再入力してください。
data/がない場合は起動時に空のフォルダーを作成します。

8. 困ったとき
-------------
・Pythonが見つからない: ターミナルでpython --versionを確認してください。
・LLMに接続できない: API URL・ポート・モデルID・APIキー・サーバーの起動状態を確認。
・ノードやモデルが見つからない: ComfyUI側の導入と配置を確認し、再起動して一覧を更新。
・JSONのエラー: LLMの出力が要求形式に合っていない場合があります。
  自動再試行のログを確認し、少ない案数や別のLLMでも試してください。
・クラウドの429/503: 表示された残高・利用上限・混雑等の理由を確認してください。
・更新後に画面の動作がおかしい: 古いSceneLabを終了して起動し直し、画面を再読み込み。
・コンソールウィンドウの[timing]: LLM・画像生成・VRAM調整等の処理時間です。
  [strata VRAM]のafterは調整後の報告値、beforeは前回の参考値で古い場合があります。

参考（外部サービスの仕様は変更される場合があります）
----------------------------------------------------
ComfyUI公式: https://github.com/Comfy-Org/ComfyUI
Qwenノード実装: https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_qwen.py
Strata公式: https://github.com/Niko1221/Strata
Strata VRAM Elastic: https://github.com/Niko1221/Strata/blob/main/docs/VRAM_ELASTIC.md
Anima（モデル定義の参照元）: https://huggingface.co/circlestone-labs/Anima

9. 公開範囲とライセンス
----------------------
SceneLab v1.0はWindows向け・同じPCのブラウザーから操作するローカルアプリです。
複数ユーザー運用やインターネット向けサーバー運用、他OSの動作保証は対象外です。

SceneLab本体: MIT（SceneLab直下のLICENSE）。Copyright (c) 2026 sora242。
公式テンプレート由来のワークフロー: Comfy OrgのMIT表示を維持しています。
再配布時はSceneLab直下のLICENSE、app/THIRD_PARTY_NOTICES.md、app/LICENSES/を含めてください。
モデル・外部ソフトは非同梱で、SceneLabのMITとは別条件です。
特にQwen-Image 2.1/TurboとAnimaは独自の利用制限があります。
モデルと生成画像の条件を混同せず、配布元の最新ライセンスを確認してください。
外部由来部分の出所とライセンスの詳細はTHIRD_PARTY_NOTICES.mdを参照してください。
