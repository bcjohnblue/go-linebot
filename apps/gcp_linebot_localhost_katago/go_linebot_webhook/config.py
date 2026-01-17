import os
from dotenv import load_dotenv

load_dotenv()

config = {
    # LINE Bot
    "line": {
        "channel_access_token": os.getenv("LINE_CHANNEL_ACCESS_TOKEN"),
    },
    # GCP
    "gcp": {
        "project_id": os.getenv("GCP_PROJECT_ID"),
    },
    # GCS Storage
    "gcs": {
        "bucket_name": os.getenv("GCS_BUCKET_NAME"),
    },
    # Server
    "server": {
        "port": int(os.getenv("PORT", "8080")),
        "webhook_path": os.getenv("WEBHOOK_PATH", "/webhook"),
    },
    # Localhost Analysis Service
    "localhost": {
        "analysis_url": os.getenv("LOCALHOST_ANALYSIS_URL"),
    },
    # Cloud Run Callback
    "cloud_run": {
        "callback_analysis_url": os.getenv("CLOUD_RUN_CALLBACK_ANALYSIS_URL"),
    },
    # OpenAI
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    },
}

# Validate required config
required_env_vars = [
    "LINE_CHANNEL_ACCESS_TOKEN",
    "GCP_PROJECT_ID",
    "GCS_BUCKET_NAME",
]

for env_var in required_env_vars:
    if not os.getenv(env_var):
        raise ValueError(f"Missing required environment variable: {env_var}")
