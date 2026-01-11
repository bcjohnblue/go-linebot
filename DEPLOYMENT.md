# 部署指南

## 前置準備

### 1. GCP 設定

#### 建立 Service Account
```bash
# 建立 Service Account
gcloud iam service-accounts create go-linebot-sa \
  --display-name="Go LINE Bot Service Account"

# 授予必要權限
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:go-linebot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:go-linebot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.admin"

# 建立並下載金鑰
gcloud iam service-accounts keys create gcp-service-account-key.json \
  --iam-account=go-linebot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

#### 建立 GCS Bucket
```bash
gsutil mb -p YOUR_PROJECT_ID -l asia-east1 gs://go-linebot-storage
```

### 2. 準備 KataGo VM 映像檔

#### 方法一：使用現有映像檔並安裝 KataGo

1. 建立一個標準 VM：
```bash
gcloud compute instances create katago-setup \
  --zone=asia-east1-a \
  --machine-type=n1-standard-2 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud
```

2. SSH 進入 VM 並執行安裝：
```bash
gcloud compute ssh katago-setup --zone=asia-east1-a

# 在 VM 內執行
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3 python3-pip

# 編譯並安裝 KataGo（請參考 KataGo 官方文件）
# 下載 KataGo 模型

# 建立分析腳本
sudo mkdir -p /home/ubuntu
sudo nano /home/ubuntu/analyze.sh
# 貼上 vm-scripts/analyze.sh 的內容
sudo chmod +x /home/ubuntu/analyze.sh
```

3. 建立映像檔快照：
```bash
# 停止 VM
gcloud compute instances stop katago-setup --zone=asia-east1-a

# 建立映像檔
gcloud compute images create katago-image \
  --source-disk=katago-setup \
  --source-disk-zone=asia-east1-a \
  --family=katago
```

4. 更新 `.env` 中的映像檔設定：
```env
VM_IMAGE_PROJECT=YOUR_PROJECT_ID
VM_IMAGE_FAMILY=katago
```

#### 方法二：使用 Container-Optimized OS 與 Docker

如果 KataGo 已容器化，可以使用 Container-Optimized OS 映像檔。

### 3. LINE Bot 設定

1. 前往 [LINE Developers Console](https://developers.line.biz/console/)
2. 建立新的 Provider 和 Channel
3. 取得 Channel Access Token 和 Channel Secret
4. 設定 Webhook URL：`https://your-domain.com/webhook`

### 4. 環境變數設定

複製 `env.example` 為 `.env` 並填入所有必要值：
```bash
cp env.example .env
nano .env
```

## 本地開發

```bash
# 安裝依賴
npm install

# 啟動開發伺服器
npm run dev
```

## 部署至 GCP Cloud Run

### 1. 建立 Dockerfile

```dockerfile
FROM node:18-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .

EXPOSE 3000

CMD ["node", "src/index.js"]
```

### 2. 部署

```bash
# 建立 Docker 映像檔
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/go-linebot

# 部署至 Cloud Run
gcloud run deploy go-linebot \
  --image gcr.io/YOUR_PROJECT_ID/go-linebot \
  --platform managed \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-env-vars="LINE_CHANNEL_ACCESS_TOKEN=xxx,LINE_CHANNEL_SECRET=xxx,GCP_PROJECT_ID=xxx,..." \
  --set-secrets="GCP_SERVICE_ACCOUNT_KEY=gcp-service-account-key:latest"
```

### 3. 設定 Webhook URL

在 LINE Developers Console 中設定 Webhook URL 為 Cloud Run 的 URL。

## 監控與除錯

### 查看 VM 狀態
```bash
gcloud compute instances list --filter="name~katago-worker"
```

### 查看 GCS 檔案
```bash
gsutil ls gs://go-linebot-storage/sgf/
gsutil ls gs://go-linebot-storage/results/
```

### 查看日誌
```bash
# Cloud Run 日誌
gcloud run services logs read go-linebot --region asia-east1
```

## 成本估算

假設：
- 每天 10 次分析
- 每次分析 2 分鐘
- n1-standard-2 Preemptible VM

每月成本約：
- VM 使用時間：10 × 2 分鐘 × 30 天 = 600 分鐘 = 10 小時
- VM 成本：10 小時 × $0.02 USD/小時 = $0.20 USD
- GCS 儲存：< $0.01 USD
- **總計：約 $0.21 USD/月**

## 注意事項

1. Preemptible VM 最長存活 24 小時
2. VM 可能隨時被中斷（約 30 秒前通知）
3. 建議任務時間 < 10 分鐘以降低中斷風險
4. 所有狀態與結果都儲存在外部儲存（GCS）
5. 生產環境建議使用 Redis 或資料庫儲存任務狀態

