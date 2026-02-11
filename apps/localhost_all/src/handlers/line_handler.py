import os
import re
import json
import  time
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
from handlers.katago_handler import run_katago_analysis, run_katago_analysis_evaluation
from handlers.sgf_handler import filter_critical_moves, get_top_winrate_diff_moves
from handlers.draw_handler import draw_all_moves_gif
from LLM.providers.openai_provider import call_openai
from handlers.go_engine import GoBoard
from handlers.board_visualizer import BoardVisualizer

# Initialize LINE Bot API v3 with timeout configuration
configuration = Configuration(access_token=config["line"]["channel_access_token"])
# Set timeout to prevent indefinite hanging (30 seconds)
configuration.timeout = 30
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
blob_api = MessagingApiBlob(api_client)


current_sgf_file_name: Optional[str] = None
bot_user_id: Optional[str] = None
bot_display_name: Optional[str] = None

# Game state management (per user/group/room)
# Key: target_id (userId/groupId/roomId), Value: game state dict
game_states: Dict[str, Dict[str, Any]] = {}

# Game ID management (per target_id)
# Key: target_id, Value: game_id (unique ID for each game session)
game_ids: Dict[str, str] = {}

# VS AI mode management (per target_id)
# Key: target_id, Value: bool (True if VS AI mode is enabled)
vs_ai_modes: Dict[str, bool] = {}

# Initialize board visualizer (shared instance)
current_file = Path(__file__)
project_root = current_file.parent.parent.parent
assets_dir = project_root / "assets"
visualizer = BoardVisualizer(assets_dir=str(assets_dir))


# Get Bot's own User ID
async def init_bot_user_id():
    global bot_user_id, bot_display_name
    try:
        # Run synchronous call in thread pool
        # get_bot_info doesn't require a request object in v3 API
        bot_info = await asyncio.to_thread(line_bot_api.get_bot_info)
        bot_user_id = bot_info.user_id
        bot_display_name = bot_info.display_name
        logger.info(f"Bot User ID: {bot_user_id}, Display Name: {bot_display_name}")
    except Exception as error:
        logger.error(f"Failed to get bot info: {error}", exc_info=True)


async def get_bot_display_name() -> Optional[str]:
    """Get bot display name (cached, initialized by init_bot_user_id)"""
    global bot_display_name
    if bot_display_name is None:
        # If not initialized, try to get it
        try:
            bot_info = await asyncio.to_thread(line_bot_api.get_bot_info)
            bot_display_name = bot_info.display_name
            logger.debug(f"Bot Display Name: {bot_display_name}")
        except Exception as error:
            logger.error(f"Failed to get bot info: {error}", exc_info=True)
            return None
    return bot_display_name


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
            "url": preview_image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {"type": "uri", "uri": video_url, "label": "觀看動畫"},
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


