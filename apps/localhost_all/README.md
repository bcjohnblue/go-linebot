# 本地完整架構

## 架構說明

此架構將所有服務整合在本地運行：

- **LINE Bot Webhook 服務**：處理 LINE Bot 請求
- **圍棋對弈**：支援在 LINE 中直接下圍棋
- **AI 對弈**：與 KataGo AI 進行對戰
- **KataGo 分析服務**：執行全盤覆盤分析
- **GIF 生成**：生成關鍵手數的 GIF 動畫
- **LLM 評論**：使用 ChatGPT 生成中文評論

### 架構流程

**對弈功能：**
```
1. 用戶在 LINE 中輸入座標（如 D4, Q16）
2. 本地服務接收請求並驗證落子合法性
3. 更新棋盤狀態並保存為 SGF 格式
4. 生成當前棋盤圖片並回傳至 LINE
```

**形勢判斷功能：**
```
1. 用戶在進行中的對局裡輸入「形勢」或「形式」或「evaluation」
2. 本地服務取得當前對局的 SGF 路徑
3. 執行 KataGo evaluation 分析（單一盤面 ownership / scoreLead）
4. 繪製領地分布圖（實心黑/白方塊標示）並回傳目數差距與圖片至 LINE
```

**AI 對弈功能：**
```
1. 用戶輸入「對弈 ai」開啟 AI 對弈模式
2. 用戶在 LINE 中輸入座標（如 D4, Q16）
3. 本地服務接收請求並驗證落子合法性
4. 更新棋盤狀態並保存為 SGF 格式
5. 生成用戶的棋盤圖片（暫不回傳）
6. 本地服務執行 KataGo GTP 獲取 AI 下一步棋
7. 更新 SGF 檔案並生成 AI 的棋盤圖片
8. 合併回傳：用戶的棋盤圖片 + AI 的棋盤圖片 + 文字訊息
```

**覆盤分析功能：**
```
1. 用戶透過 LINE 上傳 SGF 檔案或發送"覆盤"指令
2. 本地服務接收請求並解析 SGF
3. 執行 KataGo 全盤覆盤分析
4. 篩選關鍵手數（勝率差距最大的前 20 手）
5. 生成關鍵手數的 GIF 動畫
6. 調用 LLM 生成中文評論
7. 結果以 Flex Message 回傳至 LINE
```

## 前置準備

### 1. Python 環境設定

#### 1.1 安裝 Python

確保已安裝 Python 3.8 或更高版本：

```bash
python --version
```

#### 1.2 建立虛擬環境

```bash
cd apps/localhost_all
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

#### 1.3 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. KataGo 設定

#### 2.1 安裝 KataGo 二進制文件

請參考 [設定與運行 KataGo](https://github.com/lightvector/KataGo?tab=readme-ov-file#windows-and-linux) 中對於不同作業系統的安裝方式

**以 macOS 為例:**
```bash
# 使用 Homebrew
brew install katago
```

#### 2.2 下載 KataGo 模型

前往 [KataGo 模型下載網址](https://katagotraining.org/networks/)，下載運行 KataGo 的模型，推薦使用 `kata1-b28c512nbt-s12192929536-d5655876072` (2026/01)，並將模型放置到 `apps/localhost_all/katago/models` 資料夾底下


### 3. 環境變數設定

#### 3.1 建立 .env 檔案

```bash
cd apps/localhost_all
cp env.example .env
```

#### 3.2 編輯 .env 檔案

```bash
# LINE Bot Configuration
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token

# Server Configuration
PORT=3000
PUBLIC_URL=https://your-public-domain.com  # 用於 LINE Webhook，本地測試可使用 ngrok or cloudflare tunnel

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

### 4. LINE Bot 設定

