# Modal KataGo 設定指南

本文件說明如何設定 Modal 環境以執行 KataGo 分析。

## 前置需求

1. Modal 帳號（https://modal.com/）
2. Modal CLI 已安裝並完成認證
3. GCP Service Account JSON key（用於存取 GCS）

## 步驟 1: 安裝 Modal CLI

```bash
pip install modal
```

## 步驟 2: 登入 Modal

```bash
modal setup
```

按照指示完成認證。

## 步驟 3: 建立 Modal Secret

建立一個名為 `gcp-credentials` 的 Modal secret，包含以下環境變數：

```bash
modal secret create gcp-credentials \
  GCP_PROJECT_ID=your-project-id \
  GCS_BUCKET_NAME=your-bucket-name \
  GCP_SERVICE_ACCOUNT_KEY_JSON='{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"...","client_email":"...","client_id":"...","auth_uri":"...","token_uri":"...","auth_provider_x509_cert_url":"...","client_x509_cert_url":"..."}' \
  CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review
```

**注意**：
- `GCP_SERVICE_ACCOUNT_KEY_JSON` 應該是完整的 JSON key 內容（作為單一字串）
- `CLOUD_RUN_CALLBACK_REVIEW_URL` 是 GCP Cloud Run 服務的回調端點 URL

### 替代方法：從檔案建立 Secret

如果您有 Service Account JSON 檔案：

```bash
# 讀取 JSON 檔案內容
GCP_KEY=$(cat path/to/service-account-key.json)

# 建立 secret
modal secret create gcp-credentials \
  GCP_PROJECT_ID=your-project-id \
  GCS_BUCKET_NAME=your-bucket-name \
  GCP_SERVICE_ACCOUNT_KEY_JSON="$GCP_KEY" \
  CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review
```

## 步驟 4: 上傳 KataGo 模型到 Modal Volume

在部署應用程式之前，需要先將 KataGo 模型上傳到 Modal Volume。模型檔案使用 Modal Volume 進行持久化儲存，避免每次部署時都包含大型模型檔案。

進入 `modal_katago` 目錄並執行上傳：

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
modal run main.py::upload_model
```

**重要說明**：
- 模型檔案路徑：`katago/models/kata1-b28c512nbt-s12192929536-d5655876072.bin.gz`
- 模型檔案較大（約 1-2 GB），上傳可能需要 5-10 分鐘
- **只需要上傳一次**，之後所有函數調用都會使用 Volume 中的模型
- 如果模型檔案不存在，會顯示錯誤訊息，請確認檔案路徑正確
- 上傳成功後，模型會儲存在名為 `katago-models` 的 Modal Volume 中

**驗證上傳**：
上傳完成後，您可以使用以下命令檢查 Volume 內容：
```bash
modal volume list katago-models
```

## 步驟 5: 部署 Modal 應用程式

### 方法 1: 使用部署腳本（推薦）

使用自動化部署腳本，它會自動檢查所有前置條件：

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
./scripts/deploy.sh
```

部署腳本會自動：
- 檢查 Modal CLI 是否安裝
- 檢查 Modal 認證狀態
- 檢查 Modal Secret 是否存在
- 檢查模型是否已上傳到 Volume（如果沒有，會提示上傳）
- 部署 Modal 應用程式

### 方法 2: 手動部署

進入 `modal_katago` 目錄並部署：

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
modal deploy main.py
```

**注意**：確保在部署前已經完成步驟 4（上傳模型），否則函數執行時會找不到模型檔案。

## 步驟 6: 設定 GCP Cloud Run 環境變數

在 GCP Cloud Run 服務中設定以下環境變數：

- `MODAL_APP_NAME=katago`（或您自訂的名稱）
- `MODAL_FUNCTION_REVIEW=review`
- `CLOUD_RUN_CALLBACK_REVIEW_URL=https://your-cloud-run-url/callback/review`

## 步驟 7: 測試

1. 透過 LINE Bot 上傳 SGF 檔案
2. 發送「覆盤」指令
3. 檢查 Modal 日誌：`modal app logs katago-review`

## 疑難排解

### Modal 函數無法找到 KataGo 模型檔案

如果出現模型檔案不存在的錯誤：

1. 確認已經執行 `modal run main.py::upload_model` 上傳模型
2. 檢查模型檔案路徑是否正確：`katago/models/kata1-b28c512nbt-s12192929536-d5655876072.bin.gz`
3. 確認 Volume 名稱是否正確：`katago-models`
4. 檢查 Volume 內容：`modal volume list katago-models`

### Modal 函數無法找到 KataGo 二進制文件

確保 Modal image 中正確安裝了 KataGo。如果預設的安裝方式不適用，您可能需要：

1. 調整 `main.py` 中的 KataGo 安裝命令
2. 或使用包含 KataGo 的自訂 Docker image

### GCS 存取錯誤

檢查：
1. Service Account key 是否正確設定在 Modal secret 中
2. Service Account 是否有足夠的權限存取 GCS bucket
3. `GCP_PROJECT_ID` 和 `GCS_BUCKET_NAME` 是否正確

### 回調失敗

檢查：
1. `CLOUD_RUN_CALLBACK_REVIEW_URL` 是否正確
2. Cloud Run 服務是否公開可訪問
3. `/callback/review` 端點是否正確設定

## 本地測試

您可以使用 Modal 的本地測試功能：

```bash
cd apps/gcp_linebot_modal_katago/modal_katago
modal serve main.py
```

這會在本地啟動 Modal 服務，方便測試。

