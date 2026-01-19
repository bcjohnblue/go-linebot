# Modal KataGo 架構實作總結

## 已完成的工作

### 1. Modal 應用程式 (`modal_katago/`)

✅ **建立 `main.py`**
- 定義 Modal 應用程式和 `review_katago` 函數
- 設定 GPU 資源（T4）、記憶體（8GB）和超時（1小時）
- 掛載整個 `modal_katago` 目錄以訪問 handlers 和 katago 腳本
- 在 Modal image 中安裝 KataGo 二進制文件
- 實作 GCS 下載/上傳和回調 GCP 的邏輯

✅ **更新 `config.py`**
- 新增 Modal 相關配置（app_name, function_name）

✅ **更新 `requirements.txt`**
- 新增 `modal>=0.60.0` 依賴

✅ **`katago_handler.py`**
- 無需修改，已可適配 Modal 環境（透過掛載目錄訪問）

### 2. GCP Cloud Run (`gcp_linebot/`)

✅ **修改 `handlers/line_handler.py`**
- 將 `handle_review_command` 從調用 localhost 改為調用 Modal 函數
- 使用 Modal SDK 的 `spawn()` 方法非同步調用 Modal 函數
- 移除 localhost URL 相關邏輯

✅ **更新 `config.py`**
- 移除 `localhost.review_url` 配置
- 新增 `modal.app_name` 和 `modal.function_name` 配置

✅ **更新 `requirements.txt`**
- 新增 `modal>=0.60.0` 依賴

✅ **`main.py`**
- `/callback/review` 端點保持不變，Modal 會回調此端點

### 3. 文件

✅ **建立 `MODAL_SETUP.md`**
- 詳細說明如何設定 Modal secrets
- 說明如何部署 Modal 應用程式
- 提供疑難排解指南

## 待完成的工作（需要手動操作）

### 1. 設定 Modal Secrets

執行以下命令建立 Modal secret：

```bash
modal secret create gcp-credentials \
  GCP_PROJECT_ID=your-project-id \
  GCS_BUCKET_NAME=your-bucket-name \
  GCP_SERVICE_ACCOUNT_KEY_JSON='<完整的 JSON key 內容>' \
  CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review
```

詳細說明請參考 `MODAL_SETUP.md`。

### 2. 部署 Modal 應用程式

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
modal deploy main.py
```

### 3. 設定 GCP Cloud Run 環境變數

在 Cloud Run 服務中設定：
- `MODAL_APP_NAME=katago`
- `MODAL_FUNCTION_REVIEW=review`
- `CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review`

### 4. 測試

1. 透過 LINE Bot 上傳 SGF 檔案
2. 發送「覆盤」指令
3. 檢查 Modal 日誌：`modal app logs katago-review`

## 架構流程

```
LINE 用戶 → GCP Cloud Run → Modal 函數 → KataGo 分析 → GCS 上傳 → 回調 GCP → LLM/GIF 生成 → LINE 用戶
```

## 注意事項

1. **KataGo 安裝**：Modal image 中已包含 KataGo 安裝步驟，但如果版本不匹配，可能需要調整 `main.py` 中的下載 URL。

2. **GPU 配額**：確保 Modal 帳號有足夠的 GPU 配額（T4）。

3. **GCS 權限**：確保 Service Account 有足夠權限存取 GCS bucket。

4. **回調 URL**：確保 Cloud Run 服務的 `/callback/review` 端點公開可訪問。

5. **Python 路徑**：在 Modal 環境中，`analysis.sh` 使用系統 Python（`/usr/bin/python3`），已透過環境變數 `VENV_PY` 設定。

## 檔案變更清單

### 新增檔案
- `modal_katago/main.py` - Modal 應用程式定義
- `MODAL_SETUP.md` - Modal 設定指南
- `IMPLEMENTATION_SUMMARY.md` - 本文件

### 修改檔案
- `modal_katago/config.py` - 新增 Modal 配置
- `modal_katago/requirements.txt` - 新增 modal 依賴
- `gcp_linebot/handlers/line_handler.py` - 改用 Modal SDK 調用
- `gcp_linebot/config.py` - 移除 localhost，新增 Modal 配置
- `gcp_linebot/requirements.txt` - 新增 modal 依賴

### 未修改檔案（但需要確認）
- `gcp_linebot/main.py` - `/callback/review` 端點保持不變
- `modal_katago/handlers/katago_handler.py` - 無需修改，透過掛載訪問