HELP_MESSAGE = """歡迎使用圍棋 Line Bot！

📋 指令列表：
• help / 幫助 / 說明 - 顯示此說明

🎮 對局功能：
• 輸入座標（如 D4, Q16）- 落子並顯示棋盤
• 悔棋 / undo - 撤銷上一步
• 讀取 / load - 從存檔恢復當前遊戲
• 讀取 game_1234567890 / load game_1234567890 - 讀取指定 game_id 的棋譜
• 讀取 game_1234567890 10 / load game_1234567890 10 - 讀取指定 game_id 的前 N 手，並創建新對局
• 重置 / reset - 重置棋盤，開始新遊戲（會保存當前棋譜）
• 投子 - 認輸並結束本局（會先顯示勝負，再重置棋盤）
• 形勢 / 形式 / evaluation - 顯示當前盤面領地分布與目數差距

🤖 AI 對弈功能：
• 對弈 / vs - 查看目前對弈模式狀態
• 對弈 ai / vs ai - 開啟 AI 對弈模式（與 AI 對戰）
• 對弈 free / vs free - 關閉 AI 對弈模式（恢復一般對弈模式）

📊 覆盤分析功能：
• 覆盤 / review - 對最新上傳的棋譜執行 KataGo 覆盤分析

覆盤使用流程：
1️⃣ 上傳 SGF 棋譜檔案
2️⃣ 輸入「覆盤」開始分析
3️⃣ 等待約 10 分鐘獲得分析結果

覆盤分析結果包含：
• 🗺️ 全盤手順圖 - 顯示整局棋的所有手順
• 📈 勝率變化圖 - 顯示黑方勝率隨手數的變化曲線
• 🎬 關鍵手數 GIF 動畫 - 勝率差距最大的前 20 手動態演示
• 💬 ChatGPT 評論 - 針對關鍵手數的評論

技術規格：
• 分析引擎：KataGo AI（visits=1000)
• 分析時間：KataGo 全盤分析約 6 分鐘
• 評論生成：ChatGPT 評論生成約 3 分鐘
• 動畫繪製：GIF 動畫繪製約 10 秒

注意事項：
• 覆盤功能每次消耗 4 個推播訊息 × 群組人數，每月訊息上限為 200 則，請注意使用頻率，超出上限將無法使用覆盤功能"""


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
    from urllib3.exceptions import ReadTimeoutError
    from requests.exceptions import Timeout, ConnectionError
    
    # If there's a replyToken, try to use replyMessage
    if reply_token:
        try:
            # Run synchronous call in thread pool
            request = ReplyMessageRequest(reply_token=reply_token, messages=messages)
            await asyncio.to_thread(line_bot_api.reply_message, request)
            logger.info(f"Sent reply message to {target_id} (message count: {len(messages)})")
            return True  # Successfully used replyMessage
        except (ReadTimeoutError, Timeout, ConnectionError, TimeoutError) as e:
            # Network timeout or connection error
            logger.error(f"Network timeout/connection error when sending reply to {target_id}: {type(e).__name__}: {e}")
            logger.warning("Message delivery failed due to network issues. Please check your internet connection.")
            return False  # Failed to send
        except ApiException as e:
            # replyToken may have expired, fallback to pushMessage
            if e.status in [400, 410]:
                logger.warning(f"replyToken expired or invalid for {target_id}, using pushMessage instead")
            else:
                logger.error(f"Error sending reply message to {target_id}: {e}", exc_info=True)
                raise
        except Exception as e:
            logger.error(f"Unexpected error sending reply message to {target_id}: {type(e).__name__}: {e}", exc_info=True)
            return False

    # Use pushMessage
    try:
        request = PushMessageRequest(to=target_id, messages=messages)
        await asyncio.to_thread(line_bot_api.push_message, request)
        logger.info(f"Sent push message to {target_id} (message count: {len(messages)})")
        return True
    except (ReadTimeoutError, Timeout, ConnectionError, TimeoutError) as e:
        logger.error(f"Network timeout/connection error when sending push message to {target_id}: {type(e).__name__}: {e}")
        logger.warning("Message delivery failed due to network issues. Please check your internet connection.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending push message to {target_id}: {type(e).__name__}: {e}", exc_info=True)
        return False


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
                    text=f"✅ 開始對棋譜：{sgf_file_name} 進行覆盤分析，完成大約需要 10 分鐘...，請稍後再回來查看分析結果。"
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
        # critical_moves = filter_critical_moves(result["moveStats"]["moves"])
        top_score_loss_moves = get_top_winrate_diff_moves(
            result["moveStats"]["moves"], 20
        )

        logger.info("Preparing to call OpenAI...")

        # Call LLM to get comments
        llm_comments = await call_openai(top_score_loss_moves)
        # llm_commentS = []
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
                    # Check if winrate chart exists
                    winrate_chart_path = output_dir / "winrate_chart.png"
                    winrate_chart_url = None
                    if winrate_chart_path.exists():
                        relative_path = str(winrate_chart_path).split("/draw/outputs/")[1]
                        encoded_path = encode_url_path(relative_path)
                        winrate_chart_url = f"{public_url}/draw/outputs/{encoded_path}"
                        if not is_valid_https_url(winrate_chart_url):
                            winrate_chart_url = None
                    
                    # Build messages array
                    messages = [
                        TextMessage(text="🗺️ 全盤手順圖："),
                        ImageMessage(
                            original_content_url=global_board_url,
                            preview_image_url=global_board_url,
                        ),
                    ]
                    
                    # Add winrate chart if available
                    if winrate_chart_url:
                        messages.extend([
                            TextMessage(text="📈 勝率變化圖："),
                            ImageMessage(
                                original_content_url=winrate_chart_url,
                                preview_image_url=winrate_chart_url,
                            ),
                        ])
                    
                    # Send all messages in one call
                    await send_message(target_id, None, messages)
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
                                winrate_before=move.get("winrate_before"),
                                winrate_after=move.get("winrate_after"),
                                score_loss=move.get("score_loss"),
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


