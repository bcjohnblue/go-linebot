#!/usr/bin/env python3
"""
KataGo 分析腳本
參考 KaTrain 的實現方式，使用 sgfmill 將 SGF 轉換為 KataGo JSON 格式
輸入：SGF 文件
輸出：帶 AI 評論的 SGF 文件
"""

import os
import sys
import json
import subprocess
import uuid
import threading
import time
from pathlib import Path

try:
    from sgfmill import sgf, boards
except ImportError:
    print("Error: sgfmill library not found. Please install it:")
    print("  pip install sgfmill")
    sys.exit(1)


def get_katago_paths():
    """獲取 KataGo 相關路徑"""
    script_dir = Path(__file__).parent.absolute()
    katago_dir = script_dir

    # KataGo 路徑設定
    katago_bin = os.environ.get("KATAGO_BIN", "katago")
    # 配置文件路徑：katago/configs/default.cfg
    default_config = katago_dir / "configs" / "default.cfg"
    katago_config = os.environ.get("KATAGO_CONFIG", str(default_config))
    katago_model = os.environ.get(
        "KATAGO_MODEL",
        str(katago_dir / "models" / "kata1-b28c512nbt-s12192929536-d5655876072.bin.gz"),
    )

    return {
        "bin": katago_bin,
        "config": katago_config,
        "model": katago_model,
        "dir": katago_dir,
    }


def sgf_to_gtp_coord(row, col, board_size):
    """將 SGF 座標轉換為 GTP 座標（如 A1, B2, T19）"""
    # GTP 座標：A=0, B=1, ..., H=7, J=8, ..., T=18 (跳過 I)
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"
    letter = letters[col]
    number = board_size - row  # SGF 從上到下，GTP 從下到上
    return f"{letter}{number}"


def parse_sgf_to_katago_format(sgf_path):
    """
    使用 sgfmill 解析 SGF 文件並轉換為 KataGo 可接受的 JSON 格式

    Returns:
        dict: KataGo 查詢格式的字典
    """
    with open(sgf_path, "rb") as f:
        sgf_game = sgf.Sgf_game.from_bytes(f.read())

    board_size = sgf_game.get_size()
    root = sgf_game.get_root()

    # 獲取規則和 komi
    # sgfmill 的 get() 方法不接受默认值，需要先检查是否存在
    if root.has_property("RU"):
        rules = root.get("RU")
        if isinstance(rules, list):
            rules = rules[0] if rules else "tromp-taylor"
        rules = rules.lower() if isinstance(rules, str) else "tromp-taylor"
    else:
        rules = "tromp-taylor"

    if root.has_property("KM"):
        komi = root.get("KM")
        if isinstance(komi, list):
            komi = float(komi[0]) if komi else 7.5
        else:
            komi = float(komi)
    else:
        komi = 7.5

    # 獲取初始棋子（讓子）
    initial_stones = []
    if root.has_property("AB"):  # Add Black
        for point in root.get("AB"):
            row, col = point
            initial_stones.append(["B", sgf_to_gtp_coord(row, col, board_size)])
    if root.has_property("AW"):  # Add White
        for point in root.get("AW"):
            row, col = point
            initial_stones.append(["W", sgf_to_gtp_coord(row, col, board_size)])

    # 獲取所有走子
    moves = []
    board = boards.Board(board_size)
    main_sequence = sgf_game.get_main_sequence()

    for node in main_sequence:
        move = node.get_move()
        if move is not None:
            color, point = move
            if point is not None:
                row, col = point
                gtp_coord = sgf_to_gtp_coord(row, col, board_size)
                color_str = "B" if color == "b" else "W"
                moves.append([color_str, gtp_coord])

    # 構建 KataGo 查詢格式
    query = {
        "id": str(uuid.uuid4()),
        "moves": moves,
        "rules": rules.lower() if isinstance(rules, str) else "tromp-taylor",
        "komi": komi,
        "boardXSize": board_size,
        "boardYSize": board_size,
        "maxVisits": 50,  # 會在調用時被覆蓋
        "analyzeTurns": list(range(len(moves) + 1)),  # 分析所有手數
    }

    if initial_stones:
        query["initialStones"] = initial_stones

    return query


