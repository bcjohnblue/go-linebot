#!/bin/bash
# KataGo 覆盤分析腳本（全盤 review）
# 使用方式: ./review.sh <sgf_file>
# 使用 katawrap.py 來讀取 SGF 文件並傳遞給 KataGo，輸出 JSONL 格式

set -e

# 獲取腳本所在目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KATAGO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODAL_KATAGO_DIR="$(cd "$KATAGO_DIR/.." && pwd)"

# KataWrap Python 路徑（Modal 環境使用系統 Python）
VENV_PY="${VENV_PY:-/usr/local/bin/python}"

# Debug: Log which Python is being used
echo "[DEBUG] Using Python: $VENV_PY"
echo "[DEBUG] Python version: $($VENV_PY --version 2>&1)"
echo "[DEBUG] Checking chardet availability..."
$VENV_PY -c "import chardet; print(f'[DEBUG] chardet found at: {chardet.__file__}')" 2>&1 || echo "[DEBUG] WARNING: chardet not available in $VENV_PY"

# SGF 文件參數（必需）
SGF_FILE="$1"
# 搜索次數（預設 5）
VISITS="${VISITS:-5}"

# KataGo 路徑設定
KATAGO_BIN="${KATAGO_BIN:-katago}"
KATAGO_CONFIG="${KATAGO_CONFIG:-$KATAGO_DIR/configs/default_analysis.cfg}"
KATAGO_MODEL="${KATAGO_MODEL:-$KATAGO_DIR/models/kata1-b28c512nbt-s12192929536-d5655876072.bin.gz}"
KATAWRAP_PY="${KATAWRAP_PY:-$KATAGO_DIR/packages/katawrap-main/katawrap/katawrap.py}"

# 檢查 KataGo 是否存在
if ! command -v "$KATAGO_BIN" &> /dev/null; then
  echo "Error: KataGo binary not found. Please set KATAGO_BIN environment variable."
  echo "Example: export KATAGO_BIN=/usr/local/bin/katago"
  exit 1
fi

# 檢查配置文件
if [ ! -f "$KATAGO_CONFIG" ]; then
  echo "Error: Config file not found: $KATAGO_CONFIG"
  exit 1
fi

# 檢查模型文件
if [ ! -f "$KATAGO_MODEL" ]; then
  echo "Error: Model file not found: $KATAGO_MODEL"
  exit 1
fi

# 檢查 katawrap.py 是否存在
if [ ! -f "$KATAWRAP_PY" ]; then
  echo "Error: katawrap.py not found: $KATAWRAP_PY"
  exit 1
fi

# 如果提供了 SGF 文件參數，檢查文件是否存在
if [ -n "$SGF_FILE" ]; then
  if [ ! -f "$SGF_FILE" ]; then
    echo "Error: SGF file not found: $SGF_FILE"
    exit 1
  fi
  # 轉換為絕對路徑
  SGF_FILE_ABS="$(cd "$(dirname "$SGF_FILE")" && pwd)/$(basename "$SGF_FILE")"
else
  echo "Error: SGF file is required"
  echo "Usage: $0 <sgf_file>"
  exit 1
fi

# 執行分析
echo "Starting KataGo review analysis..."
echo "Config: $KATAGO_CONFIG"
echo "Model:  $KATAGO_MODEL"
echo "SGF File: $SGF_FILE_ABS"
echo "Visits: $VISITS"
echo ""

# 切換到 KataGo 目錄執行（確保相對路徑正確）
cd "$KATAGO_DIR"

# 使用 katawrap.py 讀取 SGF 文件並傳遞給 KataGo
# katawrap.py 會從標準輸入讀取 JSON 查詢，我們通過 echo 傳遞 sgfFile
# 注意：配置文件中的 sgfFile 和 outputDir 設置會被忽略，因為我們通過 JSON 查詢傳遞

# 設置輸出 JSONL 文件路徑
OUTPUT_DIR="${OUTPUT_DIR:-$KATAGO_DIR/results}"
mkdir -p "$OUTPUT_DIR"
SGF_BASENAME=$(basename "$SGF_FILE_ABS" .sgf)

# 如果沒有通過環境變量指定輸出文件名，則自動生成（帶時間戳）
if [ -z "$OUTPUT_JSONL" ]; then
  # 生成時間戳（年月日時分）
  TIMESTAMP=$(date +"%Y%m%d%H%M")
  OUTPUT_JSONL="$OUTPUT_DIR/${SGF_BASENAME}_analysis_${TIMESTAMP}.jsonl"
else
  # 如果 OUTPUT_JSONL 是相對路徑，轉換為絕對路徑
  if [[ "$OUTPUT_JSONL" != /* ]]; then
    OUTPUT_JSONL="$OUTPUT_DIR/$OUTPUT_JSONL"
  fi
fi

# 使用 katawrap.py 讀取 SGF 文件並傳遞給 KataGo (輸出 JSONL)
# 輸出格式：JSONL (JSON Lines)，每行一個 JSON 對象，包含分析結果
echo "Using katawrap.py to generate JSONL output..."
echo "Output file: $OUTPUT_JSONL"
echo "Note: Output is JSONL format (not SGF)."
echo "      Each line is a JSON object with analysis results."
echo ""

echo "{\"sgfFile\": \"$SGF_FILE_ABS\"}" | \
 "$VENV_PY" "$KATAWRAP_PY" \
  -visits "$VISITS" \
  "$KATAGO_BIN" analysis \
  -config "$KATAGO_CONFIG" \
  -model "$KATAGO_MODEL" \
  > "$OUTPUT_JSONL"

if [ $? -eq 0 ]; then
  echo ""
  echo "Review analysis completed. Output saved to: $OUTPUT_JSONL"
fi

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo ""
  echo "Review analysis completed successfully! JSONL output generated."
else
  echo ""
  echo "Error: KataGo review analysis failed"
  exit 1
fi
