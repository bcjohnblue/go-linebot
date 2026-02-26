import os
import re
import json
import time
import random
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
)
from linebot.v3.messaging.models import (
    TextMessage,
    ImageMessage,
    FlexMessage,
    FlexContainer,
)
from linebot.v3.messaging.exceptions import ApiException
from sgfmill import sgf

from config import config
from logger import logger

from handlers.go_engine import GoBoard
from handlers.board_visualizer import BoardVisualizer

# Initialize LINE Bot API v3
configuration = Configuration(access_token=config["line"]["channel_access_token"])
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
blob_api = MessagingApiBlob(api_client)


# Initialize board visualizer (shared instance)
visualizer = BoardVisualizer()

DEFAULT_REVIEW_SELECTION_METRIC = "winrate"
REVIEW_SELECTION_METRICS = {"winrate", "score_loss"}

# ============================================================================
# State persistence functions (GCS-based, for Cloud Run stateless instances)
# ============================================================================


async def save_state_to_gcs(target_id: str, state_data: Dict[str, Any]) -> bool:
    """Save game state to GCS with no-cache to prevent caching issues"""
    try:
        from services.storage import upload_buffer
        import json

        remote_path = f"target_{target_id}/state/game_state.json"
        state_json = json.dumps(state_data, default=str).encode("utf-8")
        logger.info(f"save_state_to_gcs: state_json = {state_json.decode('utf-8')}")

        # 設定快取控制：no-store 確保每次都要回源伺服器檢查
        # 這樣可以避免公開 URL 的快取問題
        await upload_buffer(
            state_json,
            remote_path,
            content_type="application/json",
            cache_control="no-store",
        )
        logger.debug(f"Saved game state for {target_id} to GCS (with no-cache)")
        return True
    except Exception as error:
        logger.error(
            f"Failed to save state to GCS for {target_id}: {error}", exc_info=True
        )
        return False


async def load_state_from_gcs(target_id: str) -> Optional[Dict[str, Any]]:
    """Load game state from GCS using SDK (bypasses public cache)"""
    try:
        from services.storage import download_file_as_text, file_exists
        import json

        remote_path = f"target_{target_id}/state/game_state.json"
        if not await file_exists(remote_path):
            return None

        # 使用 SDK 讀取會直接繞過公開快取層，保證拿到最新版
        state_text = await download_file_as_text(remote_path)
        state_data = json.loads(state_text)
        logger.debug(f"Loaded game state for {target_id} from GCS: {state_data}")
        return state_data
    except Exception as error:
        logger.error(
            f"Failed to load state from GCS for {target_id}: {error}", exc_info=True
        )
        return None


async def save_review_selection_metric(target_id: str, metric: str) -> bool:
    """Save review move-selection metric to GCS."""
    try:
        normalized = (metric or "").strip().lower()
        if normalized not in REVIEW_SELECTION_METRICS:
            return False

        from services.storage import upload_buffer

        remote_path = f"target_{target_id}/state/review_setting.json"
        payload = json.dumps({"selection_metric": normalized}).encode("utf-8")
        await upload_buffer(
            payload,
            remote_path,
            content_type="application/json",
            cache_control="no-store",
        )
        logger.info(f"Saved review selection metric for {target_id}: {normalized}")
        return True
    except Exception as error:
        logger.error(
            f"Failed to save review selection metric for {target_id}: {error}",
            exc_info=True,
        )
        return False


async def get_review_selection_metric(target_id: str) -> str:
    """Load review move-selection metric from GCS (default: winrate)."""
    try:
        from services.storage import download_file_as_text, file_exists

        remote_path = f"target_{target_id}/state/review_setting.json"
        if not await file_exists(remote_path):
            return DEFAULT_REVIEW_SELECTION_METRIC

        setting_text = await download_file_as_text(remote_path)
        setting_data = json.loads(setting_text)
        metric = str(setting_data.get("selection_metric", "")).strip().lower()
        if metric in REVIEW_SELECTION_METRICS:
            return metric
        return DEFAULT_REVIEW_SELECTION_METRIC
    except Exception as error:
        logger.error(
            f"Failed to load review selection metric for {target_id}: {error}",
            exc_info=True,
        )
        return DEFAULT_REVIEW_SELECTION_METRIC


async def save_sgf_file_path(target_id: str, sgf_path: str, file_name: str) -> bool:
    """Save SGF file path to GCS"""
    try:
        from services.storage import upload_buffer
        import json

        remote_path = f"target_{target_id}/state/sgf_file_path.json"
        data = {"sgf_path": sgf_path, "file_name": file_name}
        data_json = json.dumps(data).encode("utf-8")
        await upload_buffer(data_json, remote_path)
        logger.debug(f"Saved SGF file path for {target_id} to GCS")
        return True
    except Exception as error:
        logger.error(
            f"Failed to save SGF file path to GCS for {target_id}: {error}",
            exc_info=True,
        )
        return False


async def load_sgf_file_path(target_id: str) -> Optional[Dict[str, str]]:
    """Load SGF file path from GCS"""
    try:
        from services.storage import download_file, file_exists
        import json

        remote_path = f"target_{target_id}/state/sgf_file_path.json"
        if not await file_exists(remote_path):
            return None

        data_bytes = await download_file(remote_path)
        data = json.loads(data_bytes.decode("utf-8"))
        logger.debug(f"Loaded SGF file path for {target_id} from GCS")
        return data
    except Exception as error:
        logger.error(
            f"Failed to load SGF file path from GCS for {target_id}: {error}",
            exc_info=True,
        )
        return None


# Bot info cache
_bot_display_name: Optional[str] = None

# Get Bot's own User ID
async def get_bot_user_id() -> Optional[str]:
    """Get bot user ID directly from LINE API"""
    try:
        bot_info = await asyncio.to_thread(line_bot_api.get_bot_info)
        bot_user_id = bot_info.user_id
        logger.debug(f"Bot User ID: {bot_user_id}")
        return bot_user_id
    except Exception as error:
        logger.error(f"Failed to get bot info: {error}", exc_info=True)
        return None


async def get_bot_display_name() -> Optional[str]:
    """Get bot display name directly from LINE API (cached)"""
    global _bot_display_name
    if _bot_display_name is not None:
        return _bot_display_name
    
    try:
        bot_info = await asyncio.to_thread(line_bot_api.get_bot_info)
        _bot_display_name = bot_info.display_name
        logger.debug(f"Bot Display Name: {_bot_display_name}")
        return _bot_display_name
    except Exception as error:
        logger.error(f"Failed to get bot info: {error}", exc_info=True)
        return None


def is_valid_https_url(url: str) -> bool:
    """Validate if URL is a valid HTTPS URL"""
    if not url or not isinstance(url, str):
        return False

    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.scheme == "https"
    except Exception:
        return False


def encode_url_path(path: str) -> str:
    """Encode URL path (preserve slashes, encode other special characters)"""
    from urllib.parse import quote

    return "/".join(quote(part, safe="") for part in path.split("/"))


def create_video_preview_bubble(
    move_number: int,
    color: str,
    played: str,
    comment: str,
    gif_url: str,
    winrate_before: Optional[float] = None,
    winrate_after: Optional[float] = None,
    score_loss: Optional[float] = None,
) -> Dict[str, Any]:
    """Create single Bubble content (for Carousel)"""
    color_text = "黑" if color == "B" else "白"

    # Limit comment length (LINE Flex Message has character limit)
    max_comment_length = 500
    truncated_comment = (
        comment[:max_comment_length] + "..."
        if len(comment) > max_comment_length
        else comment
    )

    # Build body contents
    body_contents = [
        {
            "type": "text",
            "text": f"📍 第 {move_number} 手（{color_text}）",
            "weight": "bold",
            "size": "lg",
            "color": "#1DB446",
        },
        {
            "type": "text",
            "text": f"落子位置：{played}",
            "size": "sm",
            "color": "#666666",
            "margin": "md",
        },
    ]

    # Add winrate change if available
    if winrate_before is not None and winrate_after is not None:
        winrate_diff = winrate_before - winrate_after
        winrate_text = f"勝率變化：{winrate_before:.1f}% → {winrate_after:.1f}%"
        if winrate_diff > 0:
            winrate_text += f" (↓{winrate_diff:.1f}%)"
        else:
            winrate_text += f" (↑{abs(winrate_diff):.1f}%)"

        body_contents.append(
            {
                "type": "text",
                "text": winrate_text,
                "size": "sm",
                "color": "#FF6B6B" if winrate_diff > 0 else "#4ECDC4",
                "margin": "sm",
            }
        )

    # Add score loss if available
    if score_loss is not None:
        body_contents.append(
            {
                "type": "text",
                "text": f"目差損失：{score_loss:.1f} 目",
                "size": "sm",
                "color": "#FF6B6B",
                "margin": "sm",
            }
        )

    body_contents.append({"type": "separator", "margin": "md"})
    body_contents.append(
        {
            "type": "text",
            "text": truncated_comment,
            "wrap": True,
            "size": "sm",
            "margin": "md",
            "color": "#333333",
        }
    )

    return {
        "type": "bubble",
        "hero": {
            "type": "image",
            "url": gif_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {"type": "uri", "uri": gif_url, "label": "觀看動畫"},
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "🎬 觀看動態棋譜",
                        "uri": gif_url,
                    },
                    "color": "#1DB446",
                }
            ],
        },
    }


def create_carousel_flex_message(
    bubbles: List[Dict[str, Any]], start_index: int = 1, total_count: int = None
) -> Dict[str, Any]:
    """Create Carousel Flex Message (combine multiple bubbles)"""
    if total_count is None:
        total_count = len(bubbles)

    return {
        "type": "flex",
        "altText": f"關鍵手數分析（{start_index}-{start_index + len(bubbles) - 1}/{total_count}）",
        "contents": {"type": "carousel", "contents": bubbles},
    }


