import json
import os
import sys
import asyncio
import subprocess
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
    project_root = current_file.parent.parent.parent
    katago_dir = project_root / "katago"
    analysis_script = katago_dir / "analysis.py"

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

    # Check if analysis.py exists
    if not analysis_script.exists():
        error_msg = f"Analysis script not found: {analysis_script}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    # Generate timestamp (year month day hour minute) for output filename
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M")

    # Build output filename (consistent with analysis.sh format)
    sgf_basename = os.path.basename(resolved_sgf_path).replace(".sgf", "")
    results_dir = katago_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = (
        results_dir / f"{sgf_basename}_analysis_{timestamp}_{visits or 'default'}.jsonl"
    )
    logger.info(f"Output JSONL file: {output_jsonl}")

    # Build arguments
    args = [str(analysis_script), resolved_sgf_path]
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
