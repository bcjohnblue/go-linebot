import os
from dotenv import load_dotenv

load_dotenv()

config = {
    # LINE Bot
    "line": {
        "channel_access_token": os.getenv("LINE_CHANNEL_ACCESS_TOKEN"),
        "channel_secret": os.getenv("LINE_CHANNEL_SECRET"),
    },
    # GCP
    "gcp": {
        "project_id": os.getenv("GCP_PROJECT_ID"),
        "zone": os.getenv("GCP_ZONE", "asia-east1-a"),
        "service_account_key_path": os.getenv("GCP_SERVICE_ACCOUNT_KEY_PATH"),
    },
    # VM Configuration
    "vm": {
        "instance_name_prefix": os.getenv("VM_INSTANCE_NAME_PREFIX", "katago-worker"),
        "machine_type": os.getenv("VM_MACHINE_TYPE", "n1-standard-2"),
        "image_project": os.getenv("VM_IMAGE_PROJECT", "ubuntu-os-cloud"),
        "image_family": os.getenv("VM_IMAGE_FAMILY", "ubuntu-2204-lts"),
        "disk_size_gb": int(os.getenv("VM_DISK_SIZE_GB", "20")),
        "preemptible": True,  # Always use preemptible
    },
    # Storage
    "storage": {
        "bucket_name": os.getenv("GCS_BUCKET_NAME"),
    },
    # KataGo
    "katago": {
        "script_path": os.getenv("KATAGO_SCRIPT_PATH", "/home/ubuntu/analyze.sh"),
        "result_path": os.getenv("KATAGO_RESULT_PATH", "/home/ubuntu/result.txt"),
    },
    # Server
    "server": {
        "port": int(os.getenv("PORT", "3000")),
        "webhook_path": os.getenv("WEBHOOK_PATH", "/webhook"),
        "public_url": os.getenv("PUBLIC_URL"),
    },
    # Minimax
    "minimax": {
        "api_key": os.getenv("MINIMAX_API_KEY"),
        "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1"),
    },
    # OpenAI
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL"),
    },
}

# Validate required config
required_env_vars = [
    "LINE_CHANNEL_ACCESS_TOKEN",
    # "LINE_CHANNEL_SECRET",  # Optional
    "GCP_PROJECT_ID",
    "GCS_BUCKET_NAME",
]

for env_var in required_env_vars:
    if not os.getenv(env_var):
        raise ValueError(f"Missing required environment variable: {env_var}")