def create_sgf_file_flex_message(file_url: str, game_id: str) -> FlexMessage:
    """Create Flex Message for SGF file download"""
    import json

    flex_contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "📄 當前棋譜檔案",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#1DB446",
                },
                {
                    "type": "text",
                    "text": f"Game ID: {game_id}",
                    "size": "sm",
                    "color": "#666666",
                    "margin": "md",
                },
                {
                    "type": "separator",
                    "margin": "md",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "📥 下載棋譜檔案",
                        "uri": file_url,
                    },
                    "color": "#1DB446",
                },
            ],
        },
    }

    flex_container = FlexContainer.from_json(json.dumps(flex_contents))
    return FlexMessage(
        alt_text="當前棋譜檔案",
        contents=flex_container,
    )


HELP_MESSAGE = """歡迎使用圍棋 Line Bot！

📋 指令列表：
• help / 幫助 / 說明 - 顯示此說明

🎮 對局功能：
• 輸入座標（如 D4, Q16）- 落子並顯示棋盤
• 悔棋 / undo - 撤銷上一步
• 悔棋 10 / undo 10 - 撤銷指定手數
• 讀取 / load - 從存檔恢復當前遊戲
• 讀取 game_1234567890 / load game_1234567890 - 讀取指定 game_id 的棋譜
• 讀取 game_1234567890 10 / load game_1234567890 10 - 讀取指定 game_id 的前 N 手，並創建新對局
• 重置 / reset - 重置棋盤，開始新遊戲（會保存當前棋譜）
• 投子 - 認輸並結束本局（會先顯示勝負，再重置棋盤）
• 形勢 / 形式 / evaluation - 顯示當前盤面領地分布與目數差距

🔐 認證功能：
• auth <token> / 認證 <token> - 進行認證以使用 AI 對弈、覆盤與形勢判斷功能

🤖 AI 對弈功能：
• 對弈 / vs - 查看目前對弈模式狀態
• 對弈 ai / vs ai - 開啟 AI 對弈模式（與 AI 對戰）
• 對弈 free / vs free - 關閉 AI 對弈模式（恢復一般對弈模式）

📊 覆盤分析功能：
• 覆盤 / review - 對最新上傳的棋譜執行 KataGo 覆盤分析
• review setting - 顯示目前關鍵手數挑選依據
• review setting winrate - 以勝率落差挑選前 20 手
• review setting score_loss - 以目差損失挑選前 20 手

覆盤使用流程：
1️⃣ 上傳 SGF 棋譜檔案
2️⃣ 輸入「覆盤」開始分析
3️⃣ 等待約 10 分鐘獲得分析結果

覆盤分析結果包含：
• 🗺️ 全盤手順圖 - 顯示整局棋的所有手順
• 📈 勝率變化圖 - 顯示黑方勝率隨手數的變化曲線
• 🎬 關鍵手數 GIF 動畫 - 依設定（勝率落差或目差損失）挑選前 20 手動態演示
• 💬 ChatGPT 評論 - 針對關鍵手數的評論

技術規格：
• 分析引擎：KataGo AI（visits=1000）
• 分析時間：KataGo 全盤分析約 6 分鐘
• 評論生成：ChatGPT 評論生成約 3 分鐘
• 動畫繪製：GIF 動畫繪製約 10 秒

注意事項：
• 覆盤功能每次消耗 4 個推播訊息 × 群組人數，每月訊息上限為 200 則，請注意使用頻率，超出上限將無法使用覆盤功能"""


async def save_sgf_file(
    file_buffer: bytes, original_file_name: str, target_id: str = None
) -> Dict[str, str]:
    """Save SGF file to GCS
    If target_id is provided, save to target_{target_id}/reviews/ folder
    Otherwise, save to sgf/ folder (for backward compatibility)
    """
    from services.storage import upload_buffer
    import time

    # Generate unique path for SGF file
    timestamp = int(time.time())
    if target_id:
        # Save to reviews folder for review processing
        remote_path = f"target_{target_id}/reviews/{original_file_name}_{timestamp}.sgf"

    # Upload to GCS
    gcs_path = await upload_buffer(file_buffer, remote_path)

    return {
        "fileName": original_file_name,
        "filePath": gcs_path,
        "remotePath": remote_path,
    }


async def send_message(
    target_id: str, reply_token: Optional[str], messages: List[Any]
) -> bool:
    """Send message (prefer replyMessage to reduce usage, fallback to pushMessage if replyToken expired)"""
    # If there's a replyToken, try to use replyMessage
    if reply_token:
        try:
            # Run synchronous call in thread pool
            request = ReplyMessageRequest(reply_token=reply_token, messages=messages)
            await asyncio.to_thread(line_bot_api.reply_message, request)
            logger.info(f"Sent reply message to {target_id} (message count: {len(messages)})")
            return True  # Successfully used replyMessage
        except ApiException as e:
            # replyToken may have expired, fallback to pushMessage
            if e.status in [400, 410]:
                logger.warning(f"replyToken expired or invalid for {target_id}, using pushMessage instead")
            else:
                logger.error(f"Error sending reply message to {target_id}: {e}", exc_info=True)
                raise

    # Use pushMessage
    request = PushMessageRequest(to=target_id, messages=messages)
    await asyncio.to_thread(line_bot_api.push_message, request)
    logger.info(f"Sent push message to {target_id} (message count: {len(messages)})")
    return False  # Used pushMessage


async def handle_auth_command(target_id: str, reply_token: Optional[str], token: str):
    """Handle auth command - Verify token and save to auth bucket"""
    try:
        from services.storage import save_auth_token
        
        # Get AUTH_TOKEN from config
        auth_token = config.get("auth", {}).get("token")
        
        if not auth_token:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 系統配置錯誤：未設定 AUTH_TOKEN")],
            )
            return
        
        # Compare user input token with secret token
        if token.strip() != auth_token:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 認證失敗：金鑰不正確")],
            )
            return
        
        # Save token to auth bucket
        success = await save_auth_token(target_id, token.strip())
        
        if success:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="✅ 認證成功！現在可以使用覆盤功能。")],
            )
        else:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 認證失敗：無法儲存認證資訊")],
            )
    except Exception as error:
        logger.error(f"Error in auth command: {error}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 執行認證時發生錯誤：{str(error)}")],
        )


async def handle_review_command(target_id: str, reply_token: Optional[str]):
    """Handle review command - Call Modal function for review"""
    import uuid
    import modal

    used_reply_token = False

    try:
        # Check authentication only if AUTH_TOKEN is configured
        auth_token = config.get("auth", {}).get("token")
        if auth_token:
            # AUTH_TOKEN is configured, require authentication
            from services.storage import check_auth
            
            # Verify authentication
            is_authenticated = await check_auth(target_id, auth_token)
            if not is_authenticated:
                used_reply_token = await send_message(
                    target_id,
                    reply_token,
                    [TextMessage(text="❌ 請先使用 'auth <token>' 指令進行認證，才可使用覆盤功能")],
                )
                return
        
        # Get latest SGF file from reviews folder
        from services.storage import list_files, storage_client, bucket

        reviews_prefix = f"target_{target_id}/reviews/"
        all_files = await list_files(reviews_prefix)

        # Filter only SGF files
        sgf_files = [f for f in all_files if f.lower().endswith(".sgf")]

        if not sgf_files:
            used_reply_token = await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 找不到棋譜，請先上傳棋譜。")],
            )
            return

        # Get the latest SGF file by time created
        def get_latest_sgf():
            sgf_blobs = [bucket.blob(f) for f in sgf_files]
            # Reload to get time_created metadata
            for blob in sgf_blobs:
                blob.reload()
            # Sort by time created (newest first) and get the latest
            latest_blob = max(sgf_blobs, key=lambda b: b.time_created)
            return latest_blob.name

        latest_sgf_path = await asyncio.to_thread(get_latest_sgf)

        # Ensure it's a GCS path
        if not latest_sgf_path.startswith("gs://"):
            sgf_gcs_path = f"gs://{config['gcs']['bucket_name']}/{latest_sgf_path}"
        else:
            sgf_gcs_path = latest_sgf_path

        # Extract timestamp from latest_sgf_path as task_id
        # Path format: target_{target_id}/reviews/filename_timestamp.sgf
        # Extract timestamp from the filename
        filename = os.path.basename(latest_sgf_path)
        # Match pattern: name_timestamp.sgf where timestamp is digits
        timestamp_match = re.search(r"_(\d+)\.sgf$", filename)
        if timestamp_match:
            task_id = timestamp_match.group(1)
        else:
            # Fallback to UUID if timestamp not found
            task_id = str(uuid.uuid4())
            logger.warning(
                f"Could not extract timestamp from {latest_sgf_path}, using UUID: {task_id}"
            )

        # Get Modal app name and callback URL from config
        modal_app_name = config.get("modal", {}).get("app_name")
        modal_function_review = config.get("modal", {}).get("function_review")
        callback_review_url = config.get("cloud_run", {}).get("callback_review_url")

        if not modal_app_name or not modal_function_review:
            logger.error("MODAL_APP_NAME or MODAL_FUNCTION_REVIEW not configured")
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 系統配置錯誤：未設定 Modal 應用程式名稱")],
            )
            return

        if not callback_review_url:
            logger.error("CLOUD_RUN_CALLBACK_REVIEW_URL not configured")
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 系統配置錯誤：未設定回調 URL")],
            )
            return

        selection_metric = await get_review_selection_metric(target_id)
        selection_metric_text = (
            "勝率落差" if selection_metric == "winrate" else "目差損失"
        )

        # Notify start of review (use replyMessage if available)
        sgf_file_name = os.path.basename(sgf_gcs_path)
        # Only process SGF files, ignore other file types (e.g., JSON files)
        if sgf_file_name.lower().endswith(".sgf"):
            # Remove timestamp from filename (format: name_timestamp.sgf -> name.sgf)
            # Match pattern: name_timestamp.sgf where timestamp is digits
            sgf_file_name = re.sub(r"_(\d+)\.sgf$", r".sgf", sgf_file_name)
            # Remove .sgf extension for display
            sgf_file_name = sgf_file_name[:-4]
        else:
            # If not SGF file, use filename as-is (should not happen, but handle gracefully)
            logger.warning(f"Expected SGF file but got: {sgf_file_name}")
        used_reply_token = await send_message(
            target_id,
            reply_token,
            [
                TextMessage(
                    text=(
                        f"✅ 開始對棋譜：{sgf_file_name} 進行覆盤分析（關鍵手數依據：{selection_metric_text}），"
                        "完成大約需要 10 分鐘...，請稍後再回來查看分析結果。"
                    )
                )
            ],
        )

        # After using replyToken, set to None, subsequent messages use pushMessage
        if used_reply_token:
            reply_token = None

        # Call Modal function for review
        logger.info(f"Calling Modal function: {modal_app_name}.{modal_function_review}")
        try:
            review_function = modal.Function.from_name(
                modal_app_name, modal_function_review
            )

            # Spawn the function asynchronously (non-blocking)
            # This will trigger the Modal function to run in the background
            # visits = config.get("modal", {}).get("visits", 1000)
            review_function.spawn(
                task_id=task_id,
                sgf_gcs_path=sgf_gcs_path,
                callback_url=callback_review_url,
                target_id=target_id,
                # visits=visits,
            )
            logger.info(f"Successfully spawned Modal function for task: {task_id}")

        except Exception as modal_error:
            logger.error(f"Error calling Modal function: {modal_error}", exc_info=True)
            await send_message(
                target_id,
                None,
                [TextMessage(text=f"❌ 調用 Modal 函數時發生錯誤：{str(modal_error)}")],
            )
            return

        # Review will continue asynchronously via callback
        # No need to wait here
    except Exception as error:
        logger.error(f"Error in 覆盤 command: {error}", exc_info=True)
        await send_message(
            target_id,
            None,
            [TextMessage(text=f"❌ 執行覆盤時發生錯誤：{str(error)}")],
        )


