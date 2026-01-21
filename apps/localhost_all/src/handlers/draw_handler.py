import os
import sys
import asyncio
import re
import subprocess
from pathlib import Path
from typing import List


async def draw_all_moves_gif(json_file_path: str, output_dir: str = None) -> List[str]:
    """Call Python script to draw GIFs for all topScoreLossMoves"""
    # Get project root
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent
    
    # Default output directory
    if not output_dir:
        output_dir = str(project_root / "draw" / "output")
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract and output folder name
    dir_name = os.path.basename(output_dir)
    print(f"Drawing all moves GIFs to outputDir: {dir_name}")
    
    # Python script path
    draw_dir = project_root / "draw"
    python_script = draw_dir / "draw.py"
    
    if not python_script.exists():
        raise FileNotFoundError(f"Python script not found: {python_script}")
    
    if not os.path.exists(json_file_path):
        raise FileNotFoundError(f"JSON file not found: {json_file_path}")
    
    # Use the current Python interpreter (same as the running process)
    python_executable = sys.executable
    
    # Use Python interpreter to call script
    process = await asyncio.create_subprocess_exec(
        python_executable,
        str(python_script),
        json_file_path,
        output_dir,
        cwd=str(project_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout = b""
    stderr = b""
    
    # Capture stdout
    while True:
        chunk = await process.stdout.read(1024)
        if not chunk:
            break
        stdout += chunk
        output = chunk.decode("utf-8", errors="replace")
        # Real-time output to shell, only show filename
        lines = output.split("\n")
        for line in lines:
            match = re.search(r"GIF created: (.+)", line)
            if match:
                full_path = match.group(1)
                filename = os.path.basename(full_path)
                print(f"GIF created: {filename}")
            elif line.strip():
                # Other output as-is
                print(line)
    
    # Capture stderr
    while True:
        chunk = await process.stderr.read(1024)
        if not chunk:
            break
        stderr += chunk
        # Real-time output to shell
        print(chunk.decode("utf-8", errors="replace"), end="", file=sys.stderr)
    
    return_code = await process.wait()
    
    if return_code == 0:
        # Extract all generated GIF paths from stdout
        stdout_text = stdout.decode("utf-8", errors="replace")
        gif_matches = re.findall(r"GIF created: (.+)", stdout_text)
        return gif_matches
    else:
        raise RuntimeError(
            f"Python script failed with code {return_code}\n{stderr.decode('utf-8', errors='replace')}"
        )

