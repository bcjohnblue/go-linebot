# Modal 1.3.0 更新說明

## 更新內容

已將 Modal SDK 從 `>=0.60.0` 更新到 `>=1.3.0`，並修改相關 API 調用以符合新版本要求。

## 主要變更

### 1. 版本更新

**檔案**: `modal_katago/requirements.txt` 和 `gcp_linebot/requirements.txt`
- 從 `modal>=0.60.0` 更新到 `modal>=1.3.0`

### 2. API 變更：移除 Mount，改用 Image.add_local_*

**檔案**: `modal_katago/main.py`

#### 舊方式（已棄用）：
```python
modal_katago_mount = modal.Mount.from_local_dir(
    current_dir,
    remote_path="/app",
    condition=lambda path: not any(...)
)

@app.function(
    image=image,
    mounts=[modal_katago_mount],
    ...
)
```

#### 新方式（Modal 1.3.0）：
```python
image = (
    modal.Image.debian_slim(...)
    ...
    # 使用 add_local_python_source 添加 Python 模組
    .add_local_python_source(current_dir / "handlers")
    .add_local_python_source(current_dir / "services")
    # 使用 add_local_file 添加單個檔案
    .add_local_file(current_dir / "config.py")
    .add_local_file(current_dir / "logger.py")
    # 使用 add_local_dir 添加目錄
    .add_local_dir(
        current_dir / "katago",
        exclude=[...]
    )
)

@app.function(
    image=image,  # 不再需要 mounts= 參數
    ...
)
```

### 3. GCP 端 API 調用

**檔案**: `gcp_linebot/handlers/line_handler.py`

`App.lookup()` 和 `spawn()` 在 Modal 1.3.0 中仍然有效，無需修改：

```python
app = modal.App.lookup(modal_app_name)
review_function = app[modal_function_name]
review_function.spawn(...)
```

## 變更摘要

| 項目 | 舊版本 | 新版本 (1.3.0) |
|------|--------|----------------|
| SDK 版本 | `>=0.60.0` | `>=1.3.0` |
| 掛載方式 | `Mount.from_local_dir()` | `Image.add_local_dir()` |
| Python 模組 | `Mount` + `mounts=` | `Image.add_local_python_source()` |
| 單個檔案 | `Mount` + `mounts=` | `Image.add_local_file()` |
| Function decorator | `mounts=[...]` | 不需要，已在 image 中定義 |

## 注意事項

1. **Python 版本**: Modal 1.3.0 停止支援 Python 3.9，我們使用的是 Python 3.11，符合要求。

2. **自動掛載**: Modal 1.3.0 移除了自動掛載本地 Python source 的行為，必須明確使用 `add_local_python_source()` 來包含本地模組。

3. **排除檔案**: 使用 `add_local_dir()` 時，可以透過 `exclude` 參數排除不需要的檔案（如 `__pycache__`、`venv`、`results` 等）。

## 測試建議

更新後，建議進行以下測試：

1. 部署 Modal 應用程式：
   ```bash
   cd apps/gcp_linebot_modal_katago/modal_katago
   modal deploy main.py
   ```

2. 檢查函數是否可以正確訪問掛載的檔案：
   - handlers 模組
   - katago 目錄（scripts, configs, packages, models）
   - config.py 和 logger.py

3. 測試完整流程：
   - 透過 LINE Bot 上傳 SGF 檔案
   - 發送「覆盤」指令
   - 確認 Modal 函數可以正確執行並回調 GCP

## 相關文件

- [Modal 1.0 Migration Guide](https://modal.com/docs/guide/modal-1-0-migration)
- [Modal 1.3.0 Changelog](https://modal.com/docs/reference/changelog)