async def handle_review_setting_command(
    target_id: str, reply_token: Optional[str], metric: Optional[str] = None
):
    """Handle review setting command."""
    try:
        if not metric:
            current_metric = await get_review_selection_metric(target_id)
            current_text = "勝率落差" if current_metric == "winrate" else "目差損失"
            await send_message(
                target_id,
                reply_token,
                [
                    TextMessage(
                        text=(
                            "📊 覆盤關鍵手數挑選設定\n"
                            f"目前：{current_text}（{current_metric}）\n\n"
                            "可用指令：\n"
                            "• review setting winrate\n"
                            "• review setting score_loss"
                        )
                    )
                ],
            )
            return

        normalized = metric.strip().lower()
        if normalized not in REVIEW_SELECTION_METRICS:
            await send_message(
                target_id,
                reply_token,
                [
                    TextMessage(
                        text=(
                            "❌ 覆盤設定無效，請使用：\n"
                            "• review setting winrate\n"
                            "• review setting score_loss"
                        )
                    )
                ],
            )
            return

        success = await save_review_selection_metric(target_id, normalized)
        if not success:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 儲存覆盤設定失敗，請稍後再試。")],
            )
            return

        metric_text = "勝率落差" if normalized == "winrate" else "目差損失"
        await send_message(
            target_id,
            reply_token,
            [
                TextMessage(
                    text=f"✅ 已更新覆盤設定：{metric_text}（{normalized}）。\n後續 review 將依此挑選前 20 手。"
                )
            ],
        )
    except Exception as error:
        logger.error(f"Error in review setting command: {error}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 設定覆盤參數時發生錯誤：{str(error)}")],
        )


async def handle_guess_first_command(target_id: str, reply_token: Optional[str], player1: str, player2: str):
    """Handle guess first (Nigiri) command"""
    try:
        # Player 1 rolls 1 (Odd) or 2 (Even)
        p1_roll = random.choice([1, 2])
        # Player 2 rolls 1 to 20
        p2_roll = random.randint(1, 20)

        p1_guess_odd = (p1_roll == 1)
        p2_is_odd = (p2_roll % 2 != 0)

        # Compare parity
        # If P1 guessed correctly (same parity), P1 takes Black
        if p1_guess_odd == p2_is_odd:
            p1_color = "黑"
            p2_color = "白"
        else:
            p1_color = "白"
            p2_color = "黑"

        p1_items = "1" if p1_roll == 1 else "2"
        
        message_text = (
            f"{player1}抓{p1_items}顆，{player2}抓{p2_roll}顆，"
            f"{player1}執{p1_color}，{player2}執{p2_color}。"
        )

        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=message_text)]
        )
    except Exception as e:
        logger.error(f"Error in handle_guess_first_command: {e}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 猜先功能發生錯誤：{str(e)}")]
        )


async def handle_evaluation_command(target_id: str, reply_token: Optional[str]):
    """Handle shape evaluation command (形勢判斷 / evaluation)"""
    import modal
    import tempfile
    from pathlib import Path

    try:
        # Check authentication only if AUTH_TOKEN is configured
        auth_token = config.get("auth", {}).get("token")
        if auth_token:
            from services.storage import check_auth

            is_authenticated = await check_auth(target_id, auth_token)
            if not is_authenticated:
                await send_message(
                    target_id,
                    reply_token,
                    [TextMessage(text="❌ 請先使用 'auth <token>' 指令進行認證，才可使用形勢判斷功能")],
                )
                return

        state = await get_game_state(target_id)
        game = state["game"]
        current_turn = state.get("current_turn", 1)
        sgf_game = state["sgf_game"]

        # 檢查是否有任何落子
        has_stone = any(
            stone != 0 for row in game.board for stone in row
        )
        if not has_stone:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="目前盤面沒有進行中的對局，無法進行形勢判斷。")],
            )
            return

        # 確保 SGF 已保存
        sgf_gcs_path = await save_game_sgf(target_id, state)
        if not sgf_gcs_path:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 無法儲存目前棋局 SGF，請稍後再試。")],
            )
            return

        # Get Modal app name and function name from config
        modal_app_name = config.get("modal", {}).get("app_name")
        modal_function_evaluation = config.get("modal", {}).get("function_evaluation", "evaluation")

        if not modal_app_name:
            logger.error("MODAL_APP_NAME not configured")
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 系統配置錯誤：未設定 Modal 應用程式名稱")],
            )
            return

        # Call Modal function synchronously (wait for result)
        logger.info(f"Calling Modal function: {modal_app_name}.{modal_function_evaluation}")
        try:
            evaluation_function = modal.Function.from_name(
                modal_app_name, modal_function_evaluation
            )

            # Call the function synchronously (blocking)
            # visits = config.get("modal", {}).get("visits", 1000)
            result = evaluation_function.remote(
                sgf_gcs_path=sgf_gcs_path,
                current_turn=current_turn,
                # visits=visits,
            )
            logger.info(f"Successfully received evaluation result")

        except Exception as modal_error:
            logger.error(f"Error calling Modal function: {modal_error}", exc_info=True)
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text=f"❌ 調用 Modal 函數時發生錯誤：{str(modal_error)}")],
            )
            return

        if not result.get("success"):
            error = result.get("error", "Unknown error")
            logger.error(f"KataGo evaluation failed: {error}")
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text=f"❌ 形勢判斷失敗：{error}")],
            )
            return

        territory = result.get("territory")
        score_lead = result.get("scoreLead")

        # 組形勢文字
        if score_lead is None:
            shape_text = "目前無法可靠判斷形勢。"
        else:
            try:
                score_lead_val = float(score_lead)
            except (TypeError, ValueError):
                score_lead_val = 0.0

            if abs(score_lead_val) < 0.05:
                shape_text = "目前形勢：雙方大致均勢（約 0 目）。"
            else:
                # score_lead 一律為黑棋領先的目數（正=黑領先，負=白領先）
                if score_lead_val > 0:
                    leader = "黑"
                    lead = score_lead_val
                else:
                    leader = "白"
                    lead = -score_lead_val
                lead_rounded = round(lead * 2) / 2.0
                shape_text = f"目前形勢：{leader} +{lead_rounded:.1f} 目。"

        # 從 SGF 找最後一手座標，保持 last move 高亮
        last_coords = None
        sequence = sgf_game.get_main_sequence()
        for node in sequence:
            color, move = node.get_move()
            if move is not None:
                sgf_r, sgf_c = move
                r = 18 - sgf_r
                c = sgf_c
                last_coords = (r, c)

        # Draw board with territory overlay
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            filename = f"evaluation_{int(time.time())}.png"
            output_path = temp_path / filename

            visualizer.draw_board(
                game.board,
                last_move=last_coords,
                output_filename=str(output_path),
                territory=territory,
            )

            # Upload image to GCS
            from services.storage import upload_buffer, get_public_url
            game_id = await get_game_id(target_id)
            remote_path = f"target_{target_id}/boards/{game_id}/{filename}"
            with open(output_path, "rb") as f:
                image_bytes = f.read()
            await upload_buffer(
                image_bytes,
                remote_path,
                content_type="image/png",
                cache_control="no-cache, max-age=0",
            )
            image_url = get_public_url(remote_path)
            if is_valid_https_url(image_url):
                messages = [
                    TextMessage(text=shape_text),
                    TextMessage(text="下圖勢力範圍僅供參考"),
                    ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    ),
                ]
                await send_message(target_id, reply_token, messages)
                return
            logger.warning(f"Invalid image URL: {image_url}")

        # Fallback: text only
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=shape_text + "\n\n⚠️ 無法顯示棋盤圖片，請檢查 GCS 或 public URL 設定。")],
        )
    except Exception as error:
        logger.error(f"Error in 形勢判斷 command: {error}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 執行形勢判斷時發生錯誤：{str(error)}")],
        )