def json_to_sgf_with_comments(original_sgf_path, katago_responses, output_path):
    """
    將 KataGo 的 JSON 響應轉換回 SGF 格式，並添加 AI 評論

    Args:
        original_sgf_path: 原始 SGF 文件路徑
        katago_responses: KataGo 返回的 JSON 響應列表
        output_path: 輸出 SGF 文件路徑
    """
    # 讀取原始 SGF 文件
    with open(original_sgf_path, "rb") as f:
        sgf_game = sgf.Sgf_game.from_bytes(f.read())

    # 將響應按 turnNumber 排序
    responses_by_turn = {}
    for response in katago_responses:
        turn = response.get("turnNumber", 0)
        responses_by_turn[turn] = response

    # 遍歷 SGF 節點並添加評論
    main_sequence = sgf_game.get_main_sequence()
    for i, node in enumerate(main_sequence):
        turn_number = i + 1  # turnNumber 從 1 開始（0 是初始局面）
        if turn_number in responses_by_turn:
            response = responses_by_turn[turn_number]
            root_info = response.get("rootInfo", {})
            move_infos = response.get("moveInfos", [])

            # 構建評論
            comment_parts = []

            # 勝率
            winrate = root_info.get("winrate", 0)
            if winrate:
                winrate_pct = winrate * 100
                comment_parts.append(f"Win rate: {winrate_pct:.1f}%")

            # 分數
            score_lead = root_info.get("scoreLead", 0)
            if score_lead is not None:
                comment_parts.append(f"Score: {score_lead:+.1f}")

            # 最佳選點
            if move_infos:
                best_move = move_infos[0]
                best_coord = best_move.get("move", "")
                best_visits = best_move.get("visits", 0)
                if best_coord:
                    comment_parts.append(f"Best: {best_coord} ({best_visits} visits)")

            # PV (變化圖)
            if move_infos:
                pv = []
                for move_info in move_infos[:7]:  # 取前 7 手
                    pv_coord = move_info.get("move", "")
                    if pv_coord:
                        pv.append(pv_coord)
                if pv:
                    comment_parts.append(f"PV: {' '.join(pv)}")

            # 將評論添加到節點
            if comment_parts:
                comment = " | ".join(comment_parts)
                node.set("C", comment)

    # 寫入輸出文件
    with open(output_path, "wb") as f:
        sgf_game.serialise(f)


