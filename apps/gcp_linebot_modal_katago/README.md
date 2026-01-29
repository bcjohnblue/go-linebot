# GCP + Modal KataGo 分離架構

## 架構說明

此架構將服務分為兩個部分：

1. **GCP Cloud Run 服務** (`gcp_linebot/`)

   - 處理 LINE Bot Webhook 請求
   - 提供圍棋對弈功能
   - 上傳 SGF 檔案至 GCS
   - 調用 Modal 函數進行 KataGo 分析
   - 生成 GIF 動畫和 LLM 評論
   - 回傳結果至 LINE

2. **Modal KataGo 服務** (`modal_katago/`)
   - 在 Modal 平台上運行的無伺服器函數
   - 從 GCS 下載 SGF 檔案
   - 執行 KataGo 全盤覆盤分析（使用 GPU）
   - 上傳分析結果至 GCS
   - 回調 Cloud Run 服務

### 架構流程

**對弈功能：**

```
1. 用戶在 LINE 中輸入座標（如 D4, Q16）
2. Cloud Run 服務接收請求並驗證落子合法性
3. 更新棋盤狀態並保存為 SGF 格式至 GCS
4. 生成當前棋盤圖片並回傳至 LINE
```

**形勢判斷功能：**
```
1. 用戶在進行中的對局裡輸入「形勢」或「形式」或「evaluation」
2. Cloud Run 取得當前對局的 SGF（GCS）並調用 Modal evaluation 函數
3. Modal 執行 KataGo evaluation 分析（ownership / scoreLead）
4. Cloud Run 繪製領地分布圖並上傳圖片至 GCS，回傳目數差距與圖片至 LINE
```

**AI 對弈功能：**

```
1. 用戶輸入「對弈 ai」開啟 AI 對弈模式（需先認證）
2. 用戶在 LINE 中輸入座標（如 D4, Q16）
3. Cloud Run 服務接收請求並驗證落子合法性
4. 更新棋盤狀態並保存為 SGF 格式至 GCS
5. 生成用戶的棋盤圖片（暫不回傳）
6. Cloud Run 調用 Modal 函數進行 AI 思考
7. Modal 函數從 GCS 下載 SGF 檔案
8. Modal 函數執行 KataGo GTP 獲取 AI 下一步棋（使用 GPU）
9. Modal 函數回調 Cloud Run
10. Cloud Run 更新 SGF 檔案並生成 AI 的棋盤圖片
11. 合併回傳：用戶的棋盤圖片 + AI 的棋盤圖片 + 文字訊息
```

**覆盤分析功能：**

```
1. 用戶透過 LINE 上傳 SGF 檔案或發送"覆盤"指令
2. Cloud Run 服務接收請求並上傳檔案至 GCS
3. Cloud Run 調用 Modal 函數進行分析
4. Modal 函數從 GCS 下載 SGF 檔案
5. Modal 函數執行 KataGo 分析（使用 GPU）
6. 分析結果上傳至 GCS
7. Modal 函數回調 Cloud Run
8. Cloud Run 進行 LLM 分析並生成 GIF
9. 覆盤結果回傳至 LINE
```

## 前置準備

### 1. GCP 設定

#### 1.1 安裝 Google Cloud SDK

#### 1.2 初始化 GCP

#### 1.3 建立 GCS Bucket

#### 1.4 建立 Artifact Registry