async def get_game_id(target_id: str) -> str:
    """Get or create game ID for a target (user/group/room)
    Game ID is a unique identifier for each game session.
    """
    state = await load_state_from_gcs(target_id)
    if state and "game_id" in state:
        return state["game_id"]

    # Generate new game ID (timestamp-based)
    new_game_id = f"game_{int(time.time())}"
    # Save to GCS, preserving existing state fields like vs_ai_mode
    if state is None:
        state = {}
    state["game_id"] = new_game_id
    state["current_turn"] = 1
    await save_state_to_gcs(target_id, state)
    logger.info(f"Created new game ID for {target_id}: {new_game_id}")
    return new_game_id


async def enable_vs_ai_mode(target_id: str) -> bool:
    """Enable VS AI mode for a target"""
    try:
        state = await load_state_from_gcs(target_id)
        if state is None:
            state = {}
        
        state["vs_ai_mode"] = True
        success = await save_state_to_gcs(target_id, state)
        if success:
            logger.info(f"Enabled VS AI mode for {target_id}")
        return success
    except Exception as error:
        logger.error(f"Failed to enable VS AI mode for {target_id}: {error}", exc_info=True)
        return False


async def disable_vs_ai_mode(target_id: str) -> bool:
    """Disable VS AI mode for a target"""
    try:
        state = await load_state_from_gcs(target_id)
        if state is None:
            state = {}
        
        state["vs_ai_mode"] = False
        success = await save_state_to_gcs(target_id, state)
        if success:
            logger.info(f"Disabled VS AI mode for {target_id}")
        return success
    except Exception as error:
        logger.error(f"Failed to disable VS AI mode for {target_id}: {error}", exc_info=True)
        return False


async def is_vs_ai_mode(target_id: str) -> bool:
    """Check if VS AI mode is enabled for a target"""
    try:
        state = await load_state_from_gcs(target_id)
        if state is None:
            return False
        return state.get("vs_ai_mode", False)
    except Exception as error:
        logger.error(f"Failed to check VS AI mode for {target_id}: {error}", exc_info=True)
        return False


async def get_game_state(target_id: str) -> Dict[str, Any]:
    """Get or create game state for a target (user/group/room)

    Loads from GCS: tries to restore from latest SGF file, or creates a new game.
    """
    # Load state metadata from GCS
    state_meta = await load_state_from_gcs(target_id)

    if state_meta and "game_id" in state_meta:
        game_id = state_meta["game_id"]
        # Try to load SGF from GCS
        from services.storage import download_file, file_exists

        sgf_remote_path = f"target_{target_id}/boards/{game_id}/game.sgf"
        if await file_exists(sgf_remote_path):
            try:
                sgf_bytes = await download_file(sgf_remote_path)
                sgf_game = sgf.Sgf_game.from_bytes(sgf_bytes)
                restored = restore_game_from_sgf_object(sgf_game)
                if restored:
                    # Use current_turn from SGF restoration (it's calculated from moves)
                    # Only use metadata as fallback if SGF restoration didn't provide it
                    if "current_turn" not in restored:
                        if "current_turn" in state_meta:
                            restored["current_turn"] = state_meta["current_turn"]
                            logger.warning(
                                f"Using current_turn from metadata ({state_meta['current_turn']}) "
                                f"because SGF restoration didn't provide it"
                            )
                    else:
                        # Log if there's a mismatch (for debugging)
                        if "current_turn" in state_meta:
                            sgf_turn = restored["current_turn"]
                            meta_turn = state_meta["current_turn"]
                            if sgf_turn != meta_turn:
                                logger.warning(
                                    f"current_turn mismatch: SGF={sgf_turn}, metadata={meta_turn}. "
                                    f"Using SGF value ({sgf_turn})"
                                )
                    logger.info(f"Restored game state for {target_id} from GCS SGF")
                    return restored
            except Exception as error:
                logger.warning(
                    f"Failed to restore from GCS SGF for {target_id}: {error}"
                )

    # Create new game
    game_id = await get_game_id(target_id)
    new_state = {
        "game": GoBoard(),
        "current_turn": 1,  # 1=黑, 2=白
        "sgf_game": sgf.Sgf_game(size=19),
    }
    logger.info(f"Created new game state for {target_id}")
    return new_state


def create_sgf_with_first_n_moves(sgf_game: sgf.Sgf_game, n_moves: int) -> sgf.Sgf_game:
    """Create a new SGF game with only the first N moves from the original SGF
    
    Args:
        sgf_game: Original SGF game object
        n_moves: Number of moves to keep (1-based, so n_moves=10 means first 10 moves)
    
    Returns:
        New SGF game object with only the first N moves
    """
    # Create a new SGF game with the same board size
    new_sgf = sgf.Sgf_game(size=sgf_game.get_size())
    
    # Copy root properties (like komi, rules, etc.)
    root = sgf_game.get_root()
    new_root = new_sgf.get_root()
    
    # Copy common root properties except moves
    # Common SGF properties: SZ (size), KM (komi), RU (rules), DT (date), 
    # PB/PW (player names), RE (result), HA (handicap), PL (player to move)
    common_props = ["SZ", "KM", "RU", "DT", "PB", "PW", "RE", "HA", "PL", "FF", "CA", "GM", "AP"]
    for prop in common_props:
        if root.has_property(prop):
            values = root.get(prop)
            if values is not None:
                if isinstance(values, (list, tuple)) and len(values) > 0:
                    new_root.set(prop, values[0] if len(values) == 1 else values)
                else:
                    new_root.set(prop, values)
    
    # Get main sequence and take first N moves
    sequence = sgf_game.get_main_sequence()
    move_count = 0
    current_node = new_root
    
    for node in sequence:
        color, move = node.get_move()
        if move is not None:
            move_count += 1
            if move_count > n_moves:
                break  # Stop after N moves
            
            # Create a new child node with this move
            new_node = current_node.new_child()
            new_node.set_move(color, move)
            current_node = new_node
    
    return new_sgf


def restore_game_from_sgf_object(sgf_game: sgf.Sgf_game) -> Optional[Dict[str, Any]]:
    """Restore game state from an SGF game object"""
    try:
        # Rebuild board state from SGF
        game = GoBoard()
        current_turn = 1  # Start with black
        last_move_coords = None

        # 1. Handle Setup Stones (AB, AW, AE) from root node
        root = sgf_game.get_root()
        black_setup, white_setup, empty_setup = root.get_setup_stones()
        
        # Place Black setup stones
        for sgf_r, sgf_c in black_setup:
            r = 18 - sgf_r
            c = sgf_c
            game.board[r][c] = 1 # Black
            
        # Place White setup stones
        for sgf_r, sgf_c in white_setup:
            r = 18 - sgf_r
            c = sgf_c
            game.board[r][c] = 2 # White
            
        # Handle Empty setup (if any)
        for sgf_r, sgf_c in empty_setup:
            r = 18 - sgf_r
            c = sgf_c
            game.board[r][c] = 0 # Empty

        # Check if SGF specifies who starts (PL property)
        # root = sgf_game.get_root() # Already got root above
        if root.has_property("PL"):
            pl_value = root.get("PL")
            if isinstance(pl_value, (list, tuple)) and len(pl_value) > 0:
                pl_value = pl_value[0]
            if pl_value in ("B", "b"):
                current_turn = 1  # Black starts
            elif pl_value in ("W", "w"):
                current_turn = 2  # White starts
            logger.debug(
                f"SGF specifies PL={pl_value}, starting with {'black' if current_turn == 1 else 'white'}"
            )

        # Traverse SGF to rebuild board
        move_count = 0
        sequence = sgf_game.get_main_sequence()
        logger.debug(f"SGF main sequence has {len(sequence)} nodes")
        
        # Variables to store last move info
        last_move_info = None

        for node_idx, node in enumerate(sequence):
            color, move = node.get_move()

            # Log all nodes, even if they don't have moves
            if move is None:
                logger.debug(f"Node {node_idx}: no move (color={color}, move={move})")
                continue

            move_count += 1
            # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
            sgf_r, sgf_c = move

            # Convert to engine coordinates (row 0 is top)
            r = 18 - sgf_r
            c = sgf_c

            last_move_coords = (r, c)

            # Validate color value - sgfmill returns "b" or "w" (lowercase)
            if color is None:
                logger.warning(
                    f"Move {move_count}: color is None, using expected turn (current_turn={current_turn})"
                )
                stone_val = current_turn
            elif color not in ("b", "w"):
                logger.warning(
                    f"Move {move_count}: Invalid color '{color}' in SGF, using expected turn (current_turn={current_turn})"
                )
                stone_val = current_turn
            else:
                stone_val = 1 if color == "b" else 2

            # Verify that the color matches expected turn
            if stone_val != current_turn:
                logger.warning(
                    f"Move {move_count}: Color mismatch! SGF says {color} (stone_val={stone_val}), "
                    f"but expected turn is {current_turn}. Using SGF color."
                )

            # Store last move info (will be logged after loop)
            last_move_info = {
                "move_count": move_count,
                "color": color,
                "stone_val": stone_val,
                "r": r,
                "c": c,
                "expected_turn": current_turn
            }

            # Check if position is already occupied (shouldn't happen in valid SGF, but handle it)
            if game.board[r][c] != 0:
                existing_stone = game.board[r][c]
                logger.warning(
                    f"Move {move_count}: Position ({r}, {c}) already occupied with stone_val={existing_stone}, "
                    f"attempting to place stone_val={stone_val}. This may indicate a problem in SGF."
                )
                # Continue anyway - overwrite (this might be intentional in some SGF formats)

            # Use the same logic as place_stone to ensure consistency
            # 1. Place stone temporarily
            game.board[r][c] = stone_val

            # 2. Check for captured opponent stones
            opponent = 2 if stone_val == 1 else 1
            captured_stones = set()  # Use set to avoid duplicates
            neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
            for nr, nc in neighbors:
                if 0 <= nr < 19 and 0 <= nc < 19:
                    if game.board[nr][nc] == opponent:
                        group, libs = game.get_group_and_liberties(nr, nc)
                        if libs == 0:
                            # Add all stones in the captured group
                            captured_stones.update(group)
                            logger.debug(
                                f"Move {move_count}: Capturing {len(group)} stones at group starting from ({nr}, {nc})"
                            )

            # 3. Remove captured stones
            if captured_stones:
                logger.info(
                    f"Move {move_count}: Removing {len(captured_stones)} captured stones"
                )
            for cr, cc in captured_stones:
                game.board[cr][cc] = 0

            # 4. Check for suicide (shouldn't happen in valid SGF, but we check anyway)
            my_group, my_libs = game.get_group_and_liberties(r, c)
            if my_libs == 0 and len(captured_stones) == 0:
                # Suicide move - this shouldn't happen in valid SGF, but restore it anyway
                logger.warning(
                    f"Move {move_count}: Suicide move detected at ({r}, {c}) in SGF, keeping it for restoration"
                )

            # 5. Update ko point
            if len(captured_stones) == 1 and my_libs == 1:
                # Get the single captured stone position
                captured_pos = list(captured_stones)[0]
                game.ko_point = captured_pos
                logger.debug(f"Move {move_count}: Ko point set to {captured_pos}")
            else:
                game.ko_point = None

            # Switch turn for next move
            current_turn = 2 if stone_val == 1 else 1

        # Log only the last move
        if last_move_info:
            logger.info(
                f"Restoring move {last_move_info['move_count']}: color={last_move_info['color']}, "
                f"stone_val={last_move_info['stone_val']}, pos=({last_move_info['r']},{last_move_info['c']}), "
                f"expected_turn={last_move_info['expected_turn']}"
            )

        logger.info(
            f"Restored {move_count} moves from SGF. Final turn: {'black' if current_turn == 1 else 'white'}"
        )

        return {
            "game": game,
            "current_turn": current_turn,
            "sgf_game": sgf_game,
        }
    except Exception as error:
        logger.error(f"Failed to restore game from SGF object: {error}", exc_info=True)
        return None


