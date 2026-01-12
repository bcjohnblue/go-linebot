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
from handlers.katago_handler import run_katago_analysis
from handlers.sgf_handler import filter_critical_moves, get_top_score_loss_moves
from handlers.draw_handler import draw_all_moves_gif
from LLM.providers.openai_provider import call_openai
from handlers.go_engine import GoBoard
from handlers.board_visualizer import BoardVisualizer

# Initialize LINE Bot API v3
configuration = Configuration(access_token=config["line"]["channel_access_token"])
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
blob_api = MessagingApiBlob(api_client)


current_sgf_file_name: Optional[str] = None
bot_user_id: Optional[str] = None

# Game state management (per user/group/room)
# Key: target_id (userId/groupId/roomId), Value: game state dict
game_states: Dict[str, Dict[str, Any]] = {}

# Game ID management (per target_id)
# Key: target_id, Value: game_id (unique ID for each game session)
game_ids: Dict[str, str] = {}

# Initialize board visualizer (shared instance)
current_file = Path(__file__)
project_root = current_file.parent.parent.parent
assets_dir = project_root / "assets"
visualizer = BoardVisualizer(assets_dir=str(assets_dir))


# Get Bot's own User ID
async def init_bot_user_id():
    global bot_user_id
    try:
        # Run synchronous call in thread pool
        # get_bot_info doesn't require a request object in v3 API
        bot_info = await asyncio.to_thread(line_bot_api.get_bot_info)
        bot_user_id = bot_info.user_id
        logger.info(f"Bot User ID: {bot_user_id}")
    except Exception as error:
        logger.error(f"Failed to get bot info: {error}", exc_info=True)


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
    preview_image_url: str,
    video_url: str,
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

    return {
        "type": "bubble",
        "hero": {
            "type": "image",
            "url": preview_image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {"type": "uri", "uri": video_url, "label": "觀看動畫"},
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
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
                {"type": "separator", "margin": "md"},
                {
                    "type": "text",
                    "text": truncated_comment,
                    "wrap": True,
                    "size": "sm",
                    "margin": "md",
                    "color": "#333333",
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
                        "label": "🎬 觀看動態棋譜",
                        "uri": video_url,
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


HELP_MESSAGE = """歡迎使用圍棋分析 Bot！

📤 上傳 SGF 棋譜檔案，棋譜會被保存到伺服器。

指令：
• help / 幫助 / 說明 - 顯示此說明
• 覆盤 / review - 對最新上傳的棋譜執行 KataGo 分析

🎮 對局功能：
• 輸入座標（如 D4, Q16）- 落子並顯示棋盤
• 悔棋 / undo - 撤銷上一步
• 讀取 / load - 從存檔恢復遊戲
• 重置 / reset - 重置棋盤，開始新遊戲

使用流程：
1️⃣ 上傳 SGF 棋譜檔案
2️⃣ 輸入「覆盤」開始分析
3️⃣ 等待 10-15 分鐘獲得分析結果

或直接輸入座標開始對局！

注意事項：
• 分析使用 KataGo AI（visits=200）
• KataGo 全盤分析約 10 分鐘
• ChatGPT 評論生成約 1 分鐘
• GIF 動畫繪製約 10 秒
• 覆盤功能每次消耗 4 個推播訊息 × 群組人數
• 每月訊息上限為 200 則，請注意使用頻率，超出上限將無法使用覆盤功能"""


async def save_sgf_file(file_buffer: bytes, original_file_name: str) -> Dict[str, str]:
    """Save SGF file to static folder"""
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent
    static_dir = project_root / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    file_path = static_dir / original_file_name

    # Write file
    with open(file_path, "wb") as f:
        f.write(file_buffer)

    return {"fileName": original_file_name, "filePath": str(file_path)}


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
    """Handle review command"""
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent
    static_dir = project_root / "static"
    used_reply_token = False

    try:
        sgf_file_name = current_sgf_file_name
        if not sgf_file_name:
            used_reply_token = await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 找不到棋譜，請先上傳棋譜。")],
            )
            return

        sgf_path = static_dir / sgf_file_name

        # Notify start of analysis (use replyMessage if available)
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

        # Execute KataGo analysis
        print(f"Starting KataGo analysis for: {sgf_path}")
        result = await run_katago_analysis(str(sgf_path), visits=5)

        # Check if analysis was successful
        if not result.get("success"):
            await send_message(
                target_id,
                None,  # replyToken already used or doesn't exist
                [
                    TextMessage(
                        text=f"❌ KataGo 分析失敗：{result.get('stderr', '未知錯誤')}"
                    )
                ],
            )
            return

        # Check if moveStats exists
        if not result.get("moveStats"):
            await send_message(
                target_id, None, [TextMessage(text="❌ 分析完成但無法轉換結果數據")]
            )
            return

        # Analysis successful, notify user
        await send_message(
            target_id,
            None,
            [
                TextMessage(
                    text=f"""✅ KataGo 全盤分析完成！

📊 分析結果：
• 檔案：{sgf_file_name}
• 總手數：{len(result['moveStats']['moves'])}

🤖 接續使用 ChatGPT 分析 20 筆關鍵手數並生成評論，大約需要 1 分鐘...，請稍後再回來查看評論結果。"""
                )
            ],
        )

        # Filter top 20 critical points
        critical_moves = filter_critical_moves(result["moveStats"]["moves"])
        top_score_loss_moves = get_top_score_loss_moves(critical_moves, 20)

        logger.info("Preparing to call OpenAI...")

        # Call LLM to get comments
        llm_comments = await call_openai(top_score_loss_moves)
        # llm_comments = []
        logger.info(f"LLM generated {len(llm_comments)} comments")

        # Use result.jsonPath (full path) instead of result.jsonFilename
        json_file_path = result.get("jsonPath")
        if not json_file_path:
            logger.warning(f"KataGo analysis result: {result}")
            await send_message(
                target_id,
                None,
                [TextMessage(text="❌ 無法取得 KataGo 分析結果檔案路徑")],
            )
            return

        # Extract filename from full path (without extension)
        json_filename = os.path.basename(json_file_path).replace(".json", "")
        output_dir = project_root / "draw" / "outputs" / json_filename

        logger.info(f"JSON file path: {json_file_path}")
        logger.info(f"Output directory: {output_dir}")

        gif_paths = await draw_all_moves_gif(json_file_path, str(output_dir))
        logger.info(f"Generated {len(gif_paths)} GIFs")

        # Create comment mapping (move number -> comment)
        comment_map = {item["move"]: item["comment"] for item in llm_comments}

        # Create GIF mapping (move number -> gif path)
        gif_map = {}
        for path in gif_paths:
            filename = os.path.basename(path)
            match = re.search(r"move_(\d+)\.gif", filename)
            if match:
                gif_map[int(match.group(1))] = path

        # First send global_board.png to let user see full board sequence
        global_board_path = output_dir / "global_board.png"
        public_url = config["server"]["public_url"]

        try:
            if public_url and is_valid_https_url(public_url):
                # Build public URL for full board image
                relative_path = str(global_board_path).split("/draw/outputs/")[1]
                # Encode path to handle spaces and special characters
                encoded_path = encode_url_path(relative_path)
                global_board_url = f"{public_url}/draw/outputs/{encoded_path}"

                # Validate built URL is valid
                if is_valid_https_url(global_board_url):
                    await send_message(
                        target_id,
                        None,
                        [
                            TextMessage(text="🗺️ 全盤手順圖："),
                            ImageMessage(
                                original_content_url=global_board_url,
                                preview_image_url=global_board_url,
                            ),
                        ],
                    )
                else:
                    logger.warning(
                        f"Invalid HTTPS URL for global board: {global_board_url}"
                    )
                    await send_message(
                        target_id,
                        None,
                        [
                            TextMessage(
                                text="🗺️ 全盤手順圖已生成\n\n⚠️ 圖片 URL 無效（必須使用 HTTPS）\n請檢查 PUBLIC_URL 環境變數設定"
                            )
                        ],
                    )
            else:
                logger.warning(f"PUBLIC_URL not set or not HTTPS: {public_url}")
                await send_message(
                    target_id,
                    None,
                    [
                        TextMessage(
                            text="🗺️ 全盤手順圖已生成\n\n⚠️ 未設定有效的 PUBLIC_URL（必須使用 HTTPS）\n請在環境變數中設定 PUBLIC_URL"
                        )
                    ],
                )

            # Wait 1 second before starting to send each move's comment
            await asyncio.sleep(1)
        except Exception as global_board_error:
            print(f"Error sending global board image: {global_board_error}")
            # Even if full board image send fails, continue sending other content

        # Collect all critical moves' bubbles (for merging into Carousel)
        all_bubbles = []
        fallback_messages = (
            []
        )  # Messages that can't generate bubbles (e.g., invalid URL)

        for i, move in enumerate(top_score_loss_moves):
            move_number = move["move"]
            comment = comment_map.get(move_number, "無評論")
            gif_path = gif_map.get(move_number)

            # If there's a GIF, try to create bubble
            if gif_path:
                try:
                    if public_url and is_valid_https_url(public_url):
                        relative_path = gif_path.split("/draw/outputs/")[1]
                        encoded_path = encode_url_path(relative_path)

                        # Replace .gif with .mp4
                        mp4_path = encoded_path.replace(".gif", ".mp4")
                        mp4_url = f"{public_url}/draw/outputs/{mp4_path}"

                        # GIF as preview image
                        gif_url = f"{public_url}/draw/outputs/{encoded_path}"

                        # Validate built URLs are valid
                        if is_valid_https_url(mp4_url) and is_valid_https_url(gif_url):
                            logger.info(f"Creating bubble for move {move_number}")

                            # Create bubble (for Carousel)
                            bubble = create_video_preview_bubble(
                                move_number,
                                move["color"],
                                move["played"],
                                comment,
                                gif_url,
                                mp4_url,
                            )

                            all_bubbles.append(bubble)
                        else:
                            logger.warning(
                                f"Invalid HTTPS URL for move {move_number}: {mp4_url}"
                            )
                            # If URL invalid, record as fallback message
                            fallback_messages.append(
                                {
                                    "moveNumber": move_number,
                                    "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}\n\n⚠️ 影片連結無效",
                                }
                            )
                    else:
                        # If no valid PUBLIC_URL, record as fallback message
                        fallback_messages.append(
                            {
                                "moveNumber": move_number,
                                "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}",
                            }
                        )
                except Exception as flex_error:
                    logger.error(
                        f"Error preparing bubble for move {move_number}: {flex_error}",
                        exc_info=True,
                    )
                    # On error, record as fallback message
                    fallback_messages.append(
                        {
                            "moveNumber": move_number,
                            "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}",
                        }
                    )
            else:
                # If no GIF, record as fallback message
                fallback_messages.append(
                    {
                        "moveNumber": move_number,
                        "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}",
                    }
                )

        # Send Carousel in batches (LINE limits each group to max 12 bubbles, set to 10 for stability)
        MAX_BUBBLES_PER_CAROUSEL = 10
        total_bubbles = len(all_bubbles)

        if total_bubbles > 0:
            logger.info(f"Sending {total_bubbles} bubbles in Carousel format")

            # Process in batches
            for i in range(0, len(all_bubbles), MAX_BUBBLES_PER_CAROUSEL):
                batch = all_bubbles[i : i + MAX_BUBBLES_PER_CAROUSEL]
                start_index = i + 1
                end_index = min(i + len(batch), total_bubbles)

                try:
                    # Create Carousel Flex Message
                    carousel_message = create_carousel_flex_message(
                        batch, start_index, total_bubbles
                    )

                    # Create FlexMessage from carousel_message dict
                    # carousel_message is already in the correct format for FlexMessage
                    # Use from_json to create FlexContainer from the carousel contents
                    carousel_contents = carousel_message["contents"]
                    flex_container = FlexContainer.from_json(
                        json.dumps(carousel_contents)
                    )
                    flex_message = FlexMessage(
                        alt_text=carousel_message["altText"], contents=flex_container
                    )
                    await send_message(target_id, None, [flex_message])

                    logger.info(
                        f"Sent Carousel {i // MAX_BUBBLES_PER_CAROUSEL + 1} (moves {start_index}-{end_index})"
                    )

                    # Avoid sending too fast, wait 1 second
                    if i + MAX_BUBBLES_PER_CAROUSEL < len(all_bubbles):
                        await asyncio.sleep(1)
                except Exception as carousel_error:
                    logger.error(
                        f"Error sending Carousel (moves {start_index}-{end_index}): {carousel_error}",
                        exc_info=True,
                    )

        # Send fallback messages that can't generate bubbles (if any)
        if fallback_messages:
            logger.info(f"Sending {len(fallback_messages)} fallback text messages")
            for fallback in fallback_messages:
                try:
                    await send_message(
                        target_id, None, [TextMessage(text=fallback["text"])]
                    )
                    await asyncio.sleep(0.5)
                except Exception as fallback_error:
                    logger.error(
                        f"Error sending fallback message for move {fallback['moveNumber']}: {fallback_error}",
                        exc_info=True,
                    )
    except Exception as error:
        logger.error(f"Error in 覆盤 command: {error}", exc_info=True)
        await send_message(
            target_id,
            None,
            [TextMessage(text=f"❌ 執行覆盤時發生錯誤：{str(error)}")],
        )


def get_game_id(target_id: str) -> str:
    """Get or create game ID for a target (user/group/room)
    Game ID is a unique identifier for each game session.
    """
    if target_id not in game_ids:
        # Generate new game ID (timestamp-based)
        game_ids[target_id] = f"game_{int(time.time())}"
        logger.info(f"Created new game ID for {target_id}: {game_ids[target_id]}")
    return game_ids[target_id]


def get_game_state(target_id: str) -> Dict[str, Any]:
    """Get or create game state for a target (user/group/room)

    If game state doesn't exist in memory, try to restore from latest SGF file.
    If no SGF file exists, create a new game.
    """
    if target_id not in game_states:
        # Try to restore from SGF file
        restored = restore_game_from_sgf(target_id)
        if restored:
            game_states[target_id] = restored
            # Try to extract game_id from restored SGF file path
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            static_dir = project_root / "static"
            pattern = f"game_{target_id}_*"
            sgf_files = list(static_dir.glob(f"**/{pattern}/*.sgf"))
            if sgf_files:
                # Extract game_id from path: static/{game_id}/game_{target_id}_{timestamp}.sgf
                latest_sgf = max(sgf_files, key=lambda p: p.stat().st_mtime)
                game_id = latest_sgf.parent.name
                game_ids[target_id] = game_id
            logger.info(f"Restored game state for {target_id} from SGF file")
        else:
            # Create new game
            game_states[target_id] = {
                "game": GoBoard(),
                "current_turn": 1,  # 1=黑, 2=白
                "sgf_game": sgf.Sgf_game(size=19),
            }
            # Generate new game ID
            get_game_id(target_id)
            logger.info(f"Created new game state for {target_id}")
    return game_states[target_id]


def restore_game_from_sgf_file(sgf_path: str) -> Optional[Dict[str, Any]]:
    """Restore game state from a specific SGF file path"""
    try:
        # Load SGF file
        with open(sgf_path, "rb") as f:
            sgf_game = sgf.Sgf_game.from_bytes(f.read())

        # Rebuild board state from SGF
        game = GoBoard()
        current_turn = 1  # Start with black
        last_move_coords = None

        # Traverse SGF to rebuild board
        for node in sgf_game.get_main_sequence():
            color, move = node.get_move()
            if move is not None:
                # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
                sgf_r, sgf_c = move

                # Convert to engine coordinates (row 0 is top)
                r = 18 - sgf_r
                c = sgf_c

                last_move_coords = (r, c)
                stone_val = 1 if color == "b" else 2

                # Place stone on board
                game.board[r][c] = stone_val

                # Handle capture logic (simplified - just remove captured stones)
                opponent = 2 if stone_val == 1 else 1
                captured_stones = []
                neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
                for nr, nc in neighbors:
                    if 0 <= nr < 19 and 0 <= nc < 19:
                        if game.board[nr][nc] == opponent:
                            group, libs = game.get_group_and_liberties(nr, nc)
                            if libs == 0:
                                for gr, gc in group:
                                    captured_stones.append((gr, gc))

                # Remove captured stones
                for cr, cc in captured_stones:
                    game.board[cr][cc] = 0

                # Update ko point (simplified)
                my_group, my_libs = game.get_group_and_liberties(r, c)
                if len(captured_stones) == 1 and my_libs == 1:
                    game.ko_point = captured_stones[0]
                else:
                    game.ko_point = None

                # Switch turn
                current_turn = 2 if stone_val == 1 else 1

        return {
            "game": game,
            "current_turn": current_turn,
            "sgf_game": sgf_game,
        }
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


def save_game_sgf(target_id: str) -> Optional[str]:
    """Save current game SGF to file in game-specific folder
    Updates the same SGF file for the same game session (same game_id)
    """
    if target_id not in game_states:
        return None

    state = game_states[target_id]
    sgf_game = state["sgf_game"]

    try:
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"

        # Get or create game ID
        game_id = get_game_id(target_id)

        # Create game-specific folder
        game_dir = static_dir / game_id
        game_dir.mkdir(parents=True, exist_ok=True)

        # Use fixed filename for the same game (no timestamp, so it gets overwritten)
        filename = f"game_{target_id}.sgf"
        file_path = game_dir / filename

        with open(file_path, "wb") as f:
            f.write(sgf_game.serialise())

        logger.info(f"Saved/Updated game SGF to {file_path}")
        return str(file_path)
    except Exception as error:
        logger.error(f"Failed to save game SGF: {error}", exc_info=True)
        return None


def reset_game_state(target_id: str):
    """Reset game state for a target and create new game ID"""
    if target_id in game_states:
        game_states[target_id] = {
            "game": GoBoard(),
            "current_turn": 1,
            "sgf_game": sgf.Sgf_game(size=19),
        }
        # Generate new game ID for new game
        game_ids[target_id] = f"game_{int(time.time())}"
        logger.info(
            f"Reset game state for {target_id}, new game ID: {game_ids[target_id]}"
        )


async def handle_board_move(
    target_id: str, reply_token: Optional[str], coord_text: str, source: Dict[str, Any]
):
    """Handle board coordinate input and draw board"""
    try:
        # Get game state for this target
        state = get_game_state(target_id)
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

        # Save SGF file
        sgf_path = save_game_sgf(target_id)
        if sgf_path:
            logger.info(f"Saved game SGF: {sgf_path}")

        # --- 2. Switch turn and draw board ---
        state["current_turn"] = 2 if current_turn == 1 else 1

        # Generate board image
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"

        # Get game ID and create game-specific folder
        game_id = get_game_id(target_id)
        game_dir = static_dir / game_id
        game_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"board_{target_id}_{timestamp}.png"
        output_path = game_dir / filename

        # Draw board with last move highlighted
        visualizer.draw_board(
            game.board, last_move=coords, output_filename=str(output_path)
        )

        # Get public URL for image
        public_url = config["server"]["public_url"]
        if public_url and is_valid_https_url(public_url):
            # Build image URL (game_id/filename)
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"

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
                            text=f"✅ {msg}\n\n⚠️ 圖片 URL 無效，請檢查 PUBLIC_URL 設定"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            logger.warning(f"PUBLIC_URL not set or invalid: {public_url}")
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"✅ {msg}\n\n⚠️ 未設定有效的 PUBLIC_URL，無法顯示圖片"
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
        if target_id not in game_states:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="目前沒有進行中的對局，無法悔棋。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        state = game_states[target_id]
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

            # Save updated SGF
            save_game_sgf(target_id)

            # Restore game state from updated SGF
            game_id = get_game_id(target_id)
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            static_dir = project_root / "static"
            sgf_path = static_dir / game_id / f"game_{target_id}.sgf"

            if sgf_path.exists():
                restored = restore_game_from_sgf_file(str(sgf_path))
                if restored:
                    game_states[target_id] = restored
                    state = restored
                else:
                    # If restore failed, reset to empty board
                    game_states[target_id] = {
                        "game": GoBoard(),
                        "current_turn": 1,
                        "sgf_game": sgf.Sgf_game(size=19),
                    }
                    state = game_states[target_id]
            else:
                # If SGF doesn't exist, reset to empty board
                game_states[target_id] = {
                    "game": GoBoard(),
                    "current_turn": 1,
                    "sgf_game": sgf.Sgf_game(size=19),
                }
                state = game_states[target_id]

            game = state["game"]
            current_turn = state["current_turn"]

            # Find last move coordinates for highlighting
            last_coords = None
            for r in range(19):
                for c in range(19):
                    if game.board[r][c] != 0:
                        last_coords = (r, c)

            # Draw board
            game_id = get_game_id(target_id)
            game_dir = static_dir / game_id
            game_dir.mkdir(parents=True, exist_ok=True)

            timestamp = int(time.time())
            filename = f"board_undo_{target_id}_{timestamp}.png"
            output_path = game_dir / filename

            visualizer.draw_board(
                game.board, last_move=last_coords, output_filename=str(output_path)
            )

            # Send board image
            public_url = config["server"]["public_url"]
            turn_text = "黑" if current_turn == 1 else "白"

            if public_url and is_valid_https_url(public_url):
                relative_path = f"static/{game_id}/{filename}"
                encoded_path = encode_url_path(relative_path)
                image_url = f"{public_url}/{encoded_path}"

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
            else:
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(
                            text=f"↩️ 已悔棋一步。\n現在輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
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


async def handle_load_game(target_id: str, reply_token: Optional[str]):
    """Handle load game (讀取)"""
    try:
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"

        if not static_dir.exists():
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="找不到存檔。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Find latest SGF file for this target
        pattern = f"**/game_{target_id}.sgf"
        sgf_files = list(static_dir.glob(pattern))

        if not sgf_files:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="找不到存檔。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        # Get the latest file
        latest_sgf = max(sgf_files, key=lambda p: p.stat().st_mtime)

        # Extract game_id from path
        game_id = latest_sgf.parent.name
        game_ids[target_id] = game_id

        # Restore game state
        restored = restore_game_from_sgf_file(str(latest_sgf))
        if not restored:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="讀取失敗：無法解析棋譜檔案。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return

        game_states[target_id] = restored
        state = restored
        game = state["game"]
        current_turn = state["current_turn"]

        # Find last move coordinates for highlighting
        last_coords = None
        for r in range(19):
            for c in range(19):
                if game.board[r][c] != 0:
                    last_coords = (r, c)

        # Draw board
        game_dir = static_dir / game_id
        timestamp = int(time.time())
        filename = f"board_restored_{target_id}_{timestamp}.png"
        output_path = game_dir / filename

        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=str(output_path)
        )

        # Send board image
        public_url = config["server"]["public_url"]
        turn_text = "黑" if current_turn == 1 else "白"

        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"

            if is_valid_https_url(image_url):
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text=f"📂 已讀取棋譜！目前輪到：{turn_text}"),
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
                            text=f"📂 已讀取棋譜！目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜！目前輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
                    )
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling load game: {error}", exc_info=True)
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
        is_bot_mentioned = any(
            mentionee.get("userId") == bot_user_id for mentionee in mentions
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

    if text in ["help", "幫助", "說明"]:
        request = ReplyMessageRequest(
            reply_token=reply_token, messages=[TextMessage(text=HELP_MESSAGE)]
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if text == "覆盤" or text.lower() == "review":
        # Get push target ID
        target_id = (
            source.get("groupId") or source.get("roomId") or source.get("userId")
        )
        # Pass replyToken for initial reply (reduce usage)
        await handle_review_command(target_id, reply_token)
        return

    # Get target ID for game state management
    target_id = source.get("groupId") or source.get("roomId") or source.get("userId")

    # Check if input is a board coordinate (A-T, 1-19)
    # Pattern matches coordinates like "D4", "Q16", etc. (skips 'I')
    coord_pattern = r"^[A-HJ-T]([1-9]|1[0-9])$"
    user_text_upper = text.upper().strip()

    if re.match(coord_pattern, user_text_upper):
        # Handle board coordinate input
        await handle_board_move(target_id, reply_token, user_text_upper, source)
        return

    # Handle other commands (reset, undo, load, etc.)
    if "重置" in text or "reset" in text.lower():
        reset_game_state(target_id)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text="棋盤已重置，黑棋請下。")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    if "悔棋" in text or "undo" in text.lower():
        await handle_undo_move(target_id, reply_token)
        return

    if "讀取" in text or "load" in text.lower():
        await handle_load_game(target_id, reply_token)
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

        # Save file to static folder
        saved_file = await save_sgf_file(file_buffer, file_name)
        global current_sgf_file_name
        current_sgf_file_name = saved_file["fileName"]

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
