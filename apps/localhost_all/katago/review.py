#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path

# Get project root directory (parent of katago directory)
current_file = Path(__file__)
katago_dir = current_file.parent
project_root = katago_dir.parent


def resolve_sgf_path(sgf_path: str) -> str:
    """Resolve SGF file path"""
    # If absolute path, return directly
    if os.path.isabs(sgf_path):
        return sgf_path

    # If relative path, try multiple possible locations
    possible_paths = [
        os.path.join(os.getcwd(), sgf_path),  # Relative to current working directory
        str(project_root / sgf_path),  # Relative to project root
        str(katago_dir / sgf_path),  # Relative to katago directory
    ]

    # Find first existing path
    for path in possible_paths:
        if os.path.exists(path):
            return path

    # If all not found, return absolute path relative to project root (let script handle error)
    return str(project_root / sgf_path)


def run_review_script(sgf_path: str, visits: int = None, *additional_args):
    """Run review shell script (full-game analysis for 覆盤)"""
    # Resolve SGF file path
    resolved_sgf_path = resolve_sgf_path(sgf_path)

    # Check if file exists
    if not os.path.exists(resolved_sgf_path):
        raise FileNotFoundError(
            f"SGF file not found: {sgf_path}\nResolved to: {resolved_sgf_path}"
        )

    # Build script path
    script_path = katago_dir / "scripts" / "review.sh"

    # Build arguments: first is resolved SGF file path (use absolute path)
    args = [resolved_sgf_path]

    # Build environment variables (if visits provided, set VISITS environment variable)
    # Also pass OUTPUT_JSONL (if exists in environment variables)
    env = os.environ.copy()
    if visits is not None:
        env["VISITS"] = str(visits)
    # OUTPUT_JSONL will be inherited from parent process's environment variables

    # Execute script and pass arguments
    process = subprocess.Popen(
        ["bash", str(script_path)] + args,
        cwd=str(katago_dir),
        env=env,  # Pass environment variables (including VISITS and OUTPUT_JSONL)
        stdout=sys.stdout,  # Directly inherit stdin/stdout/stderr, let output display in real-time
        stderr=sys.stderr,
        stdin=sys.stdin,
    )

    # Wait for process to complete
    return_code = process.wait()

    if return_code == 0:
        print("覆盤分析完成！")
        return
    else:
        raise RuntimeError(f"覆盤腳本異常結束，代碼: {return_code}")


def main():
    """Main function"""
    # Get command line arguments
    args = sys.argv[1:]

    if len(args) == 0:
        print("錯誤: 請提供 SGF 文件路徑", file=sys.stderr)
        print("使用方式: python3 review.py <sgf_file_path> [additional_args...]", file=sys.stderr)
        print("範例: python3 review.py ./static/example.sgf", file=sys.stderr)
        sys.exit(1)

    sgf_path = args[0]
    # Second argument may be visits (number), otherwise as additional arguments
    visits = None
    additional_args = []

    if len(args) > 1:
        second_arg = args[1]
        # Check if it's a number (visits)
        if second_arg.isdigit():
            visits = int(second_arg)
            additional_args = args[2:]
        else:
            additional_args = args[1:]

    try:
        run_review_script(sgf_path, visits, *additional_args)
        print("現在可以處理接下來的事情了。")
    except Exception as error:
        print(f"執行失敗: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