async def handle_evaluation_command(target_id: str, reply_token: Optional[str]):
    """Handle shape evaluation command (形勢判斷 / evaluation)"""
    try:
        state = get_game_state(target_id)
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
        sgf_path = save_game_sgf(target_id)
        if not sgf_path:
            await send_message(
                target_id,
                reply_token,
                [TextMessage(text="❌ 無法儲存目前棋局 SGF，請稍後再試。")],
            )
            return

        # 呼叫 KataGo analysis evaluation
        logger.info(
            f"Running KataGo evaluation for target_id={target_id}, sgf_path={sgf_path}"
        )
        result = await run_katago_analysis_evaluation(sgf_path, current_turn)

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

        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"

        game_id = get_game_id(target_id)
        game_dir = static_dir / game_id
        game_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"evaluation_{target_id}_{timestamp}.png"
        output_path = game_dir / filename

        visualizer.draw_board(
            game.board,
            last_move=last_coords,
            output_filename=str(output_path),
            territory=territory,
        )

        public_url = config["server"]["public_url"]
        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"

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

        # 若 PUBLIC_URL 無效或圖片 URL 非 https，僅回文字
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=shape_text + "\n\n⚠️ 無法顯示棋盤圖片，請檢查 PUBLIC_URL 設定。")],
        )
    except Exception as error:
        logger.error(f"Error in 形勢判斷 command: {error}", exc_info=True)
        await send_message(
            target_id,
            reply_token,
            [TextMessage(text=f"❌ 執行形勢判斷時發生錯誤：{str(error)}")],
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


def enable_vs_ai_mode(target_id: str) -> bool:
    """Enable VS AI mode for a target"""
    try:
        vs_ai_modes[target_id] = True
        logger.info(f"Enabled VS AI mode for {target_id}")
        return True
    except Exception as error:
        logger.error(f"Failed to enable VS AI mode for {target_id}: {error}", exc_info=True)
        return False


def disable_vs_ai_mode(target_id: str) -> bool:
    """Disable VS AI mode for a target"""
    try:
        vs_ai_modes[target_id] = False
        logger.info(f"Disabled VS AI mode for {target_id}")
        return True
    except Exception as error:
        logger.error(f"Failed to disable VS AI mode for {target_id}: {error}", exc_info=True)
        return False


def is_vs_ai_mode(target_id: str) -> bool:
    """Check if VS AI mode is enabled for a target"""
    return vs_ai_modes.get(target_id, False)


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
            
        # Check PL property (Player to play)
        if root.has_property("PL"):
            pl = root.get("PL")
            if pl.lower() == "w":
                current_turn = 2
            else:
                current_turn = 1

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
    """Reset game state for a target and create new game ID
    Note: This function does NOT change vs_ai_mode status, which is stored separately
    in the vs_ai_modes dictionary.
    """
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
        
        # Check if VS AI mode is enabled
        vs_ai_mode = is_vs_ai_mode(target_id)
        
        if public_url and is_valid_https_url(public_url):
            # Build image URL (game_id/filename)
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"

            if is_valid_https_url(image_url):
                # If VS AI mode is enabled, don't reply immediately, wait for AI's move
                if vs_ai_mode:
                    # Call local KataGo GTP function asynchronously (non-blocking)
                    # Pass reply_token and user's board image URL so AI handler can send everything together
                    try:
                        # Get current turn (after user's move, it's AI's turn)
                        ai_current_turn = state["current_turn"]
                        
                        # Spawn async task to get AI's next move
                        asyncio.create_task(
                            handle_ai_next_move(
                                target_id=target_id,
                                sgf_path=sgf_path,
                                current_turn=ai_current_turn,
                                reply_token=reply_token,
                                user_board_image_url=image_url,
                            )
                        )
                        logger.info(f"Spawned AI next move task: target_id={target_id}, current_turn={ai_current_turn}")
                        # Don't send reply here, wait for AI to respond
                        return
                    except Exception as ai_error:
                        logger.error(f"Error spawning AI next move task: {ai_error}", exc_info=True)
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


async def handle_one_line_kill_mode(target_id: str, reply_token: Optional[str]):
    """Handle One Line Kill Mode (一線擺滿殺棋模式)"""
    try:
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"
        
        # Define the SGF file path (note: using "bottom_line_game.sgf")
        sgf_path = static_dir / "bottom_line_game.sgf"
        
        if not sgf_path.exists():
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="找不到一線擺滿殺棋模式的棋譜 (bottom_line_game.sgf)。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return
        
        # Restore game state
        restored = restore_game_from_sgf_file(str(sgf_path))
        if not restored:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="讀取失敗：無法解析棋譜檔案。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return
        
        # Create a new unique game_id for this session to avoid conflicts
        game_id = f"onelinekill_{int(time.time())}"
        game_ids[target_id] = game_id
        
        # Save the restored state for this target_id
        game_states[target_id] = restored
        state = restored
        game = state["game"]
        current_turn = state["current_turn"]
        
        # Ensure the game directory exists for saving the board image
        game_dir = static_dir / game_id
        game_dir.mkdir(parents=True, exist_ok=True)
        
        # Build move_numbers dict (empty for initial state or based on SGF)
        # For this mode, we want to show the initial setup from the SGF
        game_states[target_id] = restored
        
        # Draw board
        timestamp = int(time.time())
        filename = f"board_onelinekill_{target_id}_{timestamp}.png"
        output_path = game_dir / filename
        
        # We don't have a "last move" to highlight initially, or we could find one if needed.
        # For a problem setup, usually there is no last move highlighting.
        last_coords = None
        
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
                        TextMessage(text=f"開始一線擺滿殺棋模式！\n目前輪到：{turn_text}"),
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
                            text=f"開始一線擺滿殺棋模式！\n目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"開始一線擺滿殺棋模式！\n目前輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
                    )
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)

    except Exception as error:
        logger.error(f"Error handling One Line Kill Mode: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"處理一線擺滿殺棋模式時發生錯誤：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)