def restore_game_from_sgf_file(sgf_path: str) -> Optional[Dict[str, Any]]:
    """Restore game state from a specific SGF file path"""
    try:
        # Load SGF file
        with open(sgf_path, "rb") as f:
            sgf_game = sgf.Sgf_game.from_bytes(f.read())

        # Use the helper function to restore from SGF object
        return restore_game_from_sgf_object(sgf_game)
    except Exception as error:
        logger.error(
            f"Failed to restore game from SGF file {sgf_path}: {error}", exc_info=True
        )
        return None


def restore_game_from_sgf(target_id: str) -> Optional[Dict[str, Any]]:
    """Try to restore game state from latest SGF file for this target"""
    try:
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"

        if not static_dir.exists():
            return None

        # Find SGF file for this target
        # Pattern: static/{game_id}/game_{target_id}.sgf (fixed filename)
        # Try to find the latest game_id folder with this target's SGF
        pattern = f"**/game_{target_id}.sgf"
        sgf_files = list(static_dir.glob(pattern))

        if not sgf_files:
            return None

        # Get the latest file (by modification time)
        latest_sgf = max(sgf_files, key=lambda p: p.stat().st_mtime)

        # Use the helper function to restore
        return restore_game_from_sgf_file(str(latest_sgf))
    except Exception as error:
        logger.error(
            f"Failed to restore game from SGF for {target_id}: {error}", exc_info=True
        )
        return None