1. 前往 [LINE Developers Console](https://developers.line.biz/console/)
2. 建立新的 Provider 和 Channel
3. 取得 Channel Access Token
4. 設定 Webhook URL

## 執行方式

### 啟動服務

```bash
cd apps/localhost_all
source venv/bin/activate
python src/main.py
```

### 測試

#### 1. 對弈功能測試

**開始下棋：**
- 在 LINE 中直接輸入座標（如 `D4`、`Q16`）即可落子
- Bot 會自動顯示當前棋盤狀態
- 自動輪流下黑白棋

**對弈指令：**
- `悔棋` / `undo` - 撤銷上一步
- `讀取` / `load` - 從存檔恢復當前遊戲
- `重置` / `reset` - 重置棋盤，開始新遊戲
- `投子` - 認輸並結束本局（先顯示勝負再重置棋盤）
- `形勢` / `形式` / `evaluation` - 顯示當前盤面領地分布與目數差距（形勢判斷）

#### 2. AI 對弈功能測試

**開啟 AI 對弈模式：**
- 輸入 `對弈 ai` 或 `vs ai` 開啟 AI 對弈模式
- 輸入 `對弈 free` 或 `vs free` 關閉 AI 對弈模式，恢復一般對弈模式
- 輸入 `對弈` 或 `vs` 查看當前模式狀態

**開始與 AI 對戰：**
- 開啟 AI 對弈模式後，開始下棋（例如：`D4`）
- AI 會自動思考並下下一步棋
- 系統會合併回傳用戶的棋盤圖片和 AI 的棋盤圖片

**技術規格：**
- AI 引擎：KataGo GTP
- 思考時間：約 10 秒內
- 配置檔案：`katago/configs/default_gtp.cfg`

#### 3. 覆盤功能測試

**上傳 SGF 檔案：**
- 在 LINE 中上傳 `.sgf` 棋譜檔案，Bot 會自動接收並儲存

**發送覆盤指令：**
- 發送文字訊息 `覆盤`，Bot 會：
  1. 解析最近上傳的 SGF 檔案
  2. 執行 KataGo 全盤覆盤
  3. 生成關鍵手數的 GIF 動畫
  4. 使用 ChatGPT 生成中文評論
  5. 以 Flex Message 回傳結果

## 專案結構

```
localhost_all/
├── src/                             # 主程式
│   ├── main.py                      # FastAPI 主程式
│   ├── config.py                    # 設定檔
│   ├── handlers/                    # 處理器
│   │   ├── line_handler.py          # LINE Bot 處理
│   │   ├── go_engine.py             # 圍棋規則引擎
│   │   ├── sgf_handler.py           # SGF 解析
│   │   ├── katago_handler.py        # KataGo 處理
│   │   ├── draw_handler.py          # GIF 生成
│   │   └── board_visualizer.py      # 棋盤視覺化
│   ├── LLM/
│   │   └── providers/               # LLM 提供者
│   │       └── openai_provider.py   # OpenAI 提供者
│   └── logger.py                    # 日誌設定
├── katago/                          # KataGo 相關檔案
│   ├── models/                      # KataGo 模型
│   ├── configs/                     # 設定檔
│   ├── review.py                    # 覆盤分析邏輯
│   └── evaluation.py                # 形勢判斷邏輯
├── assets/                          # 圖片資源
│   ├── board.png                    # 棋盤圖片
│   ├── black.png                    # 黑子圖片
│   └── white.png                    # 白子圖片
├── draw/                            # 繪圖相關
│   └── draw.py                      # 繪圖邏輯
├── static/                          # 靜態檔案（測試用 SGF）
├── env.example                      # 環境變數範例
└── requirements.txt                 # Python 依賴
```

### 調整 KataGo 分析參數

**覆盤分析參數：**

編輯 `katago/configs/default_analysis.cfg` 調整分析參數：
- `visits`: 分析深度（預設 5，可增加以提高準確度但會增加執行時間）

**AI 對弈參數：**

編輯 `katago/configs/default_gtp.cfg` 調整 AI 對弈參數：
- `maxVisits`: AI 思考深度（預設 400，可增加以提高 AI 強度但會增加思考時間）
- `numSearchThreads`: 搜尋線程數（預設 6，可根據 CPU 核心數調整）
- `maxTime`: 最大思考時間（預設 10.0 秒）

### 本地測試 SGF 檔案

可以將 SGF 檔案放在 `static/` 目錄中，透過瀏覽器訪問進行測試。
