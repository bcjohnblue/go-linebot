#!/bin/bash
# KataGo 分析腳本
# 此腳本應該部署在 GCP VM 映像檔中
# 使用方式: ./analyze.sh <input.sgf> <output.txt>

set -e

INPUT_SGF="$1"
OUTPUT_TXT="$2"

if [ -z "$INPUT_SGF" ] || [ -z "$OUTPUT_TXT" ]; then
  echo "Usage: $0 <input.sgf> <output.txt>"
  exit 1
fi

# KataGo 設定（根據您的實際安裝路徑調整）
KATAGO_BIN="/usr/local/bin/katago"
KATAGO_CONFIG="/etc/katago/config.cfg"
KATAGO_MODEL="/etc/katago/model.bin.gz"

# 檢查 KataGo 是否存在
if [ ! -f "$KATAGO_BIN" ]; then
  echo "Error: KataGo binary not found at $KATAGO_BIN"
  exit 1
fi

# 執行分析
echo "Starting KataGo analysis..."
echo "Input: $INPUT_SGF"
echo "Output: $OUTPUT_TXT"

# 執行 KataGo 分析
# 這裡是範例命令，請根據您的實際需求調整
$KATAGO_BIN analysis \
  -model "$KATAGO_MODEL" \
  -config "$KATAGO_CONFIG" \
  -sgf "$INPUT_SGF" \
  -output "$OUTPUT_TXT" \
  -analysis-threads 2 \
  -max-visits 1000

echo "Analysis completed successfully!"
echo "Results saved to: $OUTPUT_TXT"