async def save_game_sgf(
    target_id: str, state: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Save current game SGF to GCS
    Path structure: target_{target_id}/boards/{game_id}/game.sgf
    Updates the same SGF file for the same game session (same game_id)
    Also saves state metadata (game_id, current_turn) to GCS
    """
    if state is None:
        state = await get_game_state(target_id)

    sgf_game = state["sgf_game"]
    current_turn = state.get("current_turn", 1)

    try:
        from services.storage import upload_buffer

        # Get or create game ID
        game_id = await get_game_id(target_id)

        # Use fixed filename for the same game
        filename = "game.sgf"
        remote_path = f"target_{target_id}/boards/{game_id}/{filename}"

        # Serialize SGF and upload to GCS
        sgf_bytes = sgf_game.serialise()
        # 設定快取控制：no-cache 確保每次都要回源伺服器檢查，避免快取問題
        gcs_path = await upload_buffer(
            sgf_bytes,
            remote_path,
            content_type="application/x-go-sgf",
            cache_control="no-cache, max-age=0",
        )

        # Save state metadata to GCS, preserving existing fields like vs_ai_mode
        existing_state = await load_state_from_gcs(target_id)
        if existing_state is None:
            existing_state = {}
        existing_state["game_id"] = game_id
        existing_state["current_turn"] = current_turn
        await save_state_to_gcs(target_id, existing_state)

        logger.info(f"Saved/Updated game SGF to {gcs_path}")
        return gcs_path
    except Exception as error:
        logger.error(f"Failed to save game SGF: {error}", exc_info=True)
        return None


async def reset_game_state(target_id: str, reply_token: Optional[str] = None):
    """Reset game state for a target and create new game ID

    Args:
        target_id: The target ID (user/group/room)
        reply_token: Optional reply token (not used, kept for compatibility)
    """
    # Generate new game ID for new game
    new_game_id = f"game_{int(time.time())}"

    # Save new state metadata to GCS, preserving existing fields like vs_ai_mode
    existing_state = await load_state_from_gcs(target_id)
    if existing_state is None:
        existing_state = {}
    existing_state["game_id"] = new_game_id
    existing_state["current_turn"] = 1
    await save_state_to_gcs(target_id, existing_state)

    # Save empty SGF to GCS
    new_sgf = sgf.Sgf_game(size=19)
    from services.storage import upload_buffer

    sgf_bytes = new_sgf.serialise()
    remote_path = f"target_{target_id}/boards/{new_game_id}/game.sgf"
    # 設定快取控制：no-cache 確保每次都要回源伺服器檢查，避免快取問題
    await upload_buffer(
        sgf_bytes,
        remote_path,
        content_type="application/x-go-sgf",
        cache_control="no-cache, max-age=0",
    )

    logger.info(f"Reset game state for {target_id}, new game ID: {new_game_id}")


async def handle_board_move(
    target_id: str, reply_token: Optional[str], coord_text: str, source: Dict[str, Any]
):
    """Handle board coordinate input and draw board"""
    try:
        # Get game state for this target
        state = await get_game_state(target_id)
        game = state["game"]
        current_turn = state["current_turn"]
        sgf_game = state["sgf_game"]

        # Place stone
        success, msg = game.place_stone(coord_text, current_turn)

        if not success:
            # Failed to place stone, send error message
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"提示：{msg}")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Successfully placed stone
        coords = game.parse_coordinates(coord_text)

        # --- 1. Update SGF record ---
        node = sgf_game.get_last_node()
        new_node = node.new_child()

        color_code = "b" if current_turn == 1 else "w"

        # coords is (row, col), where row 0 is top
        # sgfmill thinks row 0 is bottom, so flip: (19 - 1 - row)
        sgf_row = 18 - coords[0]
        sgf_col = coords[1]

        new_node.set_move(color_code, (sgf_row, sgf_col))

        # --- 2. Switch turn and update state ---
        state["current_turn"] = 2 if current_turn == 1 else 1

        # Save SGF file and state metadata
        sgf_path = await save_game_sgf(target_id, state)
        if sgf_path:
            logger.info(f"Saved game SGF: {sgf_path}")

        # Generate board image
        import tempfile
        from services.storage import upload_file, get_public_url

        # Get game ID
        game_id = await get_game_id(target_id)

        timestamp = int(time.time())
        filename = f"board_{timestamp}.png"

        # Draw board to temporary file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        visualizer.draw_board(game.board, last_move=coords, output_filename=tmp_path)

        # Upload to GCS
        remote_path = f"target_{target_id}/boards/{game_id}/{filename}"
        await upload_file(tmp_path, remote_path)

        # Get public URL
        image_url = get_public_url(remote_path)

        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except:
            pass

        # Check if VS AI mode is enabled
        vs_ai_mode = await is_vs_ai_mode(target_id)
        
        if is_valid_https_url(image_url):
            # If VS AI mode is enabled, don't reply immediately, wait for AI's move
            if vs_ai_mode:
                # Call Modal function asynchronously (non-blocking)
                # Pass reply_token and user's board image URL so callback can send everything together
                try:
                    import modal
                    modal_app_name = config.get("modal", {}).get("app_name")
                    modal_function_get_ai_next_move = config.get("modal", {}).get("function_get_ai_next_move")
                    callback_get_ai_next_move_url = config.get("cloud_run", {}).get("callback_get_ai_next_move_url")
                    
                    if modal_app_name and modal_function_get_ai_next_move and callback_get_ai_next_move_url:
                        # Get SGF GCS path (save_game_sgf returns gs:// format)
                        sgf_gcs_path = sgf_path if sgf_path and sgf_path.startswith("gs://") else None
                        
                        if not sgf_gcs_path:
                            logger.error(f"Invalid SGF path: {sgf_path}")
                        else:
                            # Get current turn (after user's move, it's AI's turn)
                            ai_current_turn = state["current_turn"]
                        
                            # Spawn Modal function asynchronously
                            # Pass reply_token and user_board_image_url to callback
                            vs_ai_function = modal.Function.from_name(
                                modal_app_name, modal_function_get_ai_next_move
                            )
                            vs_ai_function.spawn(
                                sgf_gcs_path=sgf_gcs_path,
                                callback_url=callback_get_ai_next_move_url,
                                target_id=target_id,
                                current_turn=ai_current_turn,
                                reply_token=reply_token,  # Pass reply_token to callback
                                user_board_image_url=image_url,  # Pass user's board image URL
                            )
                            logger.info(f"Spawned Modal function for VS AI: target_id={target_id}, current_turn={ai_current_turn}")
                            # Don't send reply here, wait for AI callback to respond
                            return
                    else:
                        logger.error("Modal app_name, function_get_ai_next_move, or callback_get_ai_next_move_url not configured")
                except Exception as modal_error:
                    logger.error(f"Error calling Modal function for VS AI: {modal_error}", exc_info=True)
                    # If error, fall through to send user's move image
            
            # Send board image (non-VS AI mode, or error in VS AI mode)
            messages = [
                ImageMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url,
                )
            ]
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages,
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            logger.warning(f"Invalid image URL: {image_url}")
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"✅ {msg}\n\n⚠️ 圖片 URL 無效，請檢查 GCS_BUCKET_NAME 設定"
                    )
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling board move: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"❌ 處理落子時發生錯誤：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)



async def handle_bottom_line_kill_mode(target_id: str, reply_token: Optional[str]):
    """Handle Bottom Line Kill Mode (一線擺滿殺棋模式)"""
    try:
        # Load bottom_line_game.sgf
        current_file = Path(__file__)
        project_root = current_file.parent.parent
        static_dir = project_root / "static"
        sgf_path = static_dir / "bottom_line_game.sgf"

        if not sgf_path.exists():
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 找不到一線擺滿殺棋模式的棋譜檔案。")],
            )
            return

        # Restore game state
        restored = restore_game_from_sgf_file(str(sgf_path))
        if not restored:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 無法讀取一線擺滿殺棋模式的棋譜。")],
            )
            return

        # Update game state
        # Create new game ID
        new_game_id = f"bottomlinekill_{int(time.time())}"
        
        # Save state metadata
        state = restored
        state["game_id"] = new_game_id
        await save_state_to_gcs(target_id, state)
        
        # Save SGF to GCS
        await save_game_sgf(target_id, state)
        
        logger.info(f"Started Bottom Line Kill Mode for {target_id}, game_id={new_game_id}")

        # Draw board and send image
        game = state["game"]
        current_turn = state["current_turn"]
        
        # Find last move (if any)
        last_coords = None
        sgf_game = state["sgf_game"]
        sequence = sgf_game.get_main_sequence()
        for node in reversed(sequence):
             color, move = node.get_move()
             if move is not None:
                 sgf_r, sgf_c = move
                 r = 18 - sgf_r
                 c = sgf_c
                 last_coords = (r, c)
                 break

        # Generate Image
        import tempfile
        from services.storage import upload_file, get_public_url

        timestamp = int(time.time())
        filename = f"board_bottomlinekill_{timestamp}.png"

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        
        visualizer.draw_board(game.board, last_move=last_coords, output_filename=tmp_path)
        
        # Upload to GCS
        remote_path = f"target_{target_id}/boards/{new_game_id}/{filename}"
        await upload_file(tmp_path, remote_path)
        
        # Get URL
        image_url = get_public_url(remote_path)
        
        try:
            os.unlink(tmp_path)
        except:
            pass

        turn_text = "黑" if current_turn == 1 else "白"
        
        if is_valid_https_url(image_url):
            messages = [
                TextMessage(text=f"開始一線擺滿殺棋模式！\n現在輪到：{turn_text}"),
                ImageMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url,
                ),
            ]
            await send_message(target_id, reply_token, messages)
        else:
             await send_message(
                target_id,
                reply_token,
                [TextMessage(text=f"開始一線擺滿殺棋模式！\n現在輪到：{turn_text}\n\n⚠️ 圖片 URL 無效")],
            )

    except Exception as e:
        logger.error(f"Error handling One Line Kill Mode: {e}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 發生錯誤：{str(e)}")],
        )


async def handle_load_game_by_id_with_moves(
    target_id: str, reply_token: Optional[str], source_game_id: str, move_count: int
):
    """Handle load game by game ID with move count (讀取 {gameid} {手數})
    
    This function:
    1. Loads the SGF file for the specified game_id from GCS
    2. Extracts only the first N moves
    3. Creates a new game_id
    4. Saves the truncated SGF file to GCS
    5. Updates state to the new game_id
    """
    try:
        # Load SGF from GCS using the source game_id
        from services.storage import download_file, file_exists, upload_buffer, get_public_url

        source_sgf_remote_path = f"target_{target_id}/boards/{source_game_id}/game.sgf"
        if not await file_exists(source_sgf_remote_path):
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"找不到 game_id 為 {source_game_id} 的棋譜。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Download source SGF
        sgf_bytes = await download_file(source_sgf_remote_path)
        source_sgf_game = sgf.Sgf_game.from_bytes(sgf_bytes)
        
        # Get main sequence to count total moves
        sequence = source_sgf_game.get_main_sequence()
        total_moves = sum(1 for node in sequence if node.get_move()[1] is not None)
        
        if move_count > total_moves:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"該棋譜只有 {total_moves} 手，無法讀取到第 {move_count} 手。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Create new SGF with only first N moves
        truncated_sgf = create_sgf_with_first_n_moves(source_sgf_game, move_count)
        
        # Create new game_id for the truncated game
        new_game_id = f"game_{int(time.time())}"
        
        # Save truncated SGF to GCS
        new_sgf_remote_path = f"target_{target_id}/boards/{new_game_id}/game.sgf"
        truncated_sgf_bytes = truncated_sgf.serialise()
        
        await upload_buffer(
            truncated_sgf_bytes,
            new_sgf_remote_path,
            content_type="application/x-go-sgf",
            cache_control="no-cache, max-age=0",
        )
        
        logger.info(f"Created truncated SGF with {move_count} moves: {new_sgf_remote_path}")
        
        # Restore game state from truncated SGF
        restored = restore_game_from_sgf_object(truncated_sgf)
        if not restored:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="讀取失敗：無法解析棋譜檔案。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        state = restored
        game = state["game"]
        current_turn = state["current_turn"]

        # Always update state.json with restored state from truncated SGF
        # Preserve vs_ai_mode from existing state if it exists
        existing_state = await load_state_from_gcs(target_id)
        vs_ai_mode = existing_state.get("vs_ai_mode", False) if existing_state else False
        
        await save_state_to_gcs(
            target_id,
            {
                "game_id": new_game_id,
                "current_turn": current_turn,
                "vs_ai_mode": vs_ai_mode,  # Preserve vs_ai_mode state
            },
        )
        logger.info(
            f"Updated state.json for {target_id} with truncated game: game_id={new_game_id}, current_turn={current_turn}, moves={move_count}"
        )

        # Find last move coordinates for highlighting and build move_numbers dict
        last_coords = None
        move_numbers = {}  # {(row, col): move_number}
        sequence = truncated_sgf.get_main_sequence()
        move_num = 0
        
        # Traverse sequence to build move_numbers and find last move
        for node in sequence:
            color, move = node.get_move()
            if move is not None:
                move_num += 1
                # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
                sgf_r, sgf_c = move
                # Convert to engine coordinates (row 0 is top)
                r = 18 - sgf_r
                c = sgf_c
                move_numbers[(r, c)] = move_num
                last_coords = (r, c)  # Last move will be the final one

        # Draw board
        import tempfile
        from services.storage import upload_file

        timestamp = int(time.time())
        filename = f"board_restored_{timestamp}.png"

        # Draw board to temporary file with move numbers
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=tmp_path, move_numbers=move_numbers
        )

        # Upload to GCS
        remote_path = f"target_{target_id}/boards/{new_game_id}/{filename}"
        await upload_file(tmp_path, remote_path)

        # Get public URL
        image_url = get_public_url(remote_path)

        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except:
            pass

        # Send board image
        turn_text = "黑" if current_turn == 1 else "白"
        total_moves_text = f"總手數：{move_count} 手"
        
        if is_valid_https_url(image_url):
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜 (game_id: {source_game_id}) 前 {move_count} 手！\n新對局 game_id: {new_game_id}\n{total_moves_text}\n目前輪到：{turn_text}"
                    ),
                    ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    ),
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            logger.warning(f"Invalid image URL: {image_url}")
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜 (game_id: {source_game_id}) 前 {move_count} 手！\n新對局 game_id: {new_game_id}\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                    )
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling load game by ID with moves: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"讀取失敗：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)


async def handle_undo_move(
    target_id: str, reply_token: Optional[str], undo_steps: int = 1
):
    """Handle undo move (悔棋), supports multiple steps."""
    try:
        if undo_steps <= 0:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="悔棋手數需為正整數，例如：悔棋 10")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Get game state
        state = await get_game_state(target_id)
        sgf_game = state["sgf_game"]

        try:
            # Delete N moves from SGF (or until root)
            actual_undo_steps = 0
            for _ in range(undo_steps):
                last_node = sgf_game.get_last_node()
                parent_node = last_node.parent
                if parent_node is None:
                    break
                last_node.delete()
                actual_undo_steps += 1

            if actual_undo_steps == 0:
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="目前是初始狀態，無法悔棋。")],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
                return

            # Restore game state directly from updated SGF object
            restored = restore_game_from_sgf_object(sgf_game)
            if restored:
                state = restored
            else:
                # If restore failed, reset to empty board
                logger.warning(
                    f"Failed to restore game from SGF after undo, resetting to empty board"
                )
                state = {
                    "game": GoBoard(),
                    "current_turn": 1,
                    "sgf_game": sgf.Sgf_game(size=19),
                }

            # Save updated SGF to GCS after restoring state
            await save_game_sgf(target_id, state)

            game = state["game"]
            current_turn = state["current_turn"]

            # Find last move coordinates for highlighting from SGF sequence
            last_coords = None
            sgf_game = state["sgf_game"]
            sequence = sgf_game.get_main_sequence()
            # Traverse sequence backwards to find the last move
            for node in reversed(sequence):
                color, move = node.get_move()
                if move is not None:
                    # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
                    sgf_r, sgf_c = move
                    # Convert to engine coordinates (row 0 is top)
                    r = 18 - sgf_r
                    c = sgf_c
                    last_coords = (r, c)
                    break  # Found the last move, exit loop

            # Draw board
            import tempfile
            from services.storage import upload_file, get_public_url

            game_id = await get_game_id(target_id)
            timestamp = int(time.time())
            filename = f"board_undo_{timestamp}.png"

            # Draw board to temporary file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                tmp_path = tmp_file.name

            visualizer.draw_board(
                game.board, last_move=last_coords, output_filename=tmp_path
            )

            # Upload to GCS
            remote_path = f"target_{target_id}/boards/{game_id}/{filename}"
            await upload_file(tmp_path, remote_path)

            # Get public URL
            image_url = get_public_url(remote_path)

            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except:
                pass

            turn_text = "黑" if current_turn == 1 else "白"
            undo_text = (
                "↩️ 已悔棋一步。"
                if actual_undo_steps == 1
                else f"↩️ 已悔棋 {actual_undo_steps} 手。"
            )
            if actual_undo_steps < undo_steps:
                undo_text += f"\n（要求 {undo_steps} 手，實際 {actual_undo_steps} 手）"

            if is_valid_https_url(image_url):
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text=f"{undo_text}\n現在輪到：{turn_text}"),
                        ImageMessage(
                            original_content_url=image_url,
                            preview_image_url=image_url,
                        ),
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
            else:
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(
                            text=f"{undo_text}\n現在輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)

        except Exception as e:
            logger.error(f"Error undoing move: {e}", exc_info=True)
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"悔棋失敗：{str(e)}")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling undo move: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"❌ 處理悔棋時發生錯誤：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)


async def handle_load_game_by_id(
    target_id: str, reply_token: Optional[str], game_id: Optional[str] = None
):
    """Handle load game by game ID (讀取 {gameid}) - Load specific game by game_id
    If game_id is None, loads the current game from state metadata
    """
    try:
        # If game_id is not provided, get it from state metadata
        state_meta = None
        if game_id is None:
            state_meta = await load_state_from_gcs(target_id)
            if not state_meta or "game_id" not in state_meta:
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text="找不到存檔。")],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
                return
            game_id = state_meta["game_id"]

        # Load SGF from GCS using the game_id
        from services.storage import download_file, file_exists, get_public_url

        sgf_remote_path = f"target_{target_id}/boards/{game_id}/game.sgf"
        if not await file_exists(sgf_remote_path):
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"找不到 game_id 為 {game_id} 的棋譜。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Download and restore game state
        sgf_bytes = await download_file(sgf_remote_path)
        sgf_game = sgf.Sgf_game.from_bytes(sgf_bytes)
        restored = restore_game_from_sgf_object(sgf_game)

        if not restored:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="讀取失敗：無法解析棋譜檔案。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        state = restored
        game = state["game"]
        current_turn = state["current_turn"]

        # Always update state.json with restored state from SGF when loading any game
        # This ensures state.json reflects the actual state from SGF, not the old cached value
        # If loading a historical game, this will switch the current game to that historical game
        # Preserve vs_ai_mode from existing state if it exists
        existing_state = await load_state_from_gcs(target_id)
        vs_ai_mode = existing_state.get("vs_ai_mode", False) if existing_state else False
        
        await save_state_to_gcs(
            target_id,
            {
                "game_id": game_id,
                "current_turn": current_turn,
                "vs_ai_mode": vs_ai_mode,  # Preserve vs_ai_mode state
            },
        )
        logger.info(
            f"Updated state.json for {target_id} with restored state from SGF: game_id={game_id}, current_turn={current_turn}"
        )

        # Update game state in memory
        from handlers.line_handler import get_game_state
        # Note: get_game_state will load from GCS, so state is already updated above
        
        # Find last move coordinates for highlighting and build move_numbers dict
        # Get the last move from SGF sequence and build move_numbers
        last_coords = None
        move_numbers = {}  # {(row, col): move_number}
        sequence = sgf_game.get_main_sequence()
        move_num = 0
        
        # Traverse sequence to build move_numbers and find last move
        for node in sequence:
            color, move = node.get_move()
            if move is not None:
                move_num += 1
                # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
                sgf_r, sgf_c = move
                # Convert to engine coordinates (row 0 is top)
                r = 18 - sgf_r
                c = sgf_c
                move_numbers[(r, c)] = move_num
                last_coords = (r, c)  # Last move will be the final one

        # Draw board
        import tempfile
        from services.storage import upload_file

        timestamp = int(time.time())
        filename = f"board_restored_{timestamp}.png"

        # Draw board to temporary file with move numbers
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=tmp_path, move_numbers=move_numbers
        )

        # Upload to GCS
        remote_path = f"target_{target_id}/boards/{game_id}/{filename}"
        await upload_file(tmp_path, remote_path)

        # Get public URL
        image_url = get_public_url(remote_path)

        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except:
            pass

        turn_text = "黑" if current_turn == 1 else "白"
        total_moves = move_num
        total_moves_text = f"總手數：{total_moves} 手"

        # Format message text based on whether game_id was provided
        if game_id:
            message_text = f"📂 已讀取棋譜 (game_id: {game_id})！\n{total_moves_text}\n目前輪到：{turn_text}"
        else:
            message_text = f"📂 已讀取棋譜！\n{total_moves_text}\n目前輪到：{turn_text}"

        if is_valid_https_url(image_url):
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(text=message_text),
                    ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    ),
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"{message_text}\n\n⚠️ 圖片 URL 無效")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling load game by ID: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"讀取失敗：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)


async def handle_text_message(event: Dict[str, Any]):
    """Handle text message"""
    reply_token = event.get("replyToken")
    message = event.get("message", {})
    source = event.get("source", {})
    text = message.get("text", "").strip()

    # In group/room, only process mention messages
    if source.get("type") in ["group", "room"]:
        # First, check if text starts with "@{bot_display_name}" (text mention for desktop LINE)
        bot_display_name = await get_bot_display_name()
        text_mention_matched = False
        if bot_display_name:
            # Escape special regex characters in bot display name
            escaped_display_name = re.escape(bot_display_name)
            text_mention_pattern = rf"^@{escaped_display_name}\s+(.+)$"
            text_mention_match = re.match(text_mention_pattern, text, re.IGNORECASE)
            
            if text_mention_match:
                # Extract command after @{bot_display_name}
                text = text_mention_match.group(1).strip()
                text_mention_matched = True
        else:
            logger.error("Failed to get bot display_name, skipping text mention check")
        
        # Fallback to mention API (for mobile LINE) if text mention didn't match
        if not text_mention_matched:
            mention = message.get("mention")
            if (
                not mention
                or not mention.get("mentionees")
                or len(mention["mentionees"]) == 0
            ):
                # No mention and no text mention, ignore this message
                return

            # Check if mention includes bot itself
            mentions = mention["mentionees"]
            bot_user_id = await get_bot_user_id()
            is_bot_mentioned = (
                any(mentionee.get("userId") == bot_user_id for mentionee in mentions)
                if bot_user_id
                else False
            )

            if not is_bot_mentioned:
                # Mention is not bot, ignore this message
                return

            # Remove mention markers to get actual command
            clean_text = text
            # Sort mentions by index descending to avoid index position changes
            for mention_obj in sorted(
                mentions, key=lambda x: x.get("index", 0), reverse=True
            ):
                index = mention_obj.get("index", 0)
                length = mention_obj.get("length", 0)
                clean_text = clean_text[:index] + clean_text[index + length :]

            text = clean_text.strip()

    # Get target ID for game state management
    target_id = source.get("groupId") or source.get("roomId") or source.get("userId")

    if text == "一線擺滿殺棋模式":
        await handle_bottom_line_kill_mode(target_id, reply_token)
        return

    if text in ["help", "幫助", "說明"]:
        request = ReplyMessageRequest(
            reply_token=reply_token, messages=[TextMessage(text=HELP_MESSAGE)]
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if "認證" in text or "auth" in text.lower():
        auth_match = re.match(r"^(?:auth|認證)\s+(.+)$", text, re.IGNORECASE)
        if auth_match:
            token = auth_match.group(1).strip()
            await handle_auth_command(target_id, reply_token, token)
            return

    review_setting_match = re.match(
        r"^(?:review\s+setting|覆盤設定)(?:\s+([a-zA-Z_]+))?$", text, re.IGNORECASE
    )
    if review_setting_match:
        metric = review_setting_match.group(1)
        await handle_review_setting_command(target_id, reply_token, metric)
        return

    if text == "覆盤" or text.lower() == "review":
        await handle_review_command(target_id, reply_token)
        return

    if text == "形勢" or text == "形式" or text.lower() == "evaluation":
        await handle_evaluation_command(target_id, reply_token)
        return

    # Handle Guess First
    if text.startswith("猜先 "):
        parts = text.split()
        if len(parts) >= 3:
            player1 = parts[1]
            player2 = parts[2]
            target_id = source.get("groupId") or source.get("roomId") or source.get("userId")
            await handle_guess_first_command(target_id, reply_token, player1, player2)
            return

    undo_match = re.match(r"^(?:悔棋|undo)(?:\s+(\d+))?$", text, re.IGNORECASE)
    if undo_match:
        undo_steps = int(undo_match.group(1)) if undo_match.group(1) else 1
        await handle_undo_move(target_id, reply_token, undo_steps=undo_steps)
        return

    if "讀取" in text or "load" in text.lower():
        # Match "讀取 game_1234567890 10" or "讀取 game_1234567890 10" or "load game_1234567890 10"
        # Pattern: (讀取|load) game_\d+ \d+
        read_with_moves_match = re.match(r"(?:讀取|load)\s+(game_\d+)\s+(\d+)", text, re.IGNORECASE)
        if read_with_moves_match:
            game_id = read_with_moves_match.group(1).strip()
            move_count_str = read_with_moves_match.group(2).strip()
            try:
                move_count = int(move_count_str)
                if move_count > 0:
                    await handle_load_game_by_id_with_moves(target_id, reply_token, game_id, move_count)
                    return
            except ValueError:
                pass  # Invalid move count, fall through to regular load
        
        # Match "讀取 game_1234567890" or "讀取game_1234567890" or "load game_1234567890"
        # Ensure we match the full game_id format: game_ followed by digits
        read_match = re.match(r"(?:讀取|load)\s*(game_\d+)", text, re.IGNORECASE)
        if read_match:
            game_id = read_match.group(1).strip()
            if game_id:  # Make sure game_id is not empty
                # Load specific game by game_id
                await handle_load_game_by_id(target_id, reply_token, game_id)
                return

        # Load current game (no game_id specified)
        await handle_load_game_by_id(target_id, reply_token, None)
        return

    # Handle "對弈" to show current mode status
    if text.lower() in ["對弈", "vs"]:
        # Check current VS AI mode status
        vs_ai_mode = await is_vs_ai_mode(target_id)
        state_meta = await load_state_from_gcs(target_id)
        current_turn = state_meta.get("current_turn", 1) if state_meta else 1
        
        if vs_ai_mode:
            mode_text = "AI 對弈模式"
            ai_color = "黑" if current_turn == 1 else "白"
            user_color = "白" if current_turn == 1 else "黑"
            status_message = f"""📊 目前模式：{mode_text}

您執{user_color}，AI 執{ai_color}。

🤖 AI 對弈模式：
• 您下完一手後，AI 會自動思考並下下一手
• 適合與 AI 對戰練習

🆓 一般對弈模式：
• 一人一手棋，輪流下棋
• 適合與朋友對戰或自己練習

💡 切換模式：
• 輸入「對弈 ai」開啟 AI 對弈模式（需先認證）
• 輸入「對弈 free」切換為一般對弈模式"""
        else:
            mode_text = "一般對弈模式"
            status_message = f"""📊 目前模式：{mode_text}

🆓 一般對弈模式：
• 一人一手棋，輪流下棋
• 適合與朋友對戰或自己練習

🤖 AI 對弈模式：
• 您下完一手後，AI 會自動思考並下下一手
• 適合與 AI 對戰練習
• 需先使用「auth <token>」進行認證

💡 切換模式：
• 輸入「對弈 ai」開啟 AI 對弈模式（需先認證）
• 輸入「對弈 free」切換為一般對弈模式"""
        
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=status_message)],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    # Handle "對弈 ai" to enable VS AI mode
    if text.lower() in ["對弈 ai", "對弈ai", "vs ai", "vsai"]:
        # Check authentication only if AUTH_TOKEN is configured
        auth_token = config.get("auth", {}).get("token")
        if auth_token:
            # AUTH_TOKEN is configured, require authentication
            from services.storage import check_auth
            
            # Verify authentication
            is_authenticated = await check_auth(target_id, auth_token)
            if not is_authenticated:
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(
                            text="❌ 請先使用 'auth <token>' 指令進行認證，才可使用 AI 對弈功能"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
                return
        
        # Enable VS AI mode
        success = await enable_vs_ai_mode(target_id)
        if success:
            # Get current turn to determine AI color
            state_meta = await load_state_from_gcs(target_id)
            current_turn = state_meta.get("current_turn", 1) if state_meta else 1
            user_color = "黑" if current_turn == 1 else "白"
            ai_color = "白" if current_turn == 1 else "黑"
            
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"✅ 已開啟 AI 對弈模式！\n\n您執{user_color}，AI 執{ai_color}。\n請開始下棋（例如：D4）。"
                    )
                ],
            )
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="❌ 開啟對弈模式失敗，請稍後再試。")],
            )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    # Handle "對弈 free" to disable VS AI mode
    if text.lower() in ["對弈 free", "對弈free", "vs free", "vsfree"]:
        # Disable VS AI mode
        success = await disable_vs_ai_mode(target_id)
        if success:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text="✅ 已關閉 AI 對弈模式！\n\n現在恢復為一般對弈模式（一人一手棋）。"
                    )
                ],
            )
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="❌ 關閉對弈模式失敗，請稍後再試。")],
            )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if "投子" in text:
        current_game_id = None
        current_sgf_url = None
        current_turn = 1

        try:
            state_meta = await load_state_from_gcs(target_id)
            if state_meta:
                current_turn = state_meta.get("current_turn", 1)
                if "game_id" in state_meta:
                    current_game_id = state_meta["game_id"]
                    from services.storage import file_exists, get_public_url

                    sgf_remote_path = (
                        f"target_{target_id}/boards/{current_game_id}/game.sgf"
                    )
                    if await file_exists(sgf_remote_path):
                        current_sgf_url = get_public_url(sgf_remote_path)
        except Exception as error:
            logger.warning(f"Failed to get current SGF before 投子: {error}")

        resign_side = "黑" if current_turn == 1 else "白"
        winner_side = "白" if current_turn == 1 else "黑"
        resign_msg = f"{resign_side}方投子，{winner_side}方獲勝！"

        await reset_game_state(target_id, reply_token)

        messages = [TextMessage(text=resign_msg)]
        if current_sgf_url and is_valid_https_url(current_sgf_url) and current_game_id:
            sgf_flex_message = create_sgf_file_flex_message(
                current_sgf_url, current_game_id
            )
            messages.append(sgf_flex_message)
        messages.append(TextMessage(text="✅ 棋盤已重置，黑棋請下。"))

        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=messages,
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if "重置" in text or "reset" in text.lower():
        # Get current game ID and SGF file before reset
        current_game_id = None
        current_sgf_url = None

        try:
            state_meta = await load_state_from_gcs(target_id)
            if state_meta and "game_id" in state_meta:
                current_game_id = state_meta["game_id"]
                from services.storage import file_exists, get_public_url

                sgf_remote_path = (
                    f"target_{target_id}/boards/{current_game_id}/game.sgf"
                )
                if await file_exists(sgf_remote_path):
                    current_sgf_url = get_public_url(sgf_remote_path)
        except Exception as error:
            logger.warning(f"Failed to get current SGF before reset: {error}")

        # Reset game state (preserving vs_ai_mode)
        await reset_game_state(target_id, reply_token)

        messages = []
        if current_sgf_url and is_valid_https_url(current_sgf_url) and current_game_id:
            # Send SGF file using Flex Message with download button
            sgf_flex_message = create_sgf_file_flex_message(
                current_sgf_url, current_game_id
            )
            messages.append(sgf_flex_message)

        messages.append(TextMessage(text="✅ 棋盤已重置，黑棋請下。"))

        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=messages,
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    # Check if input is a board coordinate (A-T, 1-19)
    # Pattern matches coordinates like "D4", "Q16", etc. (skips 'I')
    coord_pattern = r"^[A-HJ-T]([1-9]|1[0-9])$"
    user_text_upper = text.upper().strip()

    if re.match(coord_pattern, user_text_upper):
        # Handle board coordinate input
        await handle_board_move(target_id, reply_token, user_text_upper, source)
        return


async def handle_file_message(event: Dict[str, Any]):
    """Handle file message"""
    reply_token = event.get("replyToken")
    message = event.get("message", {})
    source = event.get("source", {})

    # Get push target ID (based on source type)
    target_id = source.get("groupId") or source.get("roomId") or source.get("userId")
    # Get user ID (for task tracking)
    user_id = source.get("userId") or target_id

    try:
        # Get file content
        content_id = message.get("id")
        # Run synchronous call in thread pool
        file_content = await asyncio.to_thread(blob_api.get_message_content, content_id)

        # Convert payload to bytes
        if isinstance(file_content, bytes):
            file_buffer = file_content
        elif hasattr(file_content, "data"):
            file_buffer = file_content.data
        elif hasattr(file_content, "body"):
            file_buffer = file_content.body
        elif hasattr(file_content, "read"):
            file_buffer = file_content.read()
        elif hasattr(file_content, "iter_content"):
            file_buffer = b"".join(chunk for chunk in file_content.iter_content())
        else:
            raise ValueError("Unsupported LINE blob response format")

        # Check file type
        file_name = message.get("fileName", "game.sgf")
        if not file_name.lower().endswith(".sgf"):
            return

        # Remove .sgf extension (case-insensitive) before passing to save_sgf_file
        file_name_lower = file_name.lower()
        if file_name_lower.endswith(".sgf"):
            # Remove the extension, preserving original case for the base name
            ext_length = len(".sgf")
            file_name_without_ext = file_name[:-ext_length]
        else:
            file_name_without_ext = file_name

        # Save file to GCS in reviews folder
        saved_file = await save_sgf_file(file_buffer, file_name_without_ext, target_id)

        # Notify user file is saved (use replyMessage to reduce usage)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(
                    text=f"""✅ 棋譜已保存！

📁 檔案: {file_name}

棋譜已保存到伺服器，後續可執行 "覆盤" 或 "review" 指令進行分析..."""
                )
            ],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
    except Exception as error:
        logger.error(f"Error handling file message: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"❌ 儲存棋譜時發生錯誤：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
