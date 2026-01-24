import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import config
from logger import logger

# Import handlers
from handlers.line_handler import handle_text_message, handle_file_message
from handlers.sgf_handler import (
    get_top_winrate_diff_moves,
)
from handlers.draw_handler import draw_all_moves_gif
from LLM.providers.openai_provider import call_openai
import asyncio
import json


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    from handlers.line_handler import get_bot_user_id

    # Initialize bot user ID (lazy load, will cache in GCS)
    await get_bot_user_id()

    yield

    # Shutdown
    pass


app = FastAPI(title="Go Line Bot Webhook API", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post(config["server"]["webhook_path"])
async def webhook(request: Request):
    """LINE Webhook handler"""
    try:
        body = await request.json()
        events = body.get("events", [])
        print("events", events)

        for event in events:
            # Handle message events (support 1-on-1, group, room)
            if event.get("type") == "message":
                # Ensure there's a valid source and corresponding ID
                source = event.get("source", {})
                has_valid_source = (
                    (source.get("type") == "user" and source.get("userId"))
                    or (source.get("type") == "group" and source.get("groupId"))
                    or (source.get("type") == "room" and source.get("roomId"))
                )

                if has_valid_source:
                    message = event.get("message", {})
                    if message.get("type") == "text":
                        await handle_text_message(event)
                    elif message.get("type") == "file":
                        await handle_file_message(event)

        return JSONResponse(content="OK", status_code=200)
    except Exception as error:
        logger.error(f"Webhook error: {error}", exc_info=True)
        return JSONResponse(content={"error": "Internal Server Error"}, status_code=500)


@app.get("/health")
async def health():
    """Health check endpoint"""
    from datetime import datetime

    return {"status": "ok", "timestamp": datetime.now().isoformat()}


async def process_review_results(
    task_id: str,
    target_id: str,
    move_stats: dict,
    result_paths: dict,
):
    """Process review results in background: LLM analysis + GIF generation"""
    try:
        # Import here to avoid circular imports
        from handlers.line_handler import send_message
        from linebot.v3.messaging.models import TextMessage, ImageMessage

        # 通知用户覆盤完成，准备进行 LLM 分析
        await send_message(
            target_id,
            None,
            [
                TextMessage(
                    text=f"""✅ KataGo 全盤覆盤完成！

📊 覆盤結果：
• 總手數：{len(move_stats.get('moves', []))}

🤖 接續使用 ChatGPT 分析 20 筆關鍵手數並生成評論，大約需要 1 分鐘...，請稍後再回來查看評論結果。"""
                )
            ],
        )

        # 筛选出前 20 个胜率差距最大的关键手数
        top_moves = get_top_winrate_diff_moves(move_stats["moves"], 20)

        logger.info("Preparing to call OpenAI...")

        # 调用 LLM (OpenAI) 生成评论
        llm_comments = await call_openai(top_moves)
        # llm_comments = []
        logger.info(f"LLM generated {len(llm_comments)} comments")

        # 从回调数据中获取 JSON 文件在 GCS 中的路径
        json_gcs_path = result_paths.get("json_gcs_path")
        if not json_gcs_path:
            logger.warning(f"No json_gcs_path in result_paths: {result_paths}")
            await send_message(
                target_id,
                None,
                [TextMessage(text="❌ 無法取得 KataGo 覆盤結果檔案路徑")],
            )
            return

        # 从 GCS 路径中提取远程路径
        if json_gcs_path.startswith("gs://"):
            gcs_path = json_gcs_path[5:]  # 移除 gs:// 前缀
            parts = gcs_path.split("/", 1)
            if len(parts) == 2:
                _, remote_path = parts
            else:
                # Fallback: 使用正确的路径格式
                remote_path = f"target_{target_id}/reviews/{task_id}.json"
        else:
            # 如果不是 gs:// 格式，假设已经是远程路径
            remote_path = json_gcs_path

        logger.info(f"Remote path: {remote_path}")

        # 从远程路径中提取文件名（用于后续处理）
        json_filename = os.path.basename(remote_path).replace(".json", "")

        # 从 GCS 下载 JSON 文件到临时目录，用于生成 GIF
        from services.storage import download_file
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            json_file_path = temp_path / f"{json_filename}.json"

            # 从 GCS 下载 JSON 文件
            json_content = await download_file(remote_path)
            json_file_path.write_bytes(json_content)

            # 生成 GIF 动画（为每个关键手数生成动画）
            output_dir = temp_path / "gifs"
            output_dir.mkdir(exist_ok=True)
            gif_paths = await draw_all_moves_gif(str(json_file_path), str(output_dir))
            logger.info(f"Generated {len(gif_paths)} GIFs")

            # 将生成的 GIF 上传到 GCS
            from services.storage import upload_file

            gif_map = {}  # 手数 -> GCS 路径的映射
            for gif_path in gif_paths:
                gif_filename = os.path.basename(gif_path)
                # 从文件名中提取手数（例如：move_123.gif -> 123）
                import re

                match = re.search(r"move_(\d+)\.gif", gif_filename)
                if match:
                    move_number = int(match.group(1))
                    gcs_gif_path = (
                        f"target_{target_id}/reviews/{task_id}_{gif_filename}"
                    )
                    await upload_file(gif_path, gcs_gif_path, cache_control="no-cache, max-age=0")
                    gif_map[move_number] = gcs_gif_path
                    logger.info(f"Uploaded GIF to: {gcs_gif_path}")

            # 上传全局棋盘图（如果有的话）
            global_board_path = output_dir / "global_board.png"
            gcs_global_board_path = None
            if global_board_path.exists():
                gcs_global_board_path = (
                    f"target_{target_id}/reviews/{task_id}_global_board.png"
                )
                await upload_file(
                    str(global_board_path),
                    gcs_global_board_path,
                    cache_control="no-cache, max-age=0",
                )
                logger.info(f"Uploaded global board to: {gcs_global_board_path}")

            # 上传胜率图（如果有的话）
            winrate_chart_path = output_dir / "winrate_chart.png"
            gcs_winrate_chart_path = None
            if winrate_chart_path.exists():
                gcs_winrate_chart_path = (
                    f"target_{target_id}/reviews/{task_id}_winrate_chart.png"
                )
                await upload_file(
                    str(winrate_chart_path),
                    gcs_winrate_chart_path,
                    cache_control="no-cache, max-age=0",
                )
                logger.info(f"Uploaded winrate chart to: {gcs_winrate_chart_path}")

            # 发送全局棋盘图和胜率图给用户（合并为一次发送）
            from services.storage import get_public_url
            from handlers.line_handler import is_valid_https_url, encode_url_path

            messages = []
            
            # Add global board if available
            if gcs_global_board_path:
                global_board_url = get_public_url(gcs_global_board_path)
                if is_valid_https_url(global_board_url):
                    messages.extend([
                        TextMessage(text="🗺️ 全盤手順圖："),
                        ImageMessage(
                            original_content_url=global_board_url,
                            preview_image_url=global_board_url,
                        ),
                    ])
            
            # Add winrate chart if available
            if gcs_winrate_chart_path:
                winrate_chart_url = get_public_url(gcs_winrate_chart_path)
                if is_valid_https_url(winrate_chart_url):
                    messages.extend([
                        TextMessage(text="📈 勝率變化圖："),
                        ImageMessage(
                            original_content_url=winrate_chart_url,
                            preview_image_url=winrate_chart_url,
                        ),
                    ])
            
            # Send all messages in one call if any available
            if messages:
                await send_message(target_id, None, messages)

            # 创建评论映射（手数 -> LLM 生成的评论）
            comment_map = {item["move"]: item["comment"] for item in llm_comments}

            # 创建 Flex Message 的 Bubble（用于 Carousel 显示）
            from handlers.line_handler import (
                create_video_preview_bubble,
                create_carousel_flex_message,
            )

            all_bubbles = []  # 可以生成 Bubble 的手数
            fallback_messages = []  # 无法生成 Bubble 的手数（使用文本消息）
            logger.info(f"Top moves: {top_moves}")
            logger.info(f"Gif map: {gif_map}")

            # 为每个关键手数创建 Bubble 或文本消息
            for move in top_moves:
                move_number = move["move"]
                comment = comment_map.get(move_number, "無評論")
                gif_gcs_path = gif_map.get(move_number)

                if gif_gcs_path:
                    try:
                        # 获取 GIF 的公共 URL（用于 LINE 显示）
                        gif_url = get_public_url(gif_gcs_path)

                        # 验证 URL 有效性，然后创建 Bubble（只使用 GIF URL）
                        if is_valid_https_url(gif_url):
                            bubble = create_video_preview_bubble(
                                move_number,
                                move["color"],
                                move["played"],
                                comment,
                                gif_url,
                                winrate_before=move.get("winrate_before"),
                                winrate_after=move.get("winrate_after"),
                                score_loss=move.get("score_loss"),
                            )
                            all_bubbles.append(bubble)
                        else:
                            # URL 无效，使用文本消息作为后备
                            fallback_messages.append(
                                {
                                    "moveNumber": move_number,
                                    "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}\n\n⚠️ 影片連結無效",
                                }
                            )
                    except Exception as flex_error:
                        logger.error(
                            f"Error preparing bubble for move {move_number}: {flex_error}",
                            exc_info=True,
                        )
                        # 发生错误，使用文本消息作为后备
                        fallback_messages.append(
                            {
                                "moveNumber": move_number,
                                "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}",
                            }
                        )
                else:
                    # 没有 GIF，使用文本消息
                    fallback_messages.append(
                        {
                            "moveNumber": move_number,
                            "text": f"📍 第 {move_number} 手（{'黑' if move['color'] == 'B' else '白'}）- {move['played']}\n\n{comment}",
                        }
                    )

            # 分批发送 Carousel Flex Message（LINE 限制每个 Carousel 最多 12 个，这里设为 10 个以确保稳定）
            MAX_BUBBLES_PER_CAROUSEL = 10
            if all_bubbles:
                logger.info(f"Sending {len(all_bubbles)} bubbles in Carousel format")
                from linebot.v3.messaging.models import FlexMessage, FlexContainer

                # 分批处理，每批最多 10 个 Bubble
                for i in range(0, len(all_bubbles), MAX_BUBBLES_PER_CAROUSEL):
                    batch = all_bubbles[i : i + MAX_BUBBLES_PER_CAROUSEL]
                    start_index = i + 1
                    end_index = min(i + len(batch), len(all_bubbles))

                    try:
                        # 创建 Carousel Flex Message
                        carousel_message = create_carousel_flex_message(
                            batch, start_index, len(all_bubbles)
                        )
                        carousel_contents = carousel_message["contents"]
                        flex_container = FlexContainer.from_json(
                            json.dumps(carousel_contents)
                        )
                        flex_message = FlexMessage(
                            alt_text=carousel_message["altText"],
                            contents=flex_container,
                        )
                        await send_message(target_id, None, [flex_message])
                        logger.info(f"Sent Carousel (moves {start_index}-{end_index})")

                        # 避免发送太快，批次之间等待 1 秒
                        if i + MAX_BUBBLES_PER_CAROUSEL < len(all_bubbles):
                            await asyncio.sleep(1)
                    except Exception as carousel_error:
                        logger.error(
                            f"Error sending Carousel: {carousel_error}", exc_info=True
                        )

            # 发送无法生成 Bubble 的手数的文本消息（后备方案）
            if fallback_messages:
                logger.info(f"Sending {len(fallback_messages)} fallback text messages")
                for fallback in fallback_messages:
                    try:
                        await send_message(
                            target_id, None, [TextMessage(text=fallback["text"])]
                        )
                        await asyncio.sleep(0.5)  # 避免发送太快
                    except Exception as fallback_error:
                        logger.error(
                            f"Error sending fallback message: {fallback_error}",
                            exc_info=True,
                        )

    except Exception as error:
        logger.error(
            f"Error in process_review_results for task {task_id}: {error}",
            exc_info=True,
        )


@app.post("/callback/review")
async def callback_review(request: Request):
    """
    接收 Modal 函數完成 KataGo 覆盤后的回调通知

    流程说明：
    1. 用户发送"覆盤"指令 → Cloud Run 立即返回，不等待
    2. Cloud Run 調用 Modal 函數（非同步）
    3. Modal 函數執行 KataGo 覆盤（10-15分钟）
    4. Modal 函數上传结果到 GCS，然后 POST 回调到此端点
    5. 此端点继续处理：LLM 分析 → GIF 生成 → 发送给用户

    请求体格式：
    {
        "task_id": "唯一任务ID",
        "status": "success" | "failed",
        "target_id": "LINE用户ID",
        "result_paths": {
            "json_gcs_path": "gs://bucket/reviews/.../result.json",
            "jsonl_gcs_path": "gs://bucket/reviews/.../result.jsonl"
        },
        "move_stats": { ... }  // 覆盤结果数据（仅 status=success 时）
    }
    """
    try:
        body = await request.json()
        task_id = body.get("task_id")
        status = body.get("status")
        target_id = body.get("target_id")
        result_paths = body.get("result_paths", {})
        move_stats = body.get("move_stats")

        # 验证必需字段
        if not all([task_id, status, target_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: task_id, status, target_id",
            )

        logger.info(f"Received review callback: task_id={task_id}, status={status}")

        # 处理覆盤失败的情况
        if status == "failed":
            error = body.get("error", "Unknown error")
            logger.error(f"Review failed for task {task_id}: {error}")
            # 发送错误消息给用户
            from handlers.line_handler import send_message
            from linebot.v3.messaging.models import TextMessage

            await send_message(
                target_id,
                None,
                [TextMessage(text=f"❌ KataGo 覆盤失敗：{error}")],
            )
            return JSONResponse(content={"status": "received"}, status_code=200)

        if not move_stats:
            logger.warning(f"No move_stats in callback for task {task_id}")
            from handlers.line_handler import send_message
            from linebot.v3.messaging.models import TextMessage

            await send_message(
                target_id,
                None,
                [TextMessage(text="❌ 覆盤完成但無法取得結果數據")],
            )
            return JSONResponse(content={"status": "received"}, status_code=200)

        # 覆盤成功，继续处理后续流程（LLM 分析 + GIF 生成）
        await process_review_results(
            task_id=task_id,
            target_id=target_id,
            move_stats=move_stats,
            result_paths=result_paths,
        )

        # 处理完成后返回响应
        return JSONResponse(
            content={"status": "received", "task_id": task_id}, status_code=200
        )

    except Exception as error:
        logger.error(f"Error in callback endpoint: {error}", exc_info=True)
        return JSONResponse(content={"error": "Internal Server Error"}, status_code=500)


@app.post("/callback/get_ai_next_move")
async def callback_get_ai_next_move(request: Request):
    """
    接收 Modal 函數完成 KataGo GTP 分析后的回调通知（AI 下一手）

    流程说明：
    1. 用户下棋 → Cloud Run 调用 Modal 函数（非同步）
    2. Modal 函数执行 KataGo GTP 获取下一手
    3. Modal 函数 POST 回调到此端点
    4. 此端点更新 SGF 文件，生成图片，发送给用户

    请求体格式：
    {
        "status": "success" | "failed",
        "target_id": "LINE用户ID",
        "move": "D4",  // GTP 格式的位置（仅 status=success 时）
        "current_turn": 1,  // 1=黑, 2=白
        "error": "错误信息"  // 仅 status=failed 时
    }
    """
    try:
        body = await request.json()
        status = body.get("status")
        target_id = body.get("target_id")
        move = body.get("move")
        current_turn = body.get("current_turn")
        reply_token = body.get("reply_token")  # Get reply_token from callback
        user_board_image_url = body.get("user_board_image_url")  # Get user's board image URL

        # 验证必需字段
        if not all([status, target_id]):
            raise HTTPException(
                status_code=400,
                detail="Missing required fields: status, target_id",
            )

        logger.info(f"Received VS AI callback: target_id={target_id}, status={status}, move={move}")

        # 处理失败的情况
        if status == "failed":
            error = body.get("error", "Unknown error")
            logger.error(f"VS AI failed for target {target_id}: {error}")
            # 发送错误消息给用户（如果用户下棋了，也发送用户的棋盘图片）
            from handlers.line_handler import send_message, is_valid_https_url
            from linebot.v3.messaging.models import TextMessage, ImageMessage

            messages = []
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
                messages.append(
                    ImageMessage(
                        original_content_url=user_board_image_url,
                        preview_image_url=user_board_image_url,
                    )
                )
            messages.append(TextMessage(text=f"❌ AI 思考失敗：{error}"))
            await send_message(target_id, reply_token, messages)
            return JSONResponse(content={"status": "received"}, status_code=200)

        if not move:
            logger.warning(f"No move in callback for target {target_id}")
            from handlers.line_handler import send_message, is_valid_https_url
            from linebot.v3.messaging.models import TextMessage, ImageMessage

            messages = []
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
                messages.append(
                    ImageMessage(
                        original_content_url=user_board_image_url,
                        preview_image_url=user_board_image_url,
                    )
                )
            messages.append(TextMessage(text="❌ AI 思考完成但無法取得落子位置"))
            await send_message(target_id, reply_token, messages)
            return JSONResponse(content={"status": "received"}, status_code=200)

        # 更新 SGF 文件并回传图片
        from handlers.line_handler import (
            get_game_state,
            save_game_sgf,
            save_state_to_gcs,
            send_message,
            is_valid_https_url,
        )
        from handlers.go_engine import GoBoard
        from linebot.v3.messaging.models import TextMessage, ImageMessage
        from sgfmill import sgf
        import tempfile
        import time
        from services.storage import upload_file, get_public_url
        from handlers.line_handler import get_game_id

        # Get current game state
        # Note: This should include the user's last move since it was saved in handle_board_move
        # Force reload from GCS to ensure we have the latest state including user's move
        state = await get_game_state(target_id)
        game = state["game"]
        sgf_game = state["sgf_game"]
        
        # Log board state before placing AI's stone for debugging
        total_stones = sum(1 for r in range(19) for c in range(19) if game.board[r][c] != 0)
        logger.info(f"Board state before AI move - total stones: {total_stones}")
        logger.info(f"Current turn from callback: {current_turn}, from state: {state.get('current_turn')}")
        
        # Verify SGF sequence includes user's move
        sequence = sgf_game.get_main_sequence()
        sgf_move_count = sum(1 for node in sequence if node.get_move()[1] is not None)
        logger.info(f"SGF sequence has {sgf_move_count} moves, board has {total_stones} stones")
        
        logger.info(f"KataGo returned GTP move: {move}")
        
        # Parse coordinates first to check if valid
        coords = game.parse_coordinates(move)
        if not coords:
            error_msg = f"Invalid GTP coordinate format: {move}"
            logger.error(error_msg)
            from handlers.line_handler import send_message, is_valid_https_url
            from linebot.v3.messaging.models import TextMessage, ImageMessage
            
            messages = []
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
                messages.append(
                    ImageMessage(
                        original_content_url=user_board_image_url,
                        preview_image_url=user_board_image_url,
                    )
                )
            messages.append(TextMessage(text=f"❌ AI 落子失敗：座標格式錯誤 ({move})"))
            await send_message(target_id, reply_token, messages)
            return JSONResponse(content={"status": "received"}, status_code=200)
        
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
            
            messages = []
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
                messages.append(
                    ImageMessage(
                        original_content_url=user_board_image_url,
                        preview_image_url=user_board_image_url,
                    )
                )
            messages.append(TextMessage(text=f"❌ AI 落子失敗：{msg}"))
            await send_message(target_id, reply_token, messages)
            return JSONResponse(content={"status": "received"}, status_code=200)

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

        # Save SGF file and state metadata
        sgf_path = await save_game_sgf(target_id, state)
        if sgf_path:
            logger.info(f"Saved game SGF after AI move: {sgf_path}")

        # Generate board image
        game_id = await get_game_id(target_id)
        timestamp = int(time.time())
        filename = f"board_ai_{timestamp}.png"

        # Draw board to temporary file
        from handlers.line_handler import visualizer
        
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

        # Send user's board image (if available) and AI's move image together
        if is_valid_https_url(image_url):
            turn_text = "黑" if state["current_turn"] == 1 else "白"
            messages = []
            
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
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
            messages = []
            # If we have user's board image, include it first
            if user_board_image_url and is_valid_https_url(user_board_image_url):
                messages.append(
                    ImageMessage(
                        original_content_url=user_board_image_url,
                        preview_image_url=user_board_image_url,
                    )
                )
            messages.append(
                TextMessage(
                    text=f"🤖 AI 下在 {move}\n\n現在輪到您（{turn_text}）下棋。\n\n⚠️ 圖片 URL 無效"
                )
            )
            await send_message(target_id, reply_token, messages)

        # 处理完成后返回响应
        return JSONResponse(
            content={"status": "received", "target_id": target_id}, status_code=200
        )

    except Exception as error:
        logger.error(f"Error in VS callback endpoint: {error}", exc_info=True)
        return JSONResponse(content={"error": "Internal Server Error"}, status_code=500)


if __name__ == "__main__":
    # 1. 優先從環境變數讀取 PORT (Cloud Run 會給 8080)
    # 2. 如果環境變數不存在 (例如在地端)，則回退到 config 的設定，若 config 也沒有則用 8080
    env_port = os.environ.get("PORT")
    port = int(env_port) if env_port else config.get("server", {}).get("port", 8080)

    # 3. 在生產環境將 reload 設為 False
    #    你可以用一個變數來判斷是否為開發環境
    is_dev = os.environ.get("ENV_MODE") == "development"

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=is_dev)
