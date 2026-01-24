"""
Modal application for KataGo analysis.

This module defines the Modal function that runs KataGo analysis on SGF files.
The function downloads SGF from GCS, runs analysis, uploads results, and calls back to GCP.
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
import modal

# Define Modal app
app = modal.App("katago")

# Get the directory containing this file (modal_katago/)
current_dir = Path(__file__).parent

# Create or get Modal Volume for KataGo models
# Volume provides persistent storage for large model files
katago_models_volume = modal.Volume.from_name("katago-models", create_if_missing=True)

# Model directory path in the container
MODEL_DIR = Path("/katago/models")
MODEL_FILENAME = "kata1-b28c512nbt-s12192929536-d5655876072.bin.gz"

# Define image with KataGo dependencies
# Note: KataGo binary needs to be installed separately
# You may need to download and install KataGo binary in the image
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04", add_python="3.11"
    )
    # 1. 使用 .env 設定持久環境變數，這會影響後續所有的步驟
    .env({"DEBIAN_FRONTEND": "noninteractive", "TZ": "Asia/Taipei"})
    # 2. 先手動處理時區軟連結，這能跳過 tzdata 的詢問
    .run_commands("ln -fs /usr/share/zoneinfo/Asia/Taipei /etc/localtime")
    # 3. 執行安裝
    # 需要安裝系統工具、Python 依賴和 KataGo 所需的庫
    .apt_install(
        # 系統工具
        "git",
        "wget",
        "curl",
        "bash",
        "unzip",
        "ca-certificates",
        # Python 相關（add_python="3.11" 已安裝 Python，但需要 pip）
        "python3-pip",
        "python3-dev",
        # KataGo 和 Python 套件所需的庫
        "libopenblas-dev",
        "libopencv-dev",
        "libstdc++6",
        "libgomp1",
        # 其他依賴
        "libzip4",
        "zlib1g",
        "libssl3",
        # TCMalloc (Google's memory allocator, required by KataGo)
        "libtcmalloc-minimal4",
    )
    .run_commands(
        "export DEBIAN_FRONTEND=noninteractive",
        "export TZ=Asia/Taipei",
        # Install unzip for extracting KataGo
        "apt-get update && apt-get install -y unzip",
        # Download and install KataGo
        # KataGo zip contains a katago executable file directly (not AppImage)
        # Download KataGo v1.16.4 CUDA 12.1 version
        "wget -q https://github.com/lightvector/KataGo/releases/download/v1.16.4/katago-v1.16.4-cuda12.1-cudnn8.9.7-linux-x64.zip -O /tmp/katago.zip || true",
        # Extract zip and find the katago executable
        # The zip contains an AppImage file that needs to be extracted
        "if [ -f /tmp/katago.zip ]; then "
        "  unzip -q /tmp/katago.zip -d /tmp/katago_extract && "
        "  KATAGO_APPIMAGE=$(find /tmp/katago_extract \\( -name 'katago*.AppImage' -o -name katago \\) -type f -executable | head -1) && "
        '  if [ -n "$KATAGO_APPIMAGE" ]; then '
        "    echo 'Found KataGo AppImage:' \"$KATAGO_APPIMAGE\" && "
        "    cd /tmp && "
        '    chmod +x "$KATAGO_APPIMAGE" && '
        '    "$KATAGO_APPIMAGE" --appimage-extract >/dev/null 2>&1 && '
        "    KATAGO_BIN=$(find /tmp/squashfs-root/usr/bin -name katago -type f -executable | head -1) && "
        '    if [ -n "$KATAGO_BIN" ]; then '
        '      mv "$KATAGO_BIN" /usr/local/bin/katago && '
        "      chmod +x /usr/local/bin/katago && "
        "      echo 'KataGo installed successfully' && "
        "      /usr/local/bin/katago version || true; "
        "    else "
        "      echo 'ERROR: katago binary not found after AppImage extraction' >&2; exit 1; "
        "    fi && "
        "    rm -rf /tmp/squashfs-root; "
        "  else "
        "    echo 'ERROR: KataGo AppImage not found in zip' >&2; exit 1; "
        "  fi && "
        "  rm -rf /tmp/katago_extract /tmp/katago.zip; "
        "fi",
    )
    .pip_install_from_requirements(str(current_dir / "requirements.txt"))
    # Add local Python source files (handlers, etc.)
    # Note: Using add_local_dir instead of add_local_python_source to avoid "has no spec" warnings
    # Modal requires string paths, not Path objects
    .add_local_dir(str(current_dir / "handlers"), "/app/handlers")
    .add_local_file(str(current_dir / "logger.py"), "/app/logger.py")
    # add_local_file requires remote_path parameter
    # Add katago directory (scripts, configs, packages)
    # Note: models are stored in Volume, not included in image
    # Note: add_local_dir uses 'ignore' parameter (not 'exclude') in Modal 1.3.0
    .add_local_dir(
        str(current_dir / "katago"),
        "/app/katago",
        ignore=[
            "**/__pycache__/**",
            "**/*.pyc",
            "**/venv/**",
            "**/.git/**",
            "**/results/**",
            "**/analysis_logs/**",
            "**/models/**",  # Exclude models directory - loaded from Volume
        ],
    )
)


@app.function(
    image=image,
    gpu="L4",  # KataGo needs GPU
    timeout=600,  # 10 minutes timeout
    memory=4096,  # 4GB memory
    volumes={str(MODEL_DIR): katago_models_volume},  # Mount Volume for models
    secrets=[
        modal.Secret.from_name("gcp-go-linebot"),  # GCP service account key
    ],
    max_containers=1,
)
def review(
    task_id: str,
    sgf_gcs_path: str,
    callback_url: str,
    target_id: str,
    visits: int = 5,
) -> Dict[str, Any]:
    """
    Execute KataGo review analysis on an SGF file.

    Args:
        task_id: Unique task identifier
        sgf_gcs_path: GCS path to SGF file (gs://bucket/path)
        callback_url: URL to callback when analysis completes
        target_id: LINE target ID (user/group/room)
        visits: Number of visits for KataGo analysis (default: 5)

    Returns:
        Dict with status and result information
    """
    import asyncio
    import sys
    from google.cloud import storage
    from google.oauth2 import service_account
    import httpx

    # Initialize logger (simple print-based for Modal)
    def log(message: str, level: str = "INFO"):
        print(f"[{level}] {message}")

    # Debug: Check Python environment
    log("=" * 60)
    log("DEBUG: Python Environment Information")
    log("=" * 60)
    log(f"Python executable: {sys.executable}")
    log(f"Python version: {sys.version}")
    log(f"Python path: {sys.path}")
    log(f"Current working directory: {os.getcwd()}")

    try:
        # Load GCP credentials from Modal secret
        gcp_key_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_JSON")
        if not gcp_key_json:
            raise ValueError("GCP_SERVICE_ACCOUNT_KEY_JSON not found in environment")

        credentials_info = json.loads(gcp_key_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info
        )

        # Initialize GCS client
        project_id = os.environ.get("GCP_PROJECT_ID")
        bucket_name = os.environ.get("GCS_BUCKET_NAME")

        if not project_id or not bucket_name:
            raise ValueError(
                "GCP_PROJECT_ID or GCS_BUCKET_NAME not found in environment"
            )

        storage_client = storage.Client(credentials=credentials, project=project_id)
        bucket = storage_client.bucket(bucket_name)

        # Extract GCS path
        if sgf_gcs_path.startswith("gs://"):
            gcs_path = sgf_gcs_path[5:]  # Remove gs:// prefix
        else:
            gcs_path = sgf_gcs_path

        # Split bucket and path
        parts = gcs_path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS path format: {sgf_gcs_path}")

        path_bucket_name, remote_path = parts
        if path_bucket_name != bucket_name:
            # Use bucket from path if different
            gcs_bucket = storage_client.bucket(path_bucket_name)
        else:
            gcs_bucket = bucket

        log(f"Starting KataGo review for task: {task_id}")
        log(f"SGF GCS path: {sgf_gcs_path}")

        # Create temporary directory for review
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Download SGF file from GCS
            log(f"Downloading SGF file from GCS: {remote_path}")
            blob = gcs_bucket.blob(remote_path)
            sgf_content = blob.download_as_bytes()
            local_sgf_path = temp_path / f"{task_id}.sgf"
            local_sgf_path.write_bytes(sgf_content)
            log(f"Downloaded SGF file to: {local_sgf_path}")

            # Import and run KataGo analysis
            # The handlers module is available via the mount
            os.chdir("/app")

            # Add /app to Python path to ensure imports work
            if "/app" not in sys.path:
                sys.path.insert(0, "/app")

            # Set environment variable for analysis.sh to use the same Python as main function
            # Use the same Python executable that's running this function
            # This ensures chardet and other packages are available
            os.environ["VENV_PY"] = sys.executable
            log(f"Set VENV_PY to: {sys.executable}")

            # Set KataGo model path to use Volume-mounted model
            # Reload volume to ensure we have the latest model
            katago_models_volume.reload()
            model_path = MODEL_DIR / MODEL_FILENAME

            # Debug: List files in MODEL_DIR to help diagnose issues
            if not model_path.exists():
                log(f"Model file not found at {model_path}")
                log(f"Checking contents of {MODEL_DIR}:")
                try:
                    if MODEL_DIR.exists():
                        files = list(MODEL_DIR.iterdir())
                        log(f"Files in {MODEL_DIR}: {[str(f) for f in files]}")
                    else:
                        log(f"Directory {MODEL_DIR} does not exist")
                except Exception as e:
                    log(f"Error listing directory: {e}")

                raise FileNotFoundError(
                    f"Model file {model_path} not found in Volume. "
                    f"Please run 'modal run main.py::upload_model' to upload the model first. "
                    f"Expected path: {model_path}"
                )
            # Set environment variable for analysis.sh to use Volume-mounted model
            os.environ["KATAGO_MODEL"] = str(model_path)
            log(f"Using model from Volume: {model_path}")

            from handlers.katago_handler import run_katago_analysis

            # Execute KataGo review
            log(f"Starting KataGo analysis for task: {task_id}")
            result = asyncio.run(
                run_katago_analysis(str(local_sgf_path), visits=visits)
            )

            if not result.get("success"):
                error_msg = result.get("stderr", "Unknown error")
                log(f"KataGo review failed for task {task_id}: {error_msg}", "ERROR")

                # Notify Cloud Run of failure
                asyncio.run(
                    _notify_callback(
                        callback_url,
                        {
                            "task_id": task_id,
                            "status": "failed",
                            "error": error_msg,
                            "target_id": target_id,
                        },
                    )
                )
                return {"status": "failed", "error": error_msg}

            # Upload review results to GCS
            result_paths = {}

            # Upload JSON file if exists
            if result.get("jsonPath") and os.path.exists(result["jsonPath"]):
                json_remote_path = f"target_{target_id}/reviews/{task_id}.json"
                json_blob = bucket.blob(json_remote_path)
                json_blob.cache_control = "no-cache, max-age=0"
                json_blob.upload_from_filename(result["jsonPath"])
                result_paths["json_gcs_path"] = f"gs://{bucket_name}/{json_remote_path}"
                log(f"Uploaded JSON to: {json_remote_path}")

            # Prepare callback payload
            callback_payload = {
                "task_id": task_id,
                "status": "success",
                "target_id": target_id,
                "result_paths": result_paths,
                "move_stats": result.get("moveStats"),
            }

            # Notify Cloud Run of completion
            log(f"Notifying Cloud Run of completion: {callback_url}")
            asyncio.run(_notify_callback(callback_url, callback_payload))
            log(f"Successfully notified Cloud Run")

            return {"status": "success", "task_id": task_id}

    except Exception as error:
        log(f"Error in review task {task_id}: {error}", "ERROR")
        import traceback

        traceback.print_exc()

        # Try to notify Cloud Run of error
        try:
            asyncio.run(
                _notify_callback(
                    callback_url,
                    {
                        "task_id": task_id,
                        "status": "failed",
                        "error": str(error),
                        "target_id": target_id,
                    },
                )
            )
        except Exception as callback_error:
            log(f"Failed to send error callback: {callback_error}", "ERROR")

        return {"status": "failed", "error": str(error)}


async def _notify_callback(callback_url: str, payload: Dict[str, Any]):
    """Helper function to notify callback URL"""
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(callback_url, json=payload, timeout=600.0)
        response.raise_for_status()
        return response


# Local entrypoint to upload model to Volume
# This runs on your local machine, not in Modal container
@app.local_entrypoint()
def upload_model(force: bool = False):
    """
    Upload KataGo model file to Modal Volume.

    This function runs locally and uploads the model file to Modal Volume.

    Args:
        force: If True, overwrite existing file. If False, skip if file exists.

    Run this once to upload the model:
    modal run main.py::upload_model
    Or to force overwrite:
    modal run main.py::upload_model --force
    """
    # Use absolute path to local model file
    local_model_path = current_dir / "katago" / "models" / MODEL_FILENAME

    # Convert to absolute path for clarity
    local_model_path = local_model_path.resolve()

    if not local_model_path.exists():
        raise FileNotFoundError(
            f"Model file not found at {local_model_path}. "
            f"Please ensure the model file exists at: {local_model_path}"
        )

    # Remote path in Volume
    # When Volume is mounted to /katago/models, the Volume root contents appear at that path
    # So we upload directly to the Volume root with just the filename
    # This way the file will be at /katago/models/{MODEL_FILENAME} in the container
    remote_model_path = MODEL_FILENAME

    print(f"Uploading model to Modal Volume...")
    print(f"   Local path: {local_model_path}")
    print(f"   Volume path: {remote_model_path}")
    print(f"   Volume name: katago-models")
    print(f"   Will be available at: {MODEL_DIR}/{MODEL_FILENAME} in Modal functions")
    if force:
        print(f"   Force mode: Will overwrite if file exists")

    try:
        # Upload model file to Volume
        # According to Modal docs, batch_upload() automatically commits when exiting the context
        # put_file(local_path, remote_path) where remote_path is relative to Volume root
        with katago_models_volume.batch_upload() as batch:
            batch.put_file(str(local_model_path), remote_model_path)
        # Note: batch_upload() context manager automatically commits, no need to call commit() manually

        print(f"✅ Successfully uploaded model to Volume!")
        print(
            f"   Model is now available at: {MODEL_DIR}/{MODEL_FILENAME} in Modal functions"
        )
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg.lower() or "AlreadyExistsError" in str(
            type(e).__name__
        ):
            if force:
                # Try to delete and re-upload
                print(
                    f"⚠️  File already exists. Force mode enabled, attempting to overwrite..."
                )
                # Note: Modal Volume doesn't support direct deletion from local_entrypoint
                # We need to use a different approach - either use CLI or create a function
                print(f"   To overwrite existing file, please use Modal CLI:")
                print(f"   modal volume rm katago-models {remote_model_path}")
                print(f"   Then run this command again.")
                raise FileExistsError(
                    f"File already exists in Volume. To overwrite, first delete it using:\n"
                    f"  modal volume rm katago-models {remote_model_path}\n"
                    f"  Then run this command again."
                )
            else:
                print(f"ℹ️  Model file already exists in Volume at {remote_model_path}")
                print(f"   Skipping upload. Model is ready to use.")
                print(
                    f"   To force overwrite, use: modal run main.py::upload_model --force"
                )
                return
        else:
            # Re-raise other exceptions
            raise


@app.function(
    image=image,
    gpu="L4",  # KataGo needs GPU
    timeout=60,  # 1 minute timeout (faster for single move)
    memory=4096,  # 4GB memory
    volumes={str(MODEL_DIR): katago_models_volume},  # Mount Volume for models
    secrets=[
        modal.Secret.from_name("gcp-go-linebot"),  # GCP service account key
    ],
    max_containers=1,
)
def get_ai_next_move(
    sgf_gcs_path: str,
    callback_url: str,
    target_id: str,
    current_turn: int,
    visits: int = 400,
    reply_token: Optional[str] = None,
    user_board_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get AI's next move from KataGo using GTP mode.

    Args:
        sgf_gcs_path: GCS path to SGF file (gs://bucket/path)
        callback_url: URL to callback when analysis completes
        target_id: LINE target ID (user/group/room)
        current_turn: Current turn (1=black, 2=white)
        visits: Number of visits for KataGo (default: 400)
        reply_token: Reply token from user's move (if available)
        user_board_image_url: User's board image URL (if available)

    Returns:
        Dict with status and result information
    """
    import asyncio
    import sys
    from google.cloud import storage
    from google.oauth2 import service_account
    import httpx
    import tempfile
    from pathlib import Path

    # Initialize logger (simple print-based for Modal)
    def log(message: str, level: str = "INFO"):
        print(f"[{level}] {message}")

    try:
        # Load GCP credentials from Modal secret
        gcp_key_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_JSON")
        if not gcp_key_json:
            raise ValueError("GCP_SERVICE_ACCOUNT_KEY_JSON not found in environment")

        credentials_info = json.loads(gcp_key_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info
        )

        # Initialize GCS client
        project_id = os.environ.get("GCP_PROJECT_ID")
        bucket_name = os.environ.get("GCS_BUCKET_NAME")

        if not project_id or not bucket_name:
            raise ValueError(
                "GCP_PROJECT_ID or GCS_BUCKET_NAME not found in environment"
            )

        storage_client = storage.Client(credentials=credentials, project=project_id)
        bucket = storage_client.bucket(bucket_name)

        # Extract GCS path
        if sgf_gcs_path.startswith("gs://"):
            gcs_path = sgf_gcs_path[5:]  # Remove gs:// prefix
        else:
            gcs_path = sgf_gcs_path

        # Split bucket and path
        parts = gcs_path.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid GCS path format: {sgf_gcs_path}")

        path_bucket_name, remote_path = parts
        if path_bucket_name != bucket_name:
            # Use bucket from path if different
            gcs_bucket = storage_client.bucket(path_bucket_name)
        else:
            gcs_bucket = bucket

        log(f"Starting KataGo GTP for next move: target_id={target_id}, current_turn={current_turn}")
        log(f"SGF GCS path: {sgf_gcs_path}")

        # Create temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Download SGF file from GCS
            log(f"Downloading SGF file from GCS: {remote_path}")
            blob = gcs_bucket.blob(remote_path)
            sgf_content = blob.download_as_bytes()
            local_sgf_path = temp_path / "game.sgf"
            local_sgf_path.write_bytes(sgf_content)
            log(f"Downloaded SGF file to: {local_sgf_path}")

            # Set up environment
            os.chdir("/app")
            if "/app" not in sys.path:
                sys.path.insert(0, "/app")

            # Set KataGo model path
            katago_models_volume.reload()
            model_path = MODEL_DIR / MODEL_FILENAME

            if not model_path.exists():
                log(f"Model file not found at {model_path}", "ERROR")
                raise FileNotFoundError(
                    f"Model file {model_path} not found in Volume. "
                    f"Please run 'modal run main.py::upload_model' to upload the model first."
                )

            os.environ["KATAGO_MODEL"] = str(model_path)
            log(f"Using model from Volume: {model_path}")

            from handlers.katago_handler import run_katago_gtp_next_move

            # Execute KataGo GTP to get next move
            log(f"Starting KataGo GTP for next move")
            result = asyncio.run(
                run_katago_gtp_next_move(
                    str(local_sgf_path),
                    current_turn=current_turn,
                    visits=visits,
                )
            )

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                log(f"KataGo GTP failed: {error_msg}", "ERROR")

                # Notify Cloud Run of failure
                asyncio.run(
                    _notify_callback(
                        callback_url,
                        {
                            "status": "failed",
                            "error": error_msg,
                            "target_id": target_id,
                            "reply_token": reply_token,  # Pass reply_token even on failure
                            "user_board_image_url": user_board_image_url,  # Pass user's board image URL
                        },
                    )
                )
                return {"status": "failed", "error": error_msg}

            # Get the move
            move = result.get("move")
            if not move:
                error_msg = "No move returned from KataGo"
                log(f"KataGo GTP error: {error_msg}", "ERROR")
                asyncio.run(
                    _notify_callback(
                        callback_url,
                        {
                            "status": "failed",
                            "error": error_msg,
                            "target_id": target_id,
                            "reply_token": reply_token,  # Pass reply_token even on failure
                            "user_board_image_url": user_board_image_url,  # Pass user's board image URL
                        },
                    )
                )
                return {"status": "failed", "error": error_msg}

            # Prepare callback payload
            callback_payload = {
                "status": "success",
                "target_id": target_id,
                "move": move,
                "current_turn": current_turn,
                "reply_token": reply_token,  # Pass reply_token to callback
                "user_board_image_url": user_board_image_url,  # Pass user's board image URL
            }

            # Notify Cloud Run of completion
            log(f"Notifying Cloud Run of completion: {callback_url}")
            asyncio.run(_notify_callback(callback_url, callback_payload))
            log(f"Successfully notified Cloud Run")

            return {"status": "success", "move": move}

    except Exception as error:
        log(f"Error in get_ai_next_move: {error}", "ERROR")
        import traceback
        traceback.print_exc()

        # Try to notify Cloud Run of error
        try:
            asyncio.run(
                _notify_callback(
                    callback_url,
                    {
                        "status": "failed",
                        "error": str(error),
                        "target_id": target_id,
                        "reply_token": reply_token,  # Pass reply_token even on error
                        "user_board_image_url": user_board_image_url,  # Pass user's board image URL
                    },
                )
            )
        except Exception as callback_error:
            log(f"Failed to send error callback: {callback_error}", "ERROR")

        return {"status": "failed", "error": str(error)}


# For local testing
if __name__ == "__main__":
    with app.run():
        # Test the function locally
        pass
