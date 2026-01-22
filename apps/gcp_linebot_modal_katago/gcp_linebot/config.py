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
    # Modal
    "modal": {
        "app_name": os.getenv("MODAL_APP_NAME", "katago"),
        "function_review": os.getenv("MODAL_FUNCTION_REVIEW", "review"),
        "visits": int(os.getenv("KATAGO_VISITS", "5")),
    },
    # Cloud Run Callback
    "cloud_run": {
        "callback_review_url": os.getenv("CLOUD_RUN_CALLBACK_REVIEW_URL"),
    },
    # OpenAI
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    },
    # Auth
    "auth": {
        "token": os.getenv("AUTH_TOKEN"),
        "bucket_name": os.getenv("AUTH_BUCKET_NAME", "go-linebot-auth"),
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
