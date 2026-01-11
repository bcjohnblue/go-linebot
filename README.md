# Go LINE Bot - KataGo Analysis with GCP Preemptible VMs

這是一個 LINE Bot，用於接收 SGF 棋譜檔案並在 GCP Preemptible VM 上執行 KataGo 分析。

## 功能特色

- 📱 透過 LINE Bot 接收 SGF 棋譜檔案
- ☁️ 自動啟動 GCP Preemptible VM 執行分析
- 💰 使用低成本 Preemptible VM（約 70-80% 折扣）
- 🔄 自動重試機制處理 VM 中斷
- 📊 分析結果自動回傳至 LINE

## 架構流程

```
1. 用戶透過 LINE 上傳 SGF 檔案
2. Bot 接收檔案並上傳至 GCS
3. 啟動 GCP Preemptible VM
4. VM 執行 KataGo 分析
5. 結果儲存至 GCS
6. 關閉 VM
7. 回傳結果至 LINE
```

## 專案結構

```
go-linebot/
├── src/
│   ├── config.js              # 設定檔管理
│   ├── index.js               # 主應用程式入口
│   ├── handlers/
│   │   └── lineHandler.js     # LINE Bot 訊息處理
│   └── services/
│       ├── storage.js         # GCS 儲存服務
│       ├── vmManager.js       # GCP VM 管理
│       └── taskManager.js     # 任務管理與監控
├── vm-scripts/
│   ├── analyze.sh            # VM 上的 KataGo 分析腳本
│   └── setup-vm.sh           # VM 初始化腳本
├── package.json
├── Dockerfile
├── env.example               # 環境變數範例
├── README.md
└── DEPLOYMENT.md            # 詳細部署指南
```

## 快速開始

### 1. 安裝依賴

```bash
npm install
```

### 2. 環境設定

1. 複製 `env.example` 為 `.env` 並填入設定值：
```bash
cp env.example .env
```

2. 準備 GCP Service Account Key JSON 檔案（參考 `DEPLOYMENT.md`）

3. 在 GCP 建立 GCS Bucket：
```bash
gsutil mb -p YOUR_PROJECT_ID -l asia-east1 gs://go-linebot-storage
```

4. 準備 KataGo VM 映像檔（參考 `DEPLOYMENT.md` 和 `vm-scripts/`）

### 3. 執行

```bash
# 開發模式（自動重載）
npm run dev

# 生產模式
npm start
```

## 使用方式

1. 在 LINE 中搜尋並加入您的 Bot
2. 上傳 `.sgf` 棋譜檔案
3. Bot 會自動啟動 GCP Preemptible VM 進行分析
4. 分析完成後，結果會自動回傳至 LINE

### 指令

- `help` / `幫助` - 顯示說明
- `status` / `狀態` - 查看任務狀態（開發中）

## 技術架構

- **後端框架**: Express.js
- **LINE Bot SDK**: @line/bot-sdk
- **GCP 服務**: 
  - Compute Engine (Preemptible VMs)
  - Cloud Storage (GCS)
- **語言**: Node.js (ES Modules)

## 成本估算

假設每天 10 次分析，每次 2 分鐘：
- **VM 成本**: 約 $0.20 USD/月
- **儲存成本**: < $0.01 USD/月
- **總計**: 約 **$0.21 USD/月**

## 注意事項

- ⚠️ Preemptible VM 最長存活 24 小時
- ⚠️ VM 可能隨時被中斷（約 30 秒前通知）
- ✅ 建議任務時間 < 10 分鐘以降低中斷風險
- ✅ 所有狀態與結果都儲存在外部儲存（GCS）
- ✅ 自動重試機制（最多 3 次）

## 詳細部署指南

請參考 [DEPLOYMENT.md](./DEPLOYMENT.md) 了解完整的部署步驟。

## License

MIT

