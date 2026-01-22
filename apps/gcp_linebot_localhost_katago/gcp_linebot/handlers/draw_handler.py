import os
import sys
import json
import asyncio
from pathlib import Path
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
import imageio
import numpy as np
from handlers.sgf_handler import get_top_winrate_diff_moves


# 围棋坐标转换
def gtp_to_coord(gtp_coord):
    """将 GTP 坐标（如 'Q16'）转换为 (x, y) 坐标（0-18）"""
    if not gtp_coord or len(gtp_coord) < 2:
        return None
    letter = gtp_coord[0].upper()
    number = int(gtp_coord[1:])

    # A-T (跳过 I)
    if letter < "I":
        x = ord(letter) - ord("A")
    else:
        x = ord(letter) - ord("A") - 1

    # 围棋坐标从下往上，需要转换
    y = 19 - number

    if 0 <= x < 19 and 0 <= y < 19:
        return (x, y)
    return None


def coord_to_gtp(x, y):
    """将 (x, y) 坐标转换为 GTP 坐标"""
    if x < 8:
        letter = chr(ord("A") + x)
    else:
        letter = chr(ord("A") + x + 1)  # 跳过 I

    number = 19 - y
    return f"{letter}{number}"


class DrawGoBoard:
    """围棋棋盘类"""

    def __init__(self, size=19):
        self.size = size
        self.board = [[None for _ in range(size)] for _ in range(size)]
        self.move_history = []  # 记录所有走子历史

    def place_stone(self, x, y, color):
        """放置棋子，并处理提子"""
        if not (0 <= x < self.size and 0 <= y < self.size):
            return False

        # 检查位置是否已有棋子
        if self.board[y][x] is not None:
            return False

        # 放置棋子
        self.board[y][x] = color
        self.move_history.append((x, y, color))

        # 检查并移除没有气的对手棋子
        opponent_color = "W" if color == "B" else "B"
        self._remove_captured_stones(x, y, opponent_color)

        # 检查自己刚下的棋子是否也没有气（自杀），如果是则移除
        if not self._has_liberty(x, y, color):
            self.board[y][x] = None
            return False

        return True

    def get_stone(self, x, y):
        """获取棋子颜色"""
        if 0 <= x < self.size and 0 <= y < self.size:
            return self.board[y][x]
        return None

    def _get_neighbors(self, x, y):
        """获取相邻位置"""
        neighbors = []
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.size and 0 <= ny < self.size:
                neighbors.append((nx, ny))
        return neighbors

    def _has_liberty(self, x, y, color):
        """检查一个棋子或一组棋子是否有气"""
        visited = set()
        to_check = [(x, y)]

        while to_check:
            cx, cy = to_check.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))

            # 检查相邻位置
            for nx, ny in self._get_neighbors(cx, cy):
                neighbor = self.board[ny][nx]
                if neighbor is None:
                    # 找到空位，有气
                    return True
                elif neighbor == color and (nx, ny) not in visited:
                    # 同色棋子，继续检查
                    to_check.append((nx, ny))

        # 没有找到空位，没有气
        return False

    def _get_group(self, x, y, color):
        """获取与指定位置相连的同色棋子组"""
        group = set()
        to_check = [(x, y)]
        visited = set()

        while to_check:
            cx, cy = to_check.pop()
            if (cx, cy) in visited:
                continue
            visited.add((cx, cy))

            if self.board[cy][cx] == color:
                group.add((cx, cy))
                # 检查相邻位置
                for nx, ny in self._get_neighbors(cx, cy):
                    if (nx, ny) not in visited:
                        to_check.append((nx, ny))

        return group

    def _remove_captured_stones(self, x, y, opponent_color):
        """移除被吃掉的对手棋子"""
        # 检查相邻的对手棋子组
        for nx, ny in self._get_neighbors(x, y):
            if self.board[ny][nx] == opponent_color:
                # 检查这个对手棋子组是否有气
                if not self._has_liberty(nx, ny, opponent_color):
                    # 没有气，移除整个组
                    group = self._get_group(nx, ny, opponent_color)
                    for gx, gy in group:
                        self.board[gy][gx] = None

    def copy(self):
        """复制棋盘"""
        new_board = DrawGoBoard(self.size)
        new_board.board = [row[:] for row in self.board]
        new_board.move_history = self.move_history[:]
        return new_board


