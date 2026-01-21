# 本地完整架構

## 架構說明

此架構將所有服務整合在本地運行：

- **LINE Bot Webhook 服務**：處理 LINE Bot 請求
- **圍棋對弈**：支援在 LINE 中直接下圍棋
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

#### 2. 覆盤功能測試

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
│   └── analysis.py                  # 分析邏輯
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

編輯 `katago/configs/default_analysis.cfg` 調整分析參數：
- `visits`: 分析深度（預設 5，可增加以提高準確度但會增加執行時間）

### 本地測試 SGF 檔案

可以將 SGF 檔案放在 `static/` 目錄中，透過瀏覽器訪問進行測試。
