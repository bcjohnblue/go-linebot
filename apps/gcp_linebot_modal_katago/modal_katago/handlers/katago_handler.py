import json
import os
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from logger import logger


def jsonl_to_json(jsonl_content: str) -> list:
    """Convert JSONL file content to JSON array"""
    if not jsonl_content or not isinstance(jsonl_content, str):
        return []

    # Split by lines, filter empty lines
    lines = [line.strip() for line in jsonl_content.strip().split("\n") if line.strip()]

    # Parse each line as JSON object
    json_array = []
    for index, line in enumerate(lines):
        try:
            json_array.append(json.loads(line))
        except json.JSONDecodeError as error:
            logger.error(
                f"Error parsing JSONL line {index + 1}: {error}", exc_info=True
            )
            print(f"Line content: {line[:100]}...")

    return json_array


async def read_jsonl_file(file_path: str) -> list:
    """Read JSONL file and convert to JSON array"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonl_to_json(content)
    except Exception as error:
        logger.error(f"Error reading JSONL file {file_path}: {error}", exc_info=True)
        raise


def extract_move_stats(response: dict) -> Optional[dict]:
    """Extract single move statistics from KataGo JSONL response"""
    if not response or not isinstance(response, dict):
        return None

    turn_number = response.get("turnNumber", 0)
    move_number = turn_number + 1  # turnNumber starts from 0, move starts from 1

    root_info = response.get("rootInfo", {})
    move_infos = response.get("moveInfos", [])
    current_player = root_info.get("currentPlayer", "B")

    # Get actual next move (from nextMove and nextMoveColor)
    next_move = response.get("nextMove")
    next_move_color = response.get("nextMoveColor")
    next_root_info = response.get("nextRootInfo", {})

    # winrate_before: win rate before move (current node's win rate)
    # rootInfo.winrate is from current player's perspective (0-1), convert to percentage
    winrate_before = root_info.get("winrate", 0)
    winrate_before_percent = (
        winrate_before * 100 if current_player == "B" else (1 - winrate_before) * 100
    )

    # winrate_after: win rate after move (relative to current player)
    # Prefer nextRootInfo.winrate, if not available get from actual move's moveInfo
    winrate_after = None
    if next_root_info.get("winrate") is not None:
        # Correction: use currentPlayer instead of nextPlayer, keep perspective consistent
        winrate_after = (
            next_root_info["winrate"] * 100
            if current_player == "B"
            else (1 - next_root_info["winrate"]) * 100
        )
    elif next_move and len(move_infos) > 0:
        # If no nextRootInfo, try to get from actual move's moveInfo
        played_move_info = next(
            (m for m in move_infos if m.get("move") == next_move), None
        )
        if played_move_info and played_move_info.get("winrate") is not None:
            # Correction: use currentPlayer instead of nextPlayer, keep perspective consistent
            winrate_after = (
                played_move_info["winrate"] * 100
                if current_player == "B"
                else (1 - played_move_info["winrate"]) * 100
            )

    # Calculate actual move and AI best move
    played_move = None
    ai_best_move = None
    pv = []
    score_loss = None

    if len(move_infos) > 0:
        # Best move is moveInfos[0] (order 0)
        best_move_info = move_infos[0]
        ai_best_move = best_move_info.get("move")
        pv = best_move_info.get("pv", [])

        # If we know the actual move, calculate score_loss
        if next_move and next_move_color:
            played_move = next_move

            # Find actual move in moveInfos
            played_move_info = next(
                (m for m in move_infos if m.get("move") == next_move), None
            )

            if played_move_info:
                # score_loss = best move's scoreLead - actual move's scoreLead
                # Note: scoreLead is from current player's perspective
                best_score = best_move_info.get("scoreLead", 0)
                played_score = played_move_info.get("scoreLead", 0)

                # Calculate score_loss (from current player's perspective)
                if current_player == "B":
                    score_loss = best_score - played_score
                else:
                    # For W, scoreLead sign is opposite
                    score_loss = -best_score - -played_score

                # Ensure score_loss is positive (loss should be positive)
                score_loss = abs(score_loss)
            else:
                # If can't find actual move, use nextScoreGain to estimate
                if response.get("nextScoreGain") is not None:
                    score_loss = abs(response["nextScoreGain"])

    return {
        "move": move_number,
        "color": next_move_color or current_player,
        "played": played_move,
        "ai_best": ai_best_move,
        "pv": pv,
        "winrate_before": round(winrate_before_percent, 1),
        "winrate_after": round(winrate_after, 1) if winrate_after is not None else None,
        "score_loss": round(score_loss, 1) if score_loss is not None else None,
    }


def convert_jsonl_to_move_stats(jsonl_data: list) -> list:
    """Convert JSONL data to format containing statistics"""
    if not isinstance(jsonl_data, list):
        return []

    return [
        stats
        for stats in [extract_move_stats(response) for response in jsonl_data]
        if stats is not None
    ]


async def convert_jsonl_to_move_stats_file(file_path: str) -> dict:
    """Convert JSONL file to format containing statistics"""
    try:
        data = await read_jsonl_file(file_path)
        filename = os.path.basename(file_path)
        moves = convert_jsonl_to_move_stats(data)

        return {"filename": filename, "totalLines": len(data), "moves": moves}
    except Exception as error:
        logger.error(f"Error converting JSONL to move stats: {error}", exc_info=True)
        raise


async def run_katago_analysis(
    sgf_path: str,
    visits: Optional[int] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Execute KataGo analysis script"""
    logger.info(f"Starting KataGo analysis for: {sgf_path}, visits: {visits}")

    # Get current file's directory
    current_file = Path(__file__)
    project_root = current_file.parent.parent
    katago_dir = project_root / "katago"
    review_script = katago_dir / "review.py"

    # Resolve SGF file path
    def resolve_sgf_path(path: str) -> str:
        if os.path.isabs(path):
            return path

        possible_paths = [
            os.path.join(os.getcwd(), path),
            str(project_root / path),
            str(katago_dir / path),
        ]

        for p in possible_paths:
            if os.path.exists(p):
                return p

        return str(project_root / path)

    resolved_sgf_path = resolve_sgf_path(sgf_path)
    logger.info(f"Resolved SGF path: {resolved_sgf_path}")

    # Check if SGF file exists
    if not os.path.exists(resolved_sgf_path):
        error_msg = f"SGF file not found: {sgf_path}\nResolved to: {resolved_sgf_path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    # Check if review.py exists
    if not review_script.exists():
        error_msg = f"Review script not found: {review_script}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    # Generate timestamp (year month day hour minute) for output filename
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M")

    # Build output filename (consistent with review.sh format)
    sgf_basename = os.path.basename(resolved_sgf_path).replace(".sgf", "")
    results_dir = katago_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = (
        results_dir / f"{sgf_basename}_analysis_{timestamp}_{visits or 'default'}.jsonl"
    )
    logger.info(f"Output JSONL file: {output_jsonl}")

    # Build arguments
    args = [str(review_script), resolved_sgf_path]
    if visits:
        args.append(str(visits))
    logger.info(f"Running command: python3 {' '.join(args)}")

    # Build environment variables (pass output filename)
    env = os.environ.copy()
    env["OUTPUT_JSONL"] = str(output_jsonl)
    if visits:
        env["VISITS"] = str(visits)

    # Execute analysis script
    logger.info("Starting KataGo analysis subprocess...")
    process = await asyncio.create_subprocess_exec(
        "python3",
        *args,
        cwd=str(project_root),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout = b""
    stderr = b""

    # Capture stdout and stderr concurrently
    async def read_stdout():
        nonlocal stdout
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                break
            stdout += chunk
            output = chunk.decode("utf-8", errors="replace")
            if on_progress:
                on_progress(output)
            else:
                # If no progress callback, log to logger in real-time
                # Process each line separately for better readability
                for line in output.splitlines():
                    if line.strip():
                        logger.info(f"KataGo: {line.strip()}")

    async def read_stderr():
        nonlocal stderr
        while True:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            stderr += chunk
            output = chunk.decode("utf-8", errors="replace")
            if on_progress:
                on_progress(output)
            else:
                # If no progress callback, log to logger in real-time
                # Process each line separately for better readability
                for line in output.splitlines():
                    if line.strip():
                        logger.warning(f"KataGo stderr: {line.strip()}")

    # Read both streams concurrently
    await asyncio.gather(read_stdout(), read_stderr())

    # Wait for process to complete
    return_code = await process.wait()
    logger.info(f"KataGo analysis process completed with return code: {return_code}")

    if return_code == 0:
        # Analysis successful, use predefined output file path
        jsonl_path = output_jsonl

        move_stats = None
        json_path = None

        # If JSONL file exists, automatically convert to statistics JSON
        if jsonl_path.exists():
            try:
                move_stats = await convert_jsonl_to_move_stats_file(str(jsonl_path))

                # Save moveStats as JSON file (filename with timestamp)
                # e.g., sample-original_analysis_202401011230.json
                jsonl_basename = jsonl_path.stem
                json_dir = jsonl_path.parent
                json_path = json_dir / f"{jsonl_basename}.json"

                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(move_stats, f, indent=2, ensure_ascii=False)

                logger.info(f"Move stats JSON saved: {json_path}")
                logger.info(
                    f"Converted {len(move_stats.get('moves', []))} moves from JSONL"
                )
            except Exception as error:
                logger.warning(
                    f"Warning: Failed to convert JSONL to move stats or save JSON file: {error}",
                    exc_info=True,
                )
                # Don't prevent successful return, just log warning

        return {
            "success": True,
            "sgfPath": resolved_sgf_path,
            "jsonlPath": str(jsonl_path) if jsonl_path.exists() else None,
            "jsonPath": (
                str(json_path) if json_path else None
            ),  # New: saved JSON file path
            "moveStats": move_stats,  # Contains converted statistics
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }
    else:
        error_msg = f"Analysis failed with exit code {return_code}\n{stderr.decode('utf-8', errors='replace')}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)


async def run_katago_analysis_evaluation(
    sgf_path: str,
    current_turn: int,
    visits: Optional[int] = 1000,
) -> Dict[str, Any]:
    """
    使用 evaluation pipeline 對 sgf_path 做 KataGo 分析，
    取得當前盤面的 scoreLead + ownership，並轉成畫圖與文字需要的格式。
    """
    # Get current file's directory
    current_file = Path(__file__)
    project_root = current_file.parent.parent
    katago_dir = project_root / "katago"
    evaluation_script = katago_dir / "evaluation.py"

    # Resolve SGF path (reuse logic from run_katago_analysis)
    def resolve_sgf_path(path: str) -> str:
        if os.path.isabs(path):
            return path
        possible_paths = [
            os.path.join(os.getcwd(), path),
            str(project_root / path),
            str(katago_dir / path),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                return p
        return str(project_root / path)

    resolved_sgf_path = resolve_sgf_path(sgf_path)
    logger.info(f"[evaluation] Resolved SGF path: {resolved_sgf_path}")

    if not os.path.exists(resolved_sgf_path):
        error_msg = f"SGF file not found for evaluation: {sgf_path}\nResolved to: {resolved_sgf_path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    if not evaluation_script.exists():
        error_msg = f"Evaluation script not found: {evaluation_script}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    # 準備輸出 JSONL 路徑（evaluation 專用）
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M")
    results_dir = katago_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    sgf_basename = os.path.basename(resolved_sgf_path).replace(".sgf", "")
    output_jsonl = results_dir / f"{sgf_basename}_evaluation_{timestamp}_{visits or 'default'}.jsonl"
    logger.info(f"[evaluation] Output JSONL file: {output_jsonl}")

    env = os.environ.copy()
    env["OUTPUT_JSONL"] = str(output_jsonl)
    if visits:
        env["VISITS"] = str(visits)

    # 執行 evaluation.py
    try:
        logger.info(f"[evaluation] Starting KataGo evaluation subprocess...")
        process = await asyncio.create_subprocess_exec(
            "python3",
            str(evaluation_script),
            resolved_sgf_path,
            *( [str(visits)] if visits else [] ),
            cwd=str(project_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        return_code = await process.wait()
        logger.info(f"[evaluation] process completed with return code: {return_code}")

        if return_code != 0:
            error_msg = f"KataGo evaluation script failed with exit code {return_code}\n{stderr.decode('utf-8', errors='replace')}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    except Exception as error:
        error_msg = f"Error running evaluation script: {error}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}

    jsonl_path = output_jsonl
    if not jsonl_path or not os.path.exists(jsonl_path):
        error_msg = f"KataGo evaluation JSONL file not found: {jsonl_path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    # 讀取最後一行非空 JSON（只分析最後一手時應該只有一行）
    last_obj: Optional[Dict[str, Any]] = None
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last_obj = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skip invalid JSONL line in evaluation: {e}")
                    continue
    except Exception as error:
        error_msg = f"Failed to read JSONL for evaluation: {error}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}

    if not last_obj:
        error_msg = "No valid analysis result found in JSONL for evaluation"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    root_info = last_obj.get("rootInfo", {}) or {}
    score_lead = root_info.get("scoreLead")
    winrate = root_info.get("winrate")
    current_player = root_info.get("currentPlayer", "B")

    # ownership 可能出現在 rootInfo 或頂層
    ownership_list = root_info.get("ownership")
    if not isinstance(ownership_list, list):
        ownership_list = last_obj.get("ownership")

    if not isinstance(ownership_list, list):
        error_msg = "Ownership array not found in KataGo analysis result"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    board_x = last_obj.get("boardXSize", 19)
    board_y = last_obj.get("boardYSize", 19)
    expected_len = board_x * board_y
    if len(ownership_list) != expected_len:
        logger.warning(
            f"Ownership array length mismatch: expected {expected_len}, got {len(ownership_list)}"
        )

    # 還原成 19x19 矩陣（row-major，自上而下、由左至右）
    ownership_grid = []
    idx = 0
    for _ in range(board_y):
        row_vals = []
        for _ in range(board_x):
            if idx < len(ownership_list):
                try:
                    v = float(ownership_list[idx])
                except (TypeError, ValueError):
                    v = 0.0
            else:
                v = 0.0
            row_vals.append(v)
            idx += 1
        ownership_grid.append(row_vals)

    # 依 threshold=0.5 建立領地矩陣：0=中立,1=黑地,2=白地
    # ownership 是從 KataGo 的 currentPlayer 視角
    threshold = 0.5
    territory_grid = [[0 for _ in range(board_x)] for _ in range(board_y)]

    for r in range(board_y):
        for c in range(board_x):
            v = ownership_grid[r][c]
            if v > threshold:
                territory_grid[r][c] = 1  # 黑地
            elif v < -threshold:
                territory_grid[r][c] = 2  # 白地

    return {
        "success": True,
        "territory": territory_grid,
        "ownership_raw": ownership_grid,
        "currentPlayer": current_player,
        "scoreLead": score_lead,
        "winrate": winrate,
    }


async def run_katago_gtp_next_move(
    sgf_path: str,
    current_turn: int,
    visits: Optional[int] = 1000,
) -> Dict[str, Any]:
    """
    Execute KataGo GTP mode to get next move.
    
    Args:
        sgf_path: Path to SGF file
        current_turn: Current turn (1=black, 2=white)
        visits: Number of visits (optional, uses config default if not provided)
    
    Returns:
        Dict with 'success', 'move' (GTP format like "D4"), and optional 'error'
    """
    import subprocess
    import tempfile
    from pathlib import Path
    
    logger.info(f"Starting KataGo GTP for next move: sgf_path={sgf_path}, current_turn={current_turn}")
    
    # Get current file's directory
    current_file = Path(__file__)
    project_root = current_file.parent.parent
    katago_dir = project_root / "katago"
    config_path = katago_dir / "configs" / "default_gtp.cfg"
    model_path = os.environ.get("KATAGO_MODEL")
    
    if not model_path:
        error_msg = "KATAGO_MODEL environment variable not set"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    if not os.path.exists(model_path):
        error_msg = f"Model file not found: {model_path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    if not config_path.exists():
        error_msg = f"Config file not found: {config_path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    # Determine color for genmove command
    color = "B" if current_turn == 1 else "W"
    
    # Build KataGo command
    katago_cmd = [
        "katago",
        "gtp",
        "-model", model_path,
        "-config", str(config_path),
    ]
    
    if visits:
        katago_cmd.extend(["-override-config", f"maxVisits={visits}"])
    
    logger.info(f"Running KataGo GTP command: {' '.join(katago_cmd)}")
    
    try:
        # Start KataGo process
        process = await asyncio.create_subprocess_exec(
            *katago_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_root),
        )
        
        # Read SGF file content
        with open(sgf_path, "rb") as f:
            sgf_content = f.read()
        
        # Parse SGF to get moves
        from sgfmill import sgf
        sgf_game = sgf.Sgf_game.from_bytes(sgf_content)
        sequence = sgf_game.get_main_sequence()
        
        # Send GTP commands to set up the board
        gtp_commands = []
        
        # Clear board
        gtp_commands.append("boardsize 19\n")
        gtp_commands.append("clear_board\n")
        
        # Play all moves from SGF
        for node in sequence:
            color_move, move = node.get_move()
            if move is not None:
                # Convert SGF coordinates to GTP format
                # SGF: (row, col) where row 0 is bottom (same as GTP)
                # GTP: "A1" to "T19" (skips 'I'), row 1 is bottom
                sgf_row, sgf_col = move
                # Convert column: SGF col 0-18 → GTP A-T (skip I)
                gtp_col = chr(ord('A') + sgf_col)
                if gtp_col >= 'I':
                    gtp_col = chr(ord(gtp_col) + 1)  # Skip 'I'
                # Convert row: SGF row 0-18 (0=bottom) → GTP row 1-19 (1=bottom)
                # No conversion needed, just add 1: SGF row 0 → GTP row 1
                gtp_row = str(sgf_row + 1)
                gtp_move = f"{gtp_col}{gtp_row}"
                
                gtp_color = "B" if color_move == "b" else "W"
                gtp_commands.append(f"play {gtp_color} {gtp_move}\n")
        
        # Get next move
        gtp_commands.append(f"genmove {color}\n")
        gtp_commands.append("quit\n")
        
        # Send all commands
        gtp_input = "".join(gtp_commands)
        logger.debug(f"Sending GTP commands:\n{gtp_input}")
        
        stdout, stderr = await process.communicate(input=gtp_input.encode('utf-8'))
        
        return_code = await process.wait()
        
        stdout_text = stdout.decode('utf-8', errors='replace')
        stderr_text = stderr.decode('utf-8', errors='replace')
        
        logger.info(f"KataGo GTP stdout (first 1000 chars):\n{stdout_text[:1000]}")
        logger.info(f"KataGo GTP stderr (first 1000 chars):\n{stderr_text[:1000]}")
        
        if return_code != 0:
            error_msg = f"KataGo GTP failed with exit code {return_code}\n{stderr_text}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # Parse output to find genmove response
        # GTP response format: "= <move>\n" or "? <error>\n"
        # KataGo outputs responses for each command, we need to find the genmove response
        lines = stdout_text.split('\n')
        move = None
        error_response = None
        
        # Collect all responses (lines starting with = or ?)
        responses = []
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped.startswith('='):
                response_text = line_stripped[1:].strip()
                responses.append(('=', response_text, i))
                logger.debug(f"Found response at line {i}: = {response_text}")
            elif line_stripped.startswith('?'):
                response_text = line_stripped[1:].strip()
                responses.append(('?', response_text, i))
                logger.debug(f"Found error response at line {i}: ? {response_text}")
        
        logger.info(f"Found {len(responses)} GTP responses in output")
        
        # Find the last genmove command position
        last_genmove_line = -1
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if "genmove" in line_stripped.lower() and not line_stripped.startswith('='):
                last_genmove_line = i
                logger.debug(f"Found genmove command at line {i}: {line_stripped}")
        
        # Find the response after the last genmove command
        if last_genmove_line >= 0:
            for resp_type, resp_text, resp_line in responses:
                if resp_line > last_genmove_line:
                    if resp_type == '=':
                        move = resp_text
                        logger.info(f"Found genmove response at line {resp_line}: '{move}'")
                        break
                    elif resp_type == '?':
                        error_response = resp_text
                        logger.error(f"Found genmove error response at line {resp_line}: {error_response}")
                        break
        
        # Fallback: if we didn't find a response after genmove, use the last non-empty = response
        # (genmove should be the last command before quit, so its response should be the last non-empty = response)
        if not move and not error_response and responses:
            # Get the last non-empty = response (should be genmove response, quit returns empty)
            for resp_type, resp_text, resp_line in reversed(responses):
                if resp_type == '=' and resp_text.strip():  # Only use non-empty responses
                    move = resp_text
                    logger.info(f"Using last non-empty = response at line {resp_line} as genmove: '{move}'")
                    break
                elif resp_type == '?':
                    error_response = resp_text
                    logger.error(f"Last response is error at line {resp_line}: {error_response}")
                    break
        
        if error_response:
            return {"success": False, "error": error_response}
        
        if not move:
            error_msg = f"Could not find move in KataGo GTP output. Full stdout:\n{stdout_text}\nFull stderr:\n{stderr_text}"
            logger.error(error_msg)
            return {"success": False, "error": "Could not find move in KataGo GTP output"}
        
        # Handle special moves
        if move.lower() in ["pass", "resign"]:
            logger.warning(f"KataGo returned special move: {move}")
            return {"success": False, "error": f"KataGo returned {move}"}
        
        logger.info(f"KataGo GTP returned move: {move}")
        return {"success": True, "move": move}
        
    except Exception as error:
        error_msg = f"Error running KataGo GTP: {error}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": str(error)}
