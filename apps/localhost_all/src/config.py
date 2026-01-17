import os
from dotenv import load_dotenv

load_dotenv()

config = {
    # LINE Bot
    "line": {
        "channel_access_token": os.getenv("LINE_CHANNEL_ACCESS_TOKEN"),
    },
    # Server
    "server": {
        "port": int(os.getenv("PORT", "3000")),
        "webhook_path": os.getenv("WEBHOOK_PATH", "/webhook"),
        "public_url": os.getenv("PUBLIC_URL"),
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
]

for env_var in required_env_vars:
    if not os.getenv(env_var):
        raise ValueError(f"Missing required environment variable: {env_var}")