async def handle_load_game_by_id(target_id: str, reply_token: Optional[str], game_id: str):
    """Handle load game by game ID (讀取 {gameid})"""
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
        
        # Find SGF file for this game_id
        sgf_path = static_dir / game_id / f"game_{target_id}.sgf"
        
        if not sgf_path.exists():
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"找不到 game_id 為 {game_id} 的棋譜。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return
        
        # Restore game state
        restored = restore_game_from_sgf_file(str(sgf_path))
        if not restored:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text="讀取失敗：無法解析棋譜檔案。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return
        
        # Update game_id
        game_ids[target_id] = game_id
        
        game_states[target_id] = restored
        state = restored
        game = state["game"]
        current_turn = state["current_turn"]
        
        # Preserve vs_ai_mode state (it's stored separately in vs_ai_modes dict)
        # vs_ai_mode state is already in memory, no need to restore it
        # The state will remain as it was before loading the game
        
        # Find last move coordinates for highlighting and build move_numbers dict
        last_coords = None
        move_numbers = {}  # {(row, col): move_number}
        sgf_game = state["sgf_game"]
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
        game_dir = static_dir / game_id
        timestamp = int(time.time())
        filename = f"board_restored_{target_id}_{timestamp}.png"
        output_path = game_dir / filename
        
        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=str(output_path), move_numbers=move_numbers
        )
        
        # Send board image
        public_url = config["server"]["public_url"]
        turn_text = "黑" if current_turn == 1 else "白"
        total_moves = len(move_numbers)
        total_moves_text = f"總手數：{total_moves} 手"
        
        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"
            
            if is_valid_https_url(image_url):
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text=f"📂 已讀取棋譜 (game_id: {game_id})！\n{total_moves_text}\n目前輪到：{turn_text}"),
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
                            text=f"📂 已讀取棋譜 (game_id: {game_id})！\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜 (game_id: {game_id})！\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
                    )
                ],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
    
    except Exception as error:
        logger.error(f"Error handling load game by ID: {error}", exc_info=True)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=f"讀取失敗：{str(error)}")],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)


