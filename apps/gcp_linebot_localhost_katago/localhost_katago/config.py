import os
from dotenv import load_dotenv

load_dotenv()

config = {
    # GCP
    "gcp": {
        "project_id": os.getenv("GCP_PROJECT_ID"),
    },
    # Storage
    "storage": {
        "bucket_name": os.getenv("GCS_BUCKET_NAME"),
    },
    # Server
    "server": {
        "port": int(os.getenv("PORT", "3000")),
        "webhook_path": os.getenv("WEBHOOK_PATH", "/webhook"),
    },
}

# Validate required config
required_env_vars = [
    "GCP_PROJECT_ID",
    "GCS_BUCKET_NAME",
]

for env_var in required_env_vars:
    if not os.getenv(env_var):
        raise ValueError(f"Missing required environment variable: {env_var}")