以上四點設定步驟，請參考 [GCP + 本地 KataGo 分離架構 - GCP 設定](/apps/gcp_linebot_localhost_katago/README.md#1-gcp-設定)

#### 1.5 確認 GCP 相關變數設定

**必要環境變數：**

1. **GCP_PROJECT_ID** - GCP 專案 ID

2. **GCS_BUCKET_NAME** - GCS Bucket 名稱

3. **CLOUD_RUN_CALLBACK_REVIEW_URL** - Cloud Run 服務的覆盤回調 URL

   ```bash
   # 部署後會自動取得，格式為：
   # https://SERVICE_NAME-PROJECT_NUMBER.REGION.run.app/callback/review
   export CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-service-url.run.app/callback/review
   ```

4. **CLOUD_RUN_CALLBACK_GET_AI_NEXT_MOVE_URL** - Cloud Run 服務的 AI 對弈回調 URL
   ```bash
   # 部署後會自動取得，格式為：
   # https://SERVICE_NAME-PROJECT_NUMBER.REGION.run.app/callback/get_ai_next_move
   export CLOUD_RUN_CALLBACK_GET_AI_NEXT_MOVE_URL=https://your-service-url.run.app/callback/get_ai_next_move
   ```

5. **MODAL_APP_NAME** - Modal 應用程式名稱（預設：`katago`）

6. **MODAL_FUNCTION_REVIEW** - Modal 覆盤函數名稱（預設：`review`）

7. **MODAL_FUNCTION_GET_AI_NEXT_MOVE** - Modal AI 對弈函數名稱（預設：`get_ai_next_move`）

8. **MODAL_FUNCTION_EVALUATION** - Modal 形勢判斷函數名稱（預設：`evaluation`）

9. **KATAGO_VISITS** - KataGo 分析深度（預設：`5`）

**設定 Secrets Manager（用於敏感資訊）：**

於 [Google Cloud Secret Manager](https://console.cloud.google.com/security/secret-manager) 頁面設定需保密的資料

1. **LINE_CHANNEL_ACCESS_TOKEN** - LINE Bot Channel Access Token

2. **OPENAI_API_KEY** - OpenAI API Key

3. **MODAL_TOKEN_ID** - Modal API Token ID

   前往 [Modal Settings](https://modal.com/settings) => API Tokens 頁面取得 Token

4. **MODAL_TOKEN_SECRET** - Modal API Token Secret

   取得 **MODAL_TOKEN_ID** 時會一起拿到 **MODAL_TOKEN_SECRET**

**可選環境變數：**

請根據 GCP 服務中申請的名稱修改 `gcp_linebot/scripts/deploy.sh` 檔案中的各項環境變數預設值

- `CLOUD_RUN_SERVICE_NAME` - Cloud Run 服務名稱（預設：`go-linebot-webhook`）
- `GCP_REGION` - GCP 區域（預設：`asia-east1`）
- `ARTIFACT_REGISTRY_REPO` - Artifact Registry repository 名稱（預設：`go-linebot-repo`）
- `IMAGE_TAG` - Docker 映像檔標籤（預設：`latest`）

### 2. Modal 設定

#### 2.1 安裝 Modal CLI

```bash
# 使用 pip 安裝
pip install modal
```

#### 2.2 登入 Modal

```bash
# 初始化 Modal
modal setup
```

按照指示完成認證。您需要：

1. 在 [Modal 網站](https://modal.com/) 註冊帳號
2. 完成認證流程

#### 2.3 建立 GCP Service Account 並設定 Modal Secret

**建立 GCP Service Account：**

前往 [Google Cloud IAM & Admin](https://console.cloud.google.com/iam-admin) 建立 Service Account，並授予 Storage 權限（`roles/storage.objectAdmin`），目的是為了讓 Modal 有權限讀取、修改 GCS bucket，然後下載 JSON 金鑰檔案

**建立 Modal Secret：**

前往 [Modal Secrets](https://modal.com/secrets) 頁面建立一個名為 `gcp-go-linebot` 的 Modal secret，包含以下項目：

1. **GCP_PROJECT_ID** - GCP 專案 ID

2. **GCS_BUCKET_NAME** - GCS Bucket 名稱

3. **GCP_SERVICE_ACCOUNT_KEY_JSON** - 將前面的 GCP Service Account JSON 金鑰檔案的完整內容貼上

4. **CLOUD_RUN_CALLBACK_REVIEW_URL** - GCP Cloud Run 服務的回調端點 URL
   ```bash
   # 格式：https://SERVICE_NAME-PROJECT_NUMBER.REGION.run.app/callback/review
   ```

#### 2.4 上傳 KataGo 模型至 Modal Volume

在部署 Modal 應用程式之前，需要先將 KataGo 模型上傳到 Modal Volume 儲存

**下載 KataGo 模型：**

請參考 [本地完整架構 - KataGo 設定](/apps/localhost_all/README.md#2-katago-設定) 的說明下載模型，並放置在 `apps/gcp_linebot_modal_katago/modal_katago/katago/models` 資料夾底下

**上傳模型至 Modal Volume：**

```bash
cd apps/gcp_linebot_modal_katago/modal_katago

# 上傳模型（這會建立並上傳到 Modal Volume）
modal run main.py::upload_model
```

**重要說明：**

- **只需要上傳一次**，之後所有函數調用都會使用 Volume 中的模型
- 如果模型檔案不存在，會顯示錯誤訊息，請確認檔案放置在 `apps/gcp_linebot_modal_katago/modal_katago/katago/models` 資料夾底下
- 上傳成功後，模型會儲存在名為 `katago-models` 的 Modal Volume 中 (Storage 頁面可看到)

**驗證上傳：**

```bash
modal volume list katago-models
```

### 3. Python 環境設定

請參考 [本地完整架構 - Python 環境設定](/apps/localhost_all/README.md#1-python-環境設定) 的說明

## 功能說明

### 形勢判斷功能

形勢判斷功能對當前盤面進行 KataGo 評估，顯示領地分布與目數差距。

**使用方式：**

1. 在進行中的對局裡輸入「形勢」或「形式」或「evaluation」
2. Bot 會回傳領地分布圖與目數文字（例如：目前形勢：黑 +3.5 目。）

**技術規格：**

- 分析引擎：KataGo evaluation（單一盤面）
- score_lead 為黑棋領先的目數

### AI 對弈功能

AI 對弈功能允許用戶與 KataGo AI 進行對戰。啟用後，用戶下完一手棋，AI 會自動思考並下下一手。

**使用方式：**

1. 使用「auth <token>」進行認證（AI 對弈功能需要認證）
2. 輸入「對弈 ai」或「vs ai」開啟 AI 對弈模式
3. 開始下棋（例如：D4）
4. AI 會自動回應並下下一步棋
5. 輸入「對弈 free」或「vs free」關閉 AI 對弈模式，恢復一般對弈模式
6. 輸入「對弈」或「vs」查看當前模式狀態

**技術規格：**

- AI 引擎：KataGo GTP
- 思考時間：約 10 秒內
- 配置檔案：`modal_katago/katago/configs/default_gtp.cfg`

**注意事項：**

- AI 對弈模式需要先進行認證（使用 `auth <token>` 指令）
- AI 對弈模式啟用後，用戶下完棋後不會立即收到回覆，需等待 AI 思考完成
- 系統會合併回傳用戶的棋盤圖片和 AI 的棋盤圖片，以及 AI 的落子位置

### 覆盤分析功能

覆盤分析功能使用 KataGo 對整局棋進行深度分析，找出關鍵手數並生成評論。

**使用方式：**

1. 上傳 SGF 棋譜檔案
2. 輸入「覆盤」開始分析
3. 等待約 10 分鐘獲得分析結果

**分析結果包含：**

- 🗺️ 全盤手順圖 - 顯示整局棋的所有手順
- 📈 勝率變化圖 - 顯示黑方勝率隨手數的變化曲線
- 🎬 關鍵手數 GIF 動畫 - 勝率差距最大的前 20 手動態演示
- 💬 ChatGPT 評論 - 針對關鍵手數的評論

**技術規格：**

- 分析引擎：KataGo AI
- 分析時間：KataGo 全盤分析約 6 分鐘
- 評論生成：ChatGPT 評論生成約 3 分鐘
- 動畫繪製：GIF 動畫繪製約 10 秒

## 執行方式

### 本地測試

#### 1. 啟動 Cloud Run 服務

```bash
cd apps/gcp_linebot_modal_katago/gcp_linebot
source venv/bin/activate
python main.py
```

#### 2. 啟動 Modal 服務（本地模式）

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
source venv/bin/activate
modal serve main.py
```

這會在本地啟動 Modal 服務，方便測試

### 部署

#### 1. 建置並部署至 Cloud Run

```bash
cd ./gcp_linebot

# 使用部署腳本，並依照提示修改相關環境變數
./scripts/deploy.sh
```

#### 2. 部署 Modal 應用程式

```bash
cd ./modal_katago

# 使用部署腳本，並依照提示修改相關環境變數
./scripts/deploy.sh
```

部署腳本會自動：

- 檢查 Modal CLI 是否安裝
- 檢查 Modal 認證狀態
- 檢查 Modal Secret 是否存在
- 檢查模型是否已上傳到 Volume（如果沒有，會提示上傳）
- 部署 Modal 應用程式

## 專案結構

```
gcp_linebot_modal_katago/
├── gcp_linebot/                    # Cloud Run 服務
│   ├── main.py                     # FastAPI 主程式
│   ├── config.py                   # 設定檔
│   ├── handlers/                   # 處理器
│   │   ├── line_handler.py         # LINE Bot 處理
│   │   ├── sgf_handler.py          # SGF 解析
│   │   ├── draw_handler.py         # GIF 生成
│   │   └── board_visualizer.py     # 棋盤視覺化
│   ├── services/
│   │   └── storage.py              # GCS 儲存服務
│   ├── LLM/
│   │   └── providers/              # LLM 提供者
│   ├── Dockerfile
│   └── requirements.txt
└── modal_katago/                   # Modal KataGo 服務
    ├── main.py                     # Modal 應用程式定義
    ├── handlers/
    │   └── katago_handler.py       # KataGo 處理
    ├── katago/                     # KataGo 相關檔案
    │   ├── models/                 # KataGo 模型（上傳至 Modal Volume）
│   ├── configs/                # 設定檔
│   ├── review.py               # 覆盤分析
│   └── evaluation.py           # 形勢判斷
    ├── scripts/
    │   └── deploy.sh              # 部署腳本
    └── requirements.txt
```