async def handle_load_game_by_id_with_moves(
    target_id: str, reply_token: Optional[str], source_game_id: str, move_count: int
):
    """Handle load game by game ID with move count (讀取 {gameid} {手數})
    
    This function:
    1. Loads the SGF file for the specified game_id
    2. Extracts only the first N moves
    3. Creates a new game_id
    4. Saves the truncated SGF file
    5. Updates state to the new game_id
    """
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
        
        # Find SGF file for the source game_id
        source_sgf_path = static_dir / source_game_id / f"game_{target_id}.sgf"
        
        if not source_sgf_path.exists():
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"找不到 game_id 為 {source_game_id} 的棋譜。")],
            )
            await asyncio.to_thread(line_bot_api.reply_message, request)
            return
        
        # Load source SGF file
        with open(source_sgf_path, "rb") as f:
            source_sgf_game = sgf.Sgf_game.from_bytes(f.read())
        
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
        game_ids[target_id] = new_game_id
        
        # Save truncated SGF to new game_id folder
        new_game_dir = static_dir / new_game_id
        new_game_dir.mkdir(parents=True, exist_ok=True)
        new_sgf_path = new_game_dir / f"game_{target_id}.sgf"
        
        with open(new_sgf_path, "wb") as f:
            f.write(truncated_sgf.serialise())
        
        logger.info(f"Created truncated SGF with {move_count} moves: {new_sgf_path}")
        
        # Restore game state from truncated SGF
        restored = restore_game_from_sgf_file(str(new_sgf_path))
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
        
        # Preserve vs_ai_mode state
        # vs_ai_mode state is already in memory, no need to restore it
        
        # Find last move coordinates for highlighting and build move_numbers dict
        last_coords = None
        move_numbers = {}  # {(row, col): move_number}
        sgf_game = state["sgf_game"]
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
        timestamp = int(time.time())
        filename = f"board_restored_{target_id}_{timestamp}.png"
        output_path = new_game_dir / filename
        
        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=str(output_path), move_numbers=move_numbers
        )
        
        # Send board image
        public_url = config["server"]["public_url"]
        turn_text = "黑" if current_turn == 1 else "白"
        total_moves_text = f"總手數：{move_count} 手"
        
        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{new_game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"
            
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
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(
                            text=f"📂 已讀取棋譜 (game_id: {source_game_id}) 前 {move_count} 手！\n新對局 game_id: {new_game_id}\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜 (game_id: {source_game_id}) 前 {move_count} 手！\n新對局 game_id: {new_game_id}\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
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
        
        # Preserve vs_ai_mode state (it's stored separately in vs_ai_modes dict)
        # vs_ai_mode state is already in memory, no need to restore it
        # The state will remain as it was before loading the game

        # Find last move coordinates for highlighting and build move_numbers dict
        last_coords = None
        move_numbers = {}  # {(row, col): move_number}
        sgf_game = state["sgf_game"]
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
        game_dir = static_dir / game_id
        timestamp = int(time.time())
        filename = f"board_restored_{target_id}_{timestamp}.png"
        output_path = game_dir / filename

        visualizer.draw_board(
            game.board, last_move=last_coords, output_filename=str(output_path), move_numbers=move_numbers
        )

        # Send board image
        public_url = config["server"]["public_url"]
        turn_text = "黑" if current_turn == 1 else "白"
        total_moves = len(move_numbers)
        total_moves_text = f"總手數：{total_moves} 手"

        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"

            if is_valid_https_url(image_url):
                request = ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(text=f"📂 已讀取棋譜！\n{total_moves_text}\n目前輪到：{turn_text}"),
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
                            text=f"📂 已讀取棋譜！\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
                await asyncio.to_thread(line_bot_api.reply_message, request)
        else:
            request = ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=f"📂 已讀取棋譜！\n{total_moves_text}\n目前輪到：{turn_text}\n\n⚠️ 未設定有效的 PUBLIC_URL"
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

            # Initialize bot_user_id if not set
            if bot_user_id is None:
                logger.warning("bot_user_id is None, initializing...")
                await init_bot_user_id()

            # Check if bot is mentioned using userId or isSelf field
            is_bot_mentioned = False
            for mentionee in mentions:
                # Check by userId match
                if bot_user_id and mentionee.get("userId") == bot_user_id:
                    is_bot_mentioned = True
                    break
                # Also check isSelf field as fallback (when bot mentions itself)
                if mentionee.get("isSelf", False):
                    is_bot_mentioned = True
                    logger.info(f"Bot mentioned via isSelf field: {mentionee}")
                    break

            if not is_bot_mentioned:
                # Mention is not bot, ignore this message
                logger.warning(
                    f"Mention check failed: bot_user_id={bot_user_id}, "
                    f"mention_userIds={[m.get('userId') for m in mentions]}, "
                    f"isSelf_flags={[m.get('isSelf', False) for m in mentions]}"
                )
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

    if text == "形勢" or text == "形式" or text.lower() == "evaluation":
        target_id = (
            source.get("groupId") or source.get("roomId") or source.get("userId")
        )
        await handle_evaluation_command(target_id, reply_token)
        return

    # Handle One Line Kill Mode
    if text == "一線擺滿殺棋模式":
        target_id = (
            source.get("groupId") or source.get("roomId") or source.get("userId")
        )
        await handle_one_line_kill_mode(target_id, reply_token)
        return

    # Handle Guess First
    if text.startswith("猜先 "):
        parts = text.split()
        if len(parts) >= 3:
            # Join parts to handle names with potential issues, though simple split is requested
            # User request: "猜先 對局者一 對局者二"
            # We take index 1 and 2. 
            # If names have spaces, this simple split might be wrong, but "猜先" usually implies simple names.
            # Let's assume standard usage "猜先 Name1 Name2"
            player1 = parts[1]
            player2 = parts[2]
            target_id = source.get("groupId") or source.get("roomId") or source.get("userId")
            await handle_guess_first_command(target_id, reply_token, player1, player2)
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

    # Handle "對弈" to show current mode status
    if text.lower() in ["對弈", "vs"]:
        # Check current VS AI mode status
        vs_ai_mode = is_vs_ai_mode(target_id)
        state = get_game_state(target_id)
        current_turn = state.get("current_turn", 1)
        
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
• 輸入「對弈 ai」開啟 AI 對弈模式
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

💡 切換模式：
• 輸入「對弈 ai」開啟 AI 對弈模式
• 輸入「對弈 free」切換為一般對弈模式"""
        
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=status_message)],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

    # Handle "對弈 ai" to enable VS AI mode
    if text.lower() in ["對弈 ai", "對弈ai", "vs ai", "vsai"]:
        # Enable VS AI mode
        success = enable_vs_ai_mode(target_id)
        if success:
            # Get current turn to determine AI color
            state = get_game_state(target_id)
            current_turn = state.get("current_turn", 1)
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
        success = disable_vs_ai_mode(target_id)
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
        state = get_game_state(target_id)
        current_turn = state.get("current_turn", 1)
        resign_side = "黑" if current_turn == 1 else "白"
        winner_side = "白" if current_turn == 1 else "黑"
        resign_msg = f"{resign_side}方投子，{winner_side}方獲勝！"
        reset_game_state(target_id)
        request = ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TextMessage(text=resign_msg),
                TextMessage(text="棋盤已重置，黑棋請下。"),
            ],
        )
        await asyncio.to_thread(line_bot_api.reply_message, request)
        return

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
        read_match = re.match(r"(?:讀取|load)\s*(game_\d+)", text, re.IGNORECASE)
        if read_match:
            game_id = read_match.group(1).strip()
            if game_id:
                await handle_load_game_by_id(target_id, reply_token, game_id)
                return
        
        # Load current game (no game_id specified)
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


async def handle_ai_next_move(
    target_id: str,
    sgf_path: str,
    current_turn: int,
    reply_token: Optional[str] = None,
    user_board_image_url: Optional[str] = None,
):
    """Handle AI's next move in VS AI mode (local execution)
    
    Args:
        target_id: Target ID
        sgf_path: Path to SGF file
        current_turn: Current turn (AI's turn)
        reply_token: Reply token from user's move (if available)
        user_board_image_url: User's board image URL (if available)
    """
    try:
        from handlers.katago_handler import run_katago_gtp_next_move
        from linebot.v3.messaging.models import TextMessage, ImageMessage
        from sgfmill import sgf
        import time
        from config import config
        
        logger.info(f"Getting AI's next move: target_id={target_id}, current_turn={current_turn}")
        
        # Run KataGo GTP to get next move
        result = await run_katago_gtp_next_move(
            sgf_path=sgf_path,
            current_turn=current_turn,
            visits=400,
        )
        
        if not result.get("success"):
            error = result.get("error", "Unknown error")
            logger.error(f"KataGo GTP failed: {error}")
            await send_message(
                target_id,
                None,
                [TextMessage(text=f"❌ AI 思考失敗：{error}")],
            )
            return
        
        # Get the move (in GTP format, e.g., "C15")
        move = result.get("move")
        if not move:
            error_msg = "No move returned from KataGo"
            logger.error(f"KataGo GTP error: {error_msg}")
            await send_message(
                target_id,
                None,
                [TextMessage(text="❌ AI 思考完成但無法取得落子位置")],
            )
            return
        
        logger.info(f"KataGo returned GTP move: {move}")
        
        # Get current game state
        state = get_game_state(target_id)
        game = state["game"]
        sgf_game = state["sgf_game"]
        
        # Parse coordinates first to check if valid
        coords = game.parse_coordinates(move)
        if not coords:
            error_msg = f"Invalid GTP coordinate format: {move}"
            logger.error(error_msg)
            await send_message(
                target_id,
                None,
                [TextMessage(text=f"❌ AI 落子失敗：座標格式錯誤 ({move})")],
            )
            return
        
        logger.info(f"Parsed GTP move {move} to coordinates: row={coords[0]}, col={coords[1]}")
        logger.info(f"Board state at ({coords[0]}, {coords[1]}): {game.board[coords[0]][coords[1]]}")
        
        # Place AI's stone (move is in GTP format, parse_coordinates will convert it)
        success, msg = game.place_stone(move, current_turn)
        
        if not success:
            error_msg = f"Failed to place AI's stone: {msg} (move: {move}, coords: {coords})"
            logger.error(error_msg)
            # Log current board state for debugging
            logger.error(f"Current board state around ({coords[0]}, {coords[1]}):")
            for r in range(max(0, coords[0]-1), min(19, coords[0]+2)):
                row_str = f"Row {r}: "
                for c in range(max(0, coords[1]-1), min(19, coords[1]+2)):
                    row_str += f"({r},{c})={game.board[r][c]} "
                logger.error(row_str)
            await send_message(
                target_id,
                None,
                [TextMessage(text=f"❌ AI 落子失敗：{msg}")],
            )
            return
        
        # Update SGF record
        node = sgf_game.get_last_node()
        new_node = node.new_child()
        
        color_code = "b" if current_turn == 1 else "w"
        
        # coords is (row, col), where row 0 is top
        # sgfmill thinks row 0 is bottom, so flip: (19 - 1 - row)
        sgf_row = 18 - coords[0]
        sgf_col = coords[1]
        
        new_node.set_move(color_code, (sgf_row, sgf_col))
        
        # Switch turn (AI's turn is done, now it's user's turn)
        state["current_turn"] = 2 if current_turn == 1 else 1
        
        # Save SGF file
        save_game_sgf(target_id)
        
        # Generate board image
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        static_dir = project_root / "static"
        
        game_id = get_game_id(target_id)
        game_dir = static_dir / game_id
        game_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = int(time.time())
        filename = f"board_ai_{target_id}_{timestamp}.png"
        output_path = game_dir / filename
        
        visualizer.draw_board(
            game.board, last_move=coords, output_filename=str(output_path)
        )
        
        # Get public URL for image
        public_url = config["server"]["public_url"]
        
        # Send AI's move image and prompt for user's next move
        if public_url and is_valid_https_url(public_url):
            relative_path = f"static/{game_id}/{filename}"
            encoded_path = encode_url_path(relative_path)
            image_url = f"{public_url}/{encoded_path}"
            
            if is_valid_https_url(image_url):
                turn_text = "黑" if state["current_turn"] == 1 else "白"
                messages = []
                
                # If we have user's board image, include it first
                if user_board_image_url:
                    messages.append(
                        ImageMessage(
                            original_content_url=user_board_image_url,
                            preview_image_url=user_board_image_url,
                        )
                    )
                
                # Add AI's move
                messages.extend([
                    TextMessage(text=f"🤖 AI 下在 {move}"),
                    ImageMessage(
                        original_content_url=image_url,
                        preview_image_url=image_url,
                    ),
                    TextMessage(text=f"現在輪到您（{turn_text}）下棋。"),
                ])
                await send_message(target_id, reply_token, messages)
            else:
                logger.warning(f"Invalid image URL: {image_url}")
                turn_text = "黑" if state["current_turn"] == 1 else "白"
                await send_message(
                    target_id,
                    None,
                    [
                        TextMessage(
                            text=f"🤖 AI 下在 {move}\n\n現在輪到您（{turn_text}）下棋。\n\n⚠️ 圖片 URL 無效"
                        )
                    ],
                )
        else:
            turn_text = "黑" if state["current_turn"] == 1 else "白"
            await send_message(
                target_id,
                None,
                [
                    TextMessage(
                        text=f"🤖 AI 下在 {move}\n\n現在輪到您（{turn_text}）下棋。\n\n⚠️ 未設定有效的 PUBLIC_URL"
                    )
                ],
            )
        
    except Exception as error:
        logger.error(f"Error in handle_ai_next_move: {error}", exc_info=True)
        await send_message(
            target_id,
            None,
            [TextMessage(text=f"❌ AI 思考時發生錯誤：{str(error)}")],
        )
