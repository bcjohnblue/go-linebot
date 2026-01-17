import os
import re
import json
import time
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


HELP_MESSAGE = """歡迎使用圍棋分析 Bot！

指令：
• help / 幫助 / 說明 - 顯示此說明

🎮 對局功能：
• 輸入座標（如 D4, Q16）- 落子並顯示棋盤
• 悔棋 / undo - 撤銷上一步
• 讀取 / load - 從存檔恢復遊戲
• 重置 / reset - 重置棋盤，開始新遊戲

📊 覆盤功能：
• 覆盤 / review - 對最新上傳的棋譜執行 KataGo 覆盤

使用流程：
1️⃣ 上傳 SGF 棋譜檔案
2️⃣ 輸入「覆盤」開始分析
3️⃣ 等待 10-15 分鐘獲得分析結果

注意事項：
• 分析使用 KataGo AI（visits=200）
• KataGo 全盤分析約 10 分鐘
• ChatGPT 評論生成約 1 分鐘
• GIF 動畫繪製約 10 秒
• 覆盤功能每次消耗 4 個推播訊息 × 群組人數
• 每月訊息上限為 200 則，請注意使用頻率，超出上限將無法使用覆盤功能"""


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
            return True  # Successfully used replyMessage
        except ApiException as e:
            # replyToken may have expired, fallback to pushMessage
            if e.status in [400, 410]:
                print("replyToken expired or invalid, using pushMessage instead")
            else:
                raise

    # Use pushMessage
    request = PushMessageRequest(to=target_id, messages=messages)
    await asyncio.to_thread(line_bot_api.push_message, request)
    return False  # Used pushMessage


async def handle_review_command(target_id: str, reply_token: Optional[str]):
    """Handle review command - POST to localhost service for review"""
    import httpx
    import uuid

    used_reply_token = False

    try:
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

        # Get localhost URL and callback URL from config
        localhost_url = config.get("localhost", {}).get("review_url")
        callback_review_url = config.get("cloud_run", {}).get("callback_review_url")

        if not localhost_url:
            logger.error("LOCALHOST_REVIEW_URL not configured")
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 系統配置錯誤：未設定本地 KataGo 服務 URL")],
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
                    text=f"✅ 開始對棋譜：{sgf_file_name} 進行覆盤分析，完成大約需要 12 分鐘...，請稍後再回來查看分析結果。"
                )
            ],
        )

        # After using replyToken, set to None, subsequent messages use pushMessage
        if used_reply_token:
            reply_token = None

        # POST review request to localhost service
        logger.info(f"Posting review request to localhost: {localhost_url}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                localhost_url,
                json={
                    "task_id": task_id,
                    "sgf_gcs_path": sgf_gcs_path,
                    "callback_url": callback_review_url,
                    "target_id": target_id,
                    "visits": 5,
                },
            )
            response.raise_for_status()
            logger.info(f"Successfully posted review request: {response.status_code}")

        # Review will continue asynchronously via callback
        # No need to wait here
    except Exception as error:
        logger.error(f"Error in 覆盤 command: {error}", exc_info=True)
        await send_message(
            target_id,
            None,
            [TextMessage(text=f"❌ 執行覆盤時發生錯誤：{str(error)}")],
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
    # Save to GCS
    await save_state_to_gcs(target_id, {"game_id": new_game_id, "current_turn": 1})
    logger.info(f"Created new game ID for {target_id}: {new_game_id}")
    return new_game_id


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


def restore_game_from_sgf_object(sgf_game: sgf.Sgf_game) -> Optional[Dict[str, Any]]:
    """Restore game state from an SGF game object"""
    try:
        # Rebuild board state from SGF
        game = GoBoard()
        current_turn = 1  # Start with black
        last_move_coords = None

        # Check if SGF specifies who starts (PL property)
        root = sgf_game.get_root()
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

            logger.info(
                f"Restoring move {move_count}: color={color}, stone_val={stone_val}, pos=({r},{c}), expected_turn={current_turn}"
            )

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
            logger.debug(
                f"Move {move_count} complete. Next turn: {'black' if current_turn == 1 else 'white'}"
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

        # Save state metadata (game_id, current_turn) to GCS
        await save_state_to_gcs(
            target_id, {"game_id": game_id, "current_turn": current_turn}
        )

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

    # Save new state metadata to GCS
    await save_state_to_gcs(target_id, {"game_id": new_game_id, "current_turn": 1})

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

        if is_valid_https_url(image_url):
            # Send board image
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    )
                ],
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


async def handle_undo_move(target_id: str, reply_token: Optional[str]):
    """Handle undo move (悔棋)"""
    try:
        # Get game state
        state = await get_game_state(target_id)
        sgf_game = state["sgf_game"]

        # Get last node
        last_node = sgf_game.get_last_node()
        parent_node = last_node.parent

        # Check if it's root node (can't undo)
        if parent_node is None:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="目前是初始狀態，無法悔棋。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        try:
            # Delete last move from SGF
            last_node.delete()

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

            # Find last move coordinates for highlighting
            last_coords = None
            for r in range(19):
                for c in range(19):
                    if game.board[r][c] != 0:
                        last_coords = (r, c)

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

            if is_valid_https_url(image_url):
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text=f"↩️ 已悔棋一步。\n現在輪到：{turn_text}"),
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
                            text=f"↩️ 已悔棋一步。\n現在輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
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
        await save_state_to_gcs(
            target_id,
            {
                "game_id": game_id,
                "current_turn": current_turn,
            },
        )
        logger.info(
            f"Updated state.json for {target_id} with restored state from SGF: game_id={game_id}, current_turn={current_turn}"
        )

        # Find last move coordinates for highlighting
        # Get the last move from SGF sequence instead of traversing the board
        last_coords = None
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
        from services.storage import upload_file

        timestamp = int(time.time())
        filename = f"board_restored_{timestamp}.png"

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

        # Format message text based on whether game_id was provided
        if game_id:
            message_text = f"📂 已讀取棋譜 (game_id: {game_id})！目前輪到：{turn_text}"
        else:
            message_text = f"📂 已讀取棋譜！目前輪到：{turn_text}"

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
        # Check if there's a mention
        mention = message.get("mention")
        if (
            not mention
            or not mention.get("mentionees")
            or len(mention["mentionees"]) == 0
        ):
            # No mention, ignore this message
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

    if text in ["help", "幫助", "說明"]:
        request = ReplyMessageRequest(
            reply_token=reply_token, messages=[TextMessage(text=HELP_MESSAGE)]
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if text == "覆盤" or text.lower() == "review":
        await handle_review_command(target_id, reply_token)
        return

    if "悔棋" in text or "undo" in text.lower():
        await handle_undo_move(target_id, reply_token)
        return

    if "讀取" in text or "load" in text.lower():
        # Match "讀取 game_1234567890" or "讀取game_1234567890" or "load game_1234567890" or "loadgame_1234567890"
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

        # Reset game state
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

棋譜已保存到伺服器，後續可執行 "@NTUGOAnalysis 覆盤" 指令進行分析..."""
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
