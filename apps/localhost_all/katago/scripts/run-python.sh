#!/bin/bash
# 在虛擬環境中運行 analysis.py
# 使用方式: ./run-python.sh [analysis.py 的參數...]

set -e

# 獲取腳本所在目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KATAGO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 虛擬環境路徑
VENV_DIR="$KATAGO_DIR/venv"
VENV_PY="$VENV_DIR/bin/python"
ANALYSIS_PY="$KATAGO_DIR/analysis.py"

# 檢查虛擬環境是否存在
if [ ! -f "$VENV_PY" ]; then
    echo "Virtual environment not found. Creating one..."
    echo "Location: $VENV_DIR"
    echo ""
    
    # 創建虛擬環境
    python3 -m venv "$VENV_DIR"
    
    # 激活虛擬環境並安裝依賴
    source "$VENV_DIR/bin/activate"
    
    # 檢查是否有 requirements.txt
    if [ -f "$KATAGO_DIR/requirements.txt" ]; then
        echo "Installing dependencies from requirements.txt..."
        pip install -r "$KATAGO_DIR/requirements.txt"
    else
        echo "Installing sgfmill..."
        pip install sgfmill
    fi
    
    echo ""
    echo "Virtual environment created and dependencies installed."
    echo ""
fi

# 激活虛擬環境並運行 analysis.py
source "$VENV_DIR/bin/activate"
exec "$VENV_PY" "$ANALYSIS_PY" "$@"

