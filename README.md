# Go LINE Bot - KataGo Analysis with GCP

這是一個 LINE Bot，用於接收 SGF 棋譜檔案並執行 KataGo 分析，提供 AI 覆盤與評論功能。

## 功能特色

- 📱 透過 LINE Bot 接收 SGF 棋譜檔案
- 🤖 使用 KataGo 進行全盤覆盤分析
- 🎨 自動生成關鍵手數的 GIF 動畫
- 💬 整合 ChatGPT 生成中文評論
- ☁️ 支援 GCP Cloud Run 部署與本地服務分離架構
- 📊 分析結果以 Flex Message 形式回傳至 LINE

## 架構流程

### 分離架構（推薦用於生產環境）

```
1. 用戶透過 LINE 上傳 SGF 檔案或發送"覆盤"指令
2. Cloud Run 服務接收請求並上傳檔案至 GCS
3. Cloud Run 發起請求至本地 KataGo 服務
4. 本地服務執行 KataGo 分析（15-20 分鐘）
5. 分析結果上傳至 GCS
6. 本地服務回調 Cloud Run
7. Cloud Run 進行 LLM 分析並生成 GIF
8. 結果以 Flex Message 回傳至 LINE
```

### 本地完整架構（開發/測試用）

```
1. 用戶透過 LINE 上傳 SGF 檔案
2. 本地服務接收並解析 SGF
3. 執行 KataGo 分析
4. 生成 GIF 動畫
5. 調用 LLM 生成評論
6. 結果回傳至 LINE
```

## 專案結構

```
go-linebot/
├── apps/
│   ├── gcp_linebot_localhost_katago/    # 分離架構
│   │   ├── gcp_linebot/                 # Cloud Run 服務
│   │   │   ├── main.py                  # FastAPI 主程式
│   │   │   ├── config.py                # 設定檔
│   │   │   ├── handlers/                # 處理器
│   │   │   │   ├── line_handler.py      # LINE Bot 處理
│   │   │   │   ├── sgf_handler.py       # SGF 解析
│   │   │   │   ├── draw_handler.py      # GIF 生成
│   │   │   │   └── board_visualizer.py # 棋盤視覺化
│   │   │   ├── services/
│   │   │   │   └── storage.py           # GCS 儲存服務
│   │   │   ├── LLM/
│   │   │   │   └── providers/           # LLM 提供者
│   │   │   ├── Dockerfile
│   │   │   └── requirements.txt
│   │   └── localhost_katago/            # 本地 KataGo 服務
│   │       ├── main.py                  # KataGo 分析服務
│   │       ├── handlers/
│   │       │   └── katago_handler.py    # KataGo 處理
│   │       └── katago/                  # KataGo 相關檔案
│   │           ├── models/              # KataGo 模型
│   │           ├── configs/             # 設定檔
│   │           └── analysis.py
│   └── localhost_all/                   # 本地完整版本
│       ├── src/
│       │   ├── main.py
│       │   ├── handlers/
│       │   └── ...
│       ├── katago/
│       └── requirements.txt
└── README.md
```

## 快速開始

### 1. 安裝依賴

```bash
# 進入對應的應用目錄
cd apps/localhost_all  # 或 apps/gcp_linebot_localhost_katago/gcp_linebot

# 建立虛擬環境（建議）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安裝依賴
pip install -r requirements.txt
```

### 2. 環境設定

1. 複製 `env.example` 為 `.env` 並填入設定值：
```bash
cp env.example .env
```

2. 設定必要的環境變數：
   - `LINE_CHANNEL_ACCESS_TOKEN`: LINE Bot Channel Access Token
   - `GCP_PROJECT_ID`: GCP 專案 ID（分離架構需要）
   - `GCS_BUCKET_NAME`: GCS Bucket 名稱（分離架構需要）
   - `OPENAI_API_KEY`: OpenAI API Key（LLM 功能需要）
   - `LOCALHOST_REVIEW_URL`: 本地 KataGo 服務 URL（分離架構需要）
   - `CLOUD_RUN_CALLBACK_REVIEW_URL`: Cloud Run 回調 URL（分離架構需要）

3. 準備 KataGo 模型檔案：
   - 下載 KataGo 模型至 `katago/models/` 目錄
   - 支援的模型格式：`.bin.gz`

### 3. 執行

#### 本地完整版本

```bash
cd apps/localhost_all
python -m uvicorn src.main:app --reload --port 3000
```

#### 分離架構

**啟動本地 KataGo 服務：**
```bash
cd apps/gcp_linebot_localhost_katago/localhost_katago
python -m uvicorn main:app --reload --port 8000
```

**啟動 Cloud Run 服務（本地測試）：**
```bash
cd apps/gcp_linebot_localhost_katago/gcp_linebot
python -m uvicorn main:app --reload --port 8080
```

**部署至 Cloud Run：**
```bash
cd apps/gcp_linebot_localhost_katago/gcp_linebot
./scripts/deploy.sh
```

## 使用方式

1. 在 LINE 中搜尋並加入您的 Bot
2. 上傳 `.sgf` 棋譜檔案，或發送文字指令 `覆盤`
3. Bot 會自動執行以下流程：
   - 解析 SGF 檔案
   - 執行 KataGo 全盤覆盤（15-20 分鐘）
   - 篩選關鍵手數（勝率差距最大的前 20 手）
   - 生成關鍵手數的 GIF 動畫
   - 使用 ChatGPT 生成中文評論
   - 以 Flex Message 形式回傳結果

### 指令

- `help` / `幫助` - 顯示說明
- `覆盤` - 對最近上傳的 SGF 檔案進行覆盤分析

## 技術架構

- **後端框架**: FastAPI
- **LINE Bot SDK**: line-bot-sdk (Python)
- **圍棋引擎**: KataGo
- **AI 分析**: OpenAI GPT
- **GCP 服務**: 
  - Cloud Run (Webhook 服務)
  - Cloud Storage (GCS) - 檔案儲存
- **圖像處理**: Pillow, imageio
- **語言**: Python 3.8+

## 功能說明

### KataGo 覆盤分析

- 支援全盤覆盤，分析每手棋的勝率變化
- 可設定分析深度（visits 參數）
- 自動篩選關鍵手數（勝率差距最大的手數）

### GIF 動畫生成

- 為每個關鍵手數生成 GIF 動畫
- 顯示該手棋的落子位置與勝率變化
- 包含全局棋盤圖

### LLM 評論生成

- 使用 ChatGPT 分析關鍵手數
- 生成中文評論與建議
- 整合至 Flex Message 顯示

## 注意事項

- ⚠️ KataGo 全盤覆盤需要 15-20 分鐘（視棋譜長度而定）
- ⚠️ 需要足夠的計算資源執行 KataGo
- ✅ 建議使用 GPU 加速 KataGo 分析
- ✅ 分離架構適合生產環境，可獨立擴展服務
- ✅ 所有分析結果儲存在 GCS，支援持久化

## License

MIT