def analyze_sgf(input_sgf, output_sgf=None, visits=50):
    """
    分析 SGF 文件並生成帶 AI 評論的 SGF 文件
    參考 KaTrain：使用 sgfmill 轉換格式，KataGo 處理 JSON，再轉回 SGF

    Args:
        input_sgf: 輸入 SGF 文件路徑
        output_sgf: 輸出 SGF 文件路徑（可選，默認在 results 目錄）
        visits: 搜索次數（默認 50）

    Returns:
        輸出 SGF 文件路徑
    """
    paths = get_katago_paths()

    # 檢查輸入文件
    input_path = Path(input_sgf)
    if not input_path.exists():
        raise FileNotFoundError(f"Input SGF file not found: {input_sgf}")

    input_sgf_abs = str(input_path.absolute())

    # 設置輸出目錄和文件名
    if output_sgf:
        output_path = Path(output_sgf)
    else:
        output_dir = paths["dir"] / "results"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / (input_path.stem + "_analyzed.sgf")

    print(f"Starting KataGo analysis...")
    print(f"Input:  {input_sgf_abs}")
    print(f"Output: {output_path}")
    print(f"Visits: {visits}")
    print()

    # 步驟 1: 使用 sgfmill 將 SGF 轉換為 KataGo JSON 格式
    print("Step 1: Parsing SGF file with sgfmill...")
    try:
        query = parse_sgf_to_katago_format(input_sgf_abs)
        query["maxVisits"] = visits
    except Exception as e:
        raise RuntimeError(f"Failed to parse SGF file: {e}")

    print(f"  Board size: {query['boardXSize']}x{query['boardYSize']}")
    print(f"  Moves: {len(query['moves'])}")
    print(f"  Rules: {query['rules']}")
    print(f"  Komi: {query['komi']}")
    print()

    # 步驟 2: 發送 JSON 查詢給 KataGo
    print("Step 2: Sending query to KataGo...")
    query_json = json.dumps(query) + "\n"

    cmd = [
        paths["bin"],
        "analysis",
        "-config",
        paths["config"],
        "-model",
        paths["model"],
    ]

    print(f"Running KataGo command: {' '.join(cmd)}")
    print(f"Query: {query_json[:200]}...")  # 显示查询的前200个字符
    print()

    # 使用 Popen 以便实时显示输出
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(paths["dir"]),
        text=True,
        bufsize=1,  # 行缓冲
    )

    # 发送查询
    process.stdin.write(query_json)
    process.stdin.close()

    # 实时读取输出的缓冲区
    stdout_lines = []
    stderr_lines = []
    stdout_lock = threading.Lock()
    stderr_lock = threading.Lock()

    def read_stdout():
        """读取 stdout"""
        for line in iter(process.stdout.readline, ""):
            if line:
                with stdout_lock:
                    stdout_lines.append(line)
        process.stdout.close()

    def read_stderr():
        """读取 stderr"""
        for line in iter(process.stderr.readline, ""):
            if line:
                with stderr_lock:
                    stderr_lines.append(line)
        process.stderr.close()

    # 启动读取线程
    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # 实时显示输出，每秒更新一次
    print("KataGo is processing... (updating every second)")
    print("-" * 60)
    last_stdout_count = 0
    last_stderr_count = 0

    while process.poll() is None:
        time.sleep(1)  # 每秒检查一次

        # 显示新的 stderr 输出（进度信息）
        with stderr_lock:
            if len(stderr_lines) > last_stderr_count:
                new_stderr = stderr_lines[last_stderr_count:]
                for line in new_stderr:
                    print(f"[KataGo] {line.rstrip()}")
                last_stderr_count = len(stderr_lines)

        # 显示新的 stdout 输出（JSON 响应）
        with stdout_lock:
            if len(stdout_lines) > last_stdout_count:
                new_stdout = stdout_lines[last_stdout_count:]
                for line in new_stdout:
                    # 尝试解析 JSON 以显示摘要
                    try:
                        response = json.loads(line.strip())
                        turn = response.get("turnNumber", "?")
                        print(
                            f"[Response] turnNumber={turn}, id={response.get('id', '?')}"
                        )
                    except:
                        print(f"[Output] {line[:100].rstrip()}...")
                last_stdout_count = len(stdout_lines)

        # 显示当前状态
        with stdout_lock, stderr_lock:
            stdout_count = len(stdout_lines)
            stderr_count = len(stderr_lines)
            print(
                f"[Status] stdout: {stdout_count} lines, stderr: {stderr_count} lines",
                end="\r",
            )

    # 等待线程完成
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)

    # 获取所有输出
    with stdout_lock:
        stdout = "".join(stdout_lines)
    with stderr_lock:
        stderr = "".join(stderr_lines)

    print()  # 换行
    print("-" * 60)

    # 显示最终的 stderr 输出
    if stderr:
        print("Final KataGo stderr output:")
        print(stderr)
        print()

    result = subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )

    # 檢查執行結果
    if result.returncode != 0:
        error_msg = f"KataGo analysis failed with return code {result.returncode}"
        if result.stderr:
            error_msg += f"\nStderr: {result.stderr}"
        if result.stdout:
            error_msg += f"\nStdout: {result.stdout}"
        raise RuntimeError(error_msg)

    # 顯示 KataGo 的輸出（用於調試）
    if result.stdout:
        print(f"KataGo stdout length: {len(result.stdout)} characters")
        print(f"First 500 chars of stdout: {result.stdout[:500]}")
        print()

    # 步驟 3: 解析 KataGo 的 JSON 響應
    print("Step 3: Parsing KataGo responses...")
    responses = []
    stdout_lines = result.stdout.strip().split("\n")
    print(f"  Total lines in output: {len(stdout_lines)}")

    for i, line in enumerate(stdout_lines):
        if line.strip():
            try:
                response = json.loads(line)
                responses.append(response)
                if i < 3:  # 顯示前3個響應的摘要
                    turn = response.get("turnNumber", "?")
                    print(f"  Response {i+1}: turnNumber={turn}")
            except json.JSONDecodeError as e:
                print(f"  Warning: Failed to parse line {i+1}: {line[:100]}...")
                print(f"    Error: {e}")
                continue

    if not responses:
        print("Error: No valid JSON responses found in KataGo output")
        print(f"Raw stdout: {result.stdout[:1000]}")
        raise RuntimeError("No valid responses from KataGo")

    print(f"  Successfully parsed {len(responses)} responses")
    print()

    # 步驟 4: 使用 sgfmill 將 JSON 響應轉換回 SGF 並添加評論
    print("Step 4: Converting responses to SGF with AI comments...")
    try:
        json_to_sgf_with_comments(input_sgf_abs, responses, str(output_path))
        print(f"Generated SGF file: {output_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to convert responses to SGF: {e}")

    return str(output_path)


def main():
    """主函數"""
    if len(sys.argv) < 2:
        print("Usage: python analysis.py <input.sgf> [output.sgf] [visits]")
        print("  input.sgf:  輸入 SGF 文件路徑")
        print("  output.sgf: 輸出 SGF 文件路徑（可選）")
        print("  visits:     搜索次數（可選，默認 50）")
        sys.exit(1)

    input_sgf = sys.argv[1]
    output_sgf = sys.argv[2] if len(sys.argv) > 2 else None
    visits = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    try:
        output_path = analyze_sgf(input_sgf, output_sgf, visits)
        print()
        print(f"Analysis completed successfully!")
        print(f"Output SGF file: {output_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