def build_board_from_moves(moves_data, up_to_move):
    """根据 moves 数据构建到指定手数为止的棋盘状态"""
    board = DrawGoBoard(19)

    # 只处理到 up_to_move 之前的走子
    for move_data in moves_data:
        if move_data["move"] < up_to_move:
            played = move_data.get("played")
            if played:
                coord = gtp_to_coord(played)
                if coord:
                    x, y = coord
                    color = move_data.get("color", "B")
                    board.place_stone(x, y, color)

    return board


def draw_board(
    board,
    highlight_move=None,
    highlight_color=None,
    ai_best=None,
    pv_moves=None,
    move_number=None,
    pv_move_numbers=None,  # 新增：PV 步骤的顺序号字典 {坐标: 序号}
):
    """绘制棋盘图像"""
    # 图像尺寸（增加边距以容纳坐标标注）
    img_size = 800
    margin = 50  # 增加边距以容纳坐标标注
    board_size = img_size - 2 * margin
    cell_size = board_size / (board.size - 1)

    # 创建图像
    img = Image.new("RGB", (img_size, img_size), color="#DCB35C")
    draw = ImageDraw.Draw(img)

    # 绘制网格线
    for i in range(board.size):
        x = margin + i * cell_size
        y_start = margin
        y_end = margin + (board.size - 1) * cell_size
        draw.line([(x, y_start), (x, y_end)], fill="black", width=2)

        y = margin + i * cell_size
        x_start = margin
        x_end = margin + (board.size - 1) * cell_size
        draw.line([(x_start, y), (x_end, y)], fill="black", width=2)

    # 绘制星位
    star_points = [
        (3, 3),
        (3, 9),
        (3, 15),
        (9, 3),
        (9, 9),
        (9, 15),
        (15, 3),
        (15, 9),
        (15, 15),
    ]
    for x, y in star_points:
        cx = margin + x * cell_size
        cy = margin + y * cell_size
        draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill="black")

    # 绘制棋子
    stone_radius = int(cell_size * 0.48)
    for y in range(board.size):
        for x in range(board.size):
            stone = board.get_stone(x, y)
            if stone:
                cx = margin + x * cell_size
                cy = margin + y * cell_size

                # 绘制棋子
                if stone == "B":
                    draw.ellipse(
                        [
                            cx - stone_radius,
                            cy - stone_radius,
                            cx + stone_radius,
                            cy + stone_radius,
                        ],
                        fill="black",
                        outline="black",
                        width=2,
                    )
                else:  # W
                    draw.ellipse(
                        [
                            cx - stone_radius,
                            cy - stone_radius,
                            cx + stone_radius,
                            cy + stone_radius,
                        ],
                        fill="white",
                        outline="black",
                        width=2,
                    )

                # 如果这个位置在 PV 序列中，绘制顺序号
                if pv_move_numbers:
                    coord_str = coord_to_gtp(x, y)
                    if coord_str in pv_move_numbers:
                        step_num = pv_move_numbers[coord_str]
                        # 根据棋子颜色选择文字颜色
                        text_color = "white" if stone == "B" else "black"
                        # 绘制数字
                        try:
                            font = ImageFont.truetype(
                                "/System/Library/Fonts/Helvetica.ttc", 16
                            )
                        except:
                            try:
                                font = ImageFont.truetype("arial.ttf", 16)
                            except:
                                font = ImageFont.load_default()

                        # 获取文字尺寸并居中绘制
                        text = str(step_num)
                        bbox = draw.textbbox((0, 0), text, font=font)
                        text_width = bbox[2] - bbox[0]
                        text_height = bbox[3] - bbox[1]
                        text_x = cx - text_width // 2
                        text_y = cy - text_height // 2
                        draw.text((text_x, text_y), text, fill=text_color, font=font)

    # 高亮实际走的走子
    if highlight_move:
        coord = gtp_to_coord(highlight_move)
        if coord:
            x, y = coord
            cx = margin + x * cell_size
            cy = margin + y * cell_size
            # 绘制红色圆圈
            draw.ellipse(
                [
                    cx - stone_radius - 3,
                    cy - stone_radius - 3,
                    cx + stone_radius + 3,
                    cy + stone_radius + 3,
                ],
                outline="red",
                width=3,
            )

    # 高亮 AI 推荐的最佳走子
    if ai_best:
        coord = gtp_to_coord(ai_best)
        if coord:
            x, y = coord
            cx = margin + x * cell_size
            cy = margin + y * cell_size
            # 绘制绿色圆圈
            draw.ellipse(
                [
                    cx - stone_radius - 3,
                    cy - stone_radius - 3,
                    cx + stone_radius + 3,
                    cy + stone_radius + 3,
                ],
                outline="green",
                width=3,
            )
            # 绘制绿色 X
            draw.line(
                [
                    cx - stone_radius,
                    cy - stone_radius,
                    cx + stone_radius,
                    cy + stone_radius,
                ],
                fill="green",
                width=2,
            )
            draw.line(
                [
                    cx - stone_radius,
                    cy + stone_radius,
                    cx + stone_radius,
                    cy - stone_radius,
                ],
                fill="green",
                width=2,
            )

    # 绘制 PV 序列（用不同颜色的小点表示）
    if pv_moves:
        colors = ["blue", "purple", "orange", "cyan", "magenta"]
        for idx, pv_move in enumerate(pv_moves[:5]):  # 最多显示 5 手
            coord = gtp_to_coord(pv_move)
            if coord:
                x, y = coord
                cx = margin + x * cell_size
                cy = margin + y * cell_size
                color = colors[idx % len(colors)]
                # 绘制小点
                dot_radius = 8
                draw.ellipse(
                    [
                        cx - dot_radius,
                        cy - dot_radius,
                        cx + dot_radius,
                        cy + dot_radius,
                    ],
                    fill=color,
                    outline="black",
                    width=1,
                )

    # 添加文字说明
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except:
            font = ImageFont.load_default()

    if move_number:
        text = f"Move {move_number}"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text((img_size - text_width - 10, 10), text, fill="black", font=font)

    # 绘制坐标标注
    # 使用较小的字体
    try:
        coord_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except:
        try:
            coord_font = ImageFont.truetype("arial.ttf", 14)
        except:
            coord_font = ImageFont.load_default()

    # 左侧标注：1~19（从上到下）
    for i in range(board.size):
        y = margin + i * cell_size
        number = 19 - i  # 从 19 到 1
        text = str(number)
        bbox = draw.textbbox((0, 0), text, font=coord_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        # 左侧，垂直居中
        draw.text(
            (margin - text_width - 8, y - text_height // 2),
            text,
            fill="black",
            font=coord_font,
        )

    # 底部标注：A~T（从左到右，跳过 I）
    letters = []
    for i in range(19):
        if i < 8:
            letter = chr(ord("A") + i)  # A-H
        else:
            letter = chr(ord("A") + i + 1)  # J-T (跳过 I)
        letters.append(letter)

    for i in range(board.size):
        x = margin + i * cell_size
        letter = letters[i]
        bbox = draw.textbbox((0, 0), letter, font=coord_font)
        text_width = bbox[2] - bbox[0]
        # 底部，水平居中
        y_bottom = margin + (board.size - 1) * cell_size
        draw.text(
            (x - text_width // 2, y_bottom + 8), letter, fill="black", font=coord_font
        )

    return img


def draw_global_board(all_moves_data, output_path):
    """绘制全局棋盘，每个棋子上标注手数"""
    board = DrawGoBoard(19)

    # 构建完整棋盘状态
    for move_data in all_moves_data:
        played = move_data.get("played")
        if played:
            coord = gtp_to_coord(played)
            if coord:
                x, y = coord
                color = move_data.get("color", "B")
                board.place_stone(x, y, color)

    # 创建手数标注字典 {坐标: 手数}
    move_numbers = {}
    for move_data in all_moves_data:
        played = move_data.get("played")
        move_number = move_data.get("move")
        if played and move_number:
            move_numbers[played] = move_number

    # 绘制棋盘
    img = draw_board(
        board,
        highlight_move=None,
        highlight_color=None,
        ai_best=None,
        pv_moves=None,
        move_number=None,
        pv_move_numbers=move_numbers,  # 使用 move_numbers 来标注手数
    )

    # 保存图像
    img.save(output_path)
    # 只输出文件名，不包含路径
    filename = os.path.basename(output_path)
    print(f"Global board saved: {filename}")


def draw_winrate_chart(all_moves_data, output_path):
    """绘制全局胜率变化图"""
    if not all_moves_data:
        return
    
    # 提取胜率数据（使用 winrate_after，表示每手之后的胜率）
    # winrate_after 是當下那一方的勝率，黑棋直接使用，白棋要用 100 - winrate_after
    moves = []
    winrates = []
    for move_data in all_moves_data:
        move_number = move_data.get("move")
        winrate_after = move_data.get("winrate_after")
        color = move_data.get("color", "B")
        if move_number is not None and winrate_after is not None:
            moves.append(move_number)
            # 如果是黑棋，直接使用 winrate_after；如果是白棋，使用 100 - winrate_after
            if color == "B":
                winrates.append(winrate_after)
            else:  # W
                winrates.append(100 - winrate_after)
    
    if not moves:
        print("No winrate data available for chart")
        return
    
    # 图像尺寸
    img_width = 1200
    img_height = 600
    margin_left = 80
    margin_right = 40
    margin_top = 60
    margin_bottom = 80
    
    # 绘图区域
    chart_width = img_width - margin_left - margin_right
    chart_height = img_height - margin_top - margin_bottom
    
    # 创建图像（棋盘色背景）
    board_bg_color = "#282828"  # 棋盘颜色（米黄色/木色）
    img = Image.new("RGB", (img_width, img_height), color=board_bg_color)
    draw = ImageDraw.Draw(img)
    
    # 加载字体（增大字體）
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
        label_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        tick_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        try:
            title_font = ImageFont.truetype("arial.ttf", 32)
            label_font = ImageFont.truetype("arial.ttf", 20)
            tick_font = ImageFont.truetype("arial.ttf", 16)
        except:
            title_font = ImageFont.load_default()
            label_font = ImageFont.load_default()
            tick_font = ImageFont.load_default()
    
    # 颜色方案（棋盘色背景）
    bg_color = board_bg_color  # 棋盘色背景
    grid_color = "#e0e0e0"  # 网格线颜色（深棕色，在棋盘色背景上可见）
    text_color = "#ffffff"  # 文字颜色（深黑色，在棋盘色背景上更明显）
    title_color = "#ffffff"  # 标题颜色（纯黑色，更明显）
    line_color = "#00AA55"  # 曲线颜色（鲜艳的绿色，在棋盘色背景上更明显）
    point_color = "#008844"  # 数据点颜色（深绿色）
    
    # 绘制标题
    title = "Win Rate"
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text((margin_left, 20), title, fill=title_color, font=title_font)
    
    # 计算坐标范围
    min_move = min(moves)
    max_move = max(moves)
    min_winrate = 0
    max_winrate = 100
    
    # 绘制网格线（水平线，表示胜率）
    for i in range(5):
        y_percent = i / 4.0
        y = margin_top + chart_height * (1 - y_percent)
        winrate_value = min_winrate + (max_winrate - min_winrate) * y_percent
        draw.line(
            [(margin_left, y), (margin_left + chart_width, y)],
            fill=grid_color,
            width=2
        )
        # 绘制Y轴标签
        label = f"{int(winrate_value)}%"
        label_bbox = draw.textbbox((0, 0), label, font=tick_font)
        label_width = label_bbox[2] - label_bbox[0]
        draw.text(
            (margin_left - label_width - 10, y - 8),
            label,
            fill=text_color,
            font=tick_font
        )
    
    # 绘制X轴标签（手数）- 显示 10, 20, 30, ...（不包含 1）
    x_ticks = []
    # 生成 10, 20, 30, ... 这样的标签（從 10 開始）
    tick = 10
    while tick <= max_move:
        if tick >= min_move:
            x_ticks.append(tick)
        tick += 10  # 10, 20, 30, ...
    
    # 创建 move_num 到索引的映射
    move_to_idx = {move_num: idx for idx, move_num in enumerate(moves)}
    
    for move_num in x_ticks:
        if move_num in move_to_idx:
            idx = move_to_idx[move_num]
            x_percent = idx / (len(moves) - 1) if len(moves) > 1 else 0
            x = margin_left + chart_width * x_percent
            label = str(move_num)
            label_bbox = draw.textbbox((0, 0), label, font=tick_font)
            label_width = label_bbox[2] - label_bbox[0]
            draw.text(
                (x - label_width // 2, margin_top + chart_height + 10),
                label,
                fill=text_color,
                font=tick_font
            )
    
    # 绘制平滑胜率曲线
    if len(moves) > 1:
        # 计算原始点坐标
        points = []
        for i, (move_num, winrate) in enumerate(zip(moves, winrates)):
            x_percent = i / (len(moves) - 1) if len(moves) > 1 else 0
            y_percent = (winrate - min_winrate) / (max_winrate - min_winrate) if max_winrate > min_winrate else 0.5
            x = margin_left + chart_width * x_percent
            y = margin_top + chart_height * (1 - y_percent)
            points.append((x, y))
        
        # 使用 Catmull-Rom 插值生成平滑曲线
        def catmull_rom_spline(p0, p1, p2, p3, t):
            """Catmull-Rom 样条插值"""
            t2 = t * t
            t3 = t2 * t
            
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + 
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + 
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + 
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + 
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            return (x, y)
        
        # 生成平滑曲线点
        smooth_points = []
        num_segments = 20  # 每段之间的插值点数
        
        for i in range(len(points) - 1):
            # 获取控制点
            p0 = points[max(0, i - 1)]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[min(len(points) - 1, i + 2)]
            
            # 生成插值点
            for j in range(num_segments):
                t = j / num_segments
                smooth_points.append(catmull_rom_spline(p0, p1, p2, p3, t))
        
        # 添加最后一个点
        if smooth_points:
            smooth_points.append(points[-1])
        
        # 绘制平滑曲线（更粗的線條，在棋盘色背景上更明显）
        if len(smooth_points) > 1:
            for i in range(len(smooth_points) - 1):
                draw.line([smooth_points[i], smooth_points[i + 1]], fill=line_color, width=5)
    else:
        # 只有一个数据点，只绘制線條（不繪製點）
        pass
    
    # 绘制X轴标签
    x_label = "Move"
    x_label_bbox = draw.textbbox((0, 0), x_label, font=label_font)
    x_label_width = x_label_bbox[2] - x_label_bbox[0]
    draw.text(
        (margin_left + chart_width // 2 - x_label_width // 2, img_height - 40),
        x_label,
        fill=text_color,
        font=label_font
    )
    
    # 保存图像
    img.save(output_path)
    filename = os.path.basename(output_path)
    print(f"Winrate chart saved: {filename}")


def create_gif_for_move(move_data, all_moves_data, output_path):
    """为单个 move 创建 GIF 动画"""
    move_number = move_data["move"]
    played = move_data.get("played")
    ai_best = move_data.get("ai_best")
    pv = move_data.get("pv", [])
    color = move_data.get("color", "B")

    # 构建到当前手数之前的棋盘（不包含当前手）
    board = build_board_from_moves(all_moves_data, move_number)

    frames = []

    # 第一帧：当前棋盘状态（走子之前）+ 高亮实际走的走子和 AI 推荐
    img = draw_board(
        board,
        highlight_move=played,
        ai_best=ai_best,
        pv_moves=[],
        move_number=move_number,
    )
    frames.append(img.copy())

    # 第二帧：显示实际走的走子后的棋盘
    board_with_played = board.copy()
    if played:
        coord = gtp_to_coord(played)
        if coord:
            x, y = coord
            board_with_played.place_stone(x, y, color)

    img = draw_board(
        board_with_played,
        highlight_move=played,
        ai_best=ai_best,
        pv_moves=[],
        move_number=f"{move_number} (played)",
    )
    frames.append(img.copy())

    # 后续帧：显示 AI 推荐的 PV 序列
    # PV 的第一步就是 ai_best，所以不需要单独画 ai_best
    if pv and ai_best:
        current_board = board.copy()

        # PV 序列从当前玩家开始（第一步是 ai_best）
        pv_color = color
        # 用于记录 PV 步骤的顺序号 {坐标: 序号}
        pv_move_numbers = {}

        for i, pv_move in enumerate(pv[:10]):  # 最多显示 10 手 PV
            coord = gtp_to_coord(pv_move)
            if coord:
                x, y = coord
                # 放置棋子
                current_board.place_stone(x, y, pv_color)

                # 记录这一步的顺序号（从 1 开始）
                pv_move_numbers[pv_move] = i + 1

                # PV 序列帧不显示右上角文字
                img = draw_board(
                    current_board,
                    highlight_move=None,
                    ai_best=None,  # 不显示绿色框线，因为已经有数字标注了
                    pv_moves=[],
                    move_number=None,  # PV 序列帧不显示文字
                    pv_move_numbers=pv_move_numbers,  # 传递 PV 顺序号
                )
                frames.append(img.copy())

                # 下一步是对手的颜色
                pv_color = "W" if pv_color == "B" else "B"

    # 保存为 GIF（使用 PIL 直接保存，更可靠地控制帧延迟）
    # duration 设置为 1 秒（1000 毫秒），最后一帧停留 5 秒（5000 毫秒）
    if frames:
        # 将 duration 转换为毫秒（PIL 使用毫秒）
        # 1 秒 = 1000 毫秒，最后一帧 5 秒 = 5000 毫秒
        durations = [1000] * (len(frames) - 1) + [5000]  # 最后一帧停留 5 秒

        # 使用 PIL 直接保存 GIF，更可靠地控制帧延迟
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            format="GIF",
        )

        # 同時生成 MP4 版本（用於 LINE video 訊息）
        mp4_path = output_path.replace(".gif", ".mp4")
        try:
            # 使用 imageio 生成 MP4
            # fps=1 表示每秒 1 幀（與 GIF 的 1000ms duration 對應）
            # 最後一幀需要重複 5 次以停留 5 秒
            mp4_frames = frames[:-1] + [frames[-1]] * 5

            imageio.mimsave(
                mp4_path,
                [np.array(frame) for frame in mp4_frames],
                fps=1,
                codec="libx264",
                pixelformat="yuv420p",
                output_params=["-movflags", "+faststart"],  # 優化串流播放
            )
            print(f"MP4 created: {os.path.basename(mp4_path)}")
        except Exception as e:
            print(f"Warning: Failed to create MP4 for {output_path}: {e}")
            # 即使 MP4 生成失敗，GIF 仍然可用


def filter_critical_moves(moves, threshold=2.0):
    """过滤出 score_loss 大于阈值的 moves"""

    def get_score_loss(move):
        score_loss = move.get("score_loss")
        return score_loss if score_loss is not None else 0.0

    return [m for m in moves if get_score_loss(m) > threshold]


async def draw_all_moves_gif(json_file_path: str, output_dir: str) -> List[str]:
    """Call integrated functions to draw GIFs for all topScoreLossMoves"""

    print(f"Drawing all moves GIFs to outputDir: {output_dir}")

    if not os.path.exists(json_file_path):
        raise FileNotFoundError(f"JSON file not found: {json_file_path}")

    # Read JSON file
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Get all moves (for building board state)
    all_moves = data.get("moves", [])

    # Filter and get topScoreLossMoves (top 20)
    top_moves = get_top_winrate_diff_moves(all_moves, top_n=20)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # First draw global board
    global_board_path = os.path.join(output_dir, "global_board.png")
    draw_global_board(all_moves, global_board_path)
    
    # Draw winrate chart
    winrate_chart_path = os.path.join(output_dir, "winrate_chart.png")
    draw_winrate_chart(all_moves, winrate_chart_path)

    # Generate GIF for each top move
    gif_paths = []
    for move_data in top_moves:
        move_number = move_data["move"]
        output_filename = f"move_{move_number}.gif"
        output_path = os.path.join(output_dir, output_filename)

        # Create GIF
        create_gif_for_move(move_data, all_moves, output_path)
        print(f"GIF created: {output_filename}")
        gif_paths.append(output_path)

    return gif_paths
