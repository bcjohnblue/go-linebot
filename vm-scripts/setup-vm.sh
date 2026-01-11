#!/bin/bash
# GCP VM 初始化腳本
# 此腳本用於準備包含 KataGo 的 VM 映像檔
# 執行此腳本後，建立映像檔快照供後續使用

set -e

echo "Setting up KataGo VM environment..."

# 更新系統
sudo apt-get update
sudo apt-get upgrade -y

# 安裝必要套件
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  python3 \
  python3-pip \
  wget \
  curl \
  unzip

# 安裝 Google Cloud SDK（如果尚未安裝）
if ! command -v gcloud &> /dev/null; then
  echo "Installing Google Cloud SDK..."
  curl https://sdk.cloud.google.com | bash
  exec -l $SHELL
fi

# 安裝 gsutil（GCS 工具）
if ! command -v gsutil &> /dev/null; then
  echo "Installing gsutil..."
  gcloud components install gsutil
fi

# 下載並編譯 KataGo（範例，請根據實際需求調整）
KATAGO_DIR="/opt/katago"
if [ ! -d "$KATAGO_DIR" ]; then
  echo "Installing KataGo..."
  sudo mkdir -p "$KATAGO_DIR"
  cd "$KATAGO_DIR"
  
  # 這裡應該下載並編譯 KataGo
  # 範例（請替換為實際的安裝步驟）：
  # git clone https://github.com/lightvector/KataGo.git
  # cd KataGo/cpp
  # cmake . -DUSE_BACKEND=OPENCL
  # make -j4
  
  echo "KataGo installation completed"
fi

# 下載 KataGo 模型（範例）
MODEL_DIR="/etc/katago"
sudo mkdir -p "$MODEL_DIR"
# 這裡應該下載模型檔案
# wget -O "$MODEL_DIR/model.bin.gz" "https://katagotraining.org/models/..."

# 建立分析腳本
sudo mkdir -p /home/ubuntu
sudo cp /tmp/analyze.sh /home/ubuntu/analyze.sh
sudo chmod +x /home/ubuntu/analyze.sh
sudo chown ubuntu:ubuntu /home/ubuntu/analyze.sh

echo "VM setup completed!"
echo "Next steps:"
echo "1. Test KataGo installation"
echo "2. Create VM image snapshot"
echo "3. Use the snapshot as source image for Preemptible VMs"

