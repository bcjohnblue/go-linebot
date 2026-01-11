#!/usr/bin/env python3
"""
绘制围棋棋盘并生成 GIF 动画
显示当前棋盘状态、AI 推荐的最佳走子（ai_best）和后续变化（PV）
"""

import json
import sys
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import imageio
import imageio.v2 as imageio_v2
import numpy as np


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


class GoBoard:
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
        new_board = GoBoard(self.size)
        new_board.board = [row[:] for row in self.board]
        new_board.move_history = self.move_history[:]
        return new_board


def build_board_from_moves(moves_data, up_to_move):
    """根据 moves 数据构建到指定手数为止的棋盘状态"""
    board = GoBoard(19)

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
    stone_radius = int(cell_size * 0.4)
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
    board = GoBoard(19)

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
        mp4_path = output_path.replace('.gif', '.mp4')
        try:
            # 使用 imageio 生成 MP4
            # fps=1 表示每秒 1 幀（與 GIF 的 1000ms duration 對應）
            # 最後一幀需要重複 5 次以停留 5 秒
            mp4_frames = frames[:-1] + [frames[-1]] * 5
            
            import imageio
            imageio.mimsave(
                mp4_path,
                [np.array(frame) for frame in mp4_frames],
                fps=1,
                codec='libx264',
                pixelformat='yuv420p',
                output_params=['-movflags', '+faststart']  # 優化串流播放
            )
            print(f"MP4 created: {mp4_path}")
        except Exception as e:
            print(f"Warning: Failed to create MP4 for {output_path}: {e}")
            # 即使 MP4 生成失敗，GIF 仍然可用


def filter_critical_moves(moves, threshold=2.0):
    """过滤出 score_loss 大于阈值的 moves"""

    def get_score_loss(move):
        score_loss = move.get("score_loss")
        return score_loss if score_loss is not None else 0.0

    return [m for m in moves if get_score_loss(m) > threshold]


def get_top_score_loss_moves(moves, top_n=20):
    """获取 score_loss 最高的 top_n 个 moves，并按 move 排序"""

    def get_score_loss(move):
        score_loss = move.get("score_loss")
        return score_loss if score_loss is not None else 0.0

    sorted_moves = sorted(moves, key=get_score_loss, reverse=True)
    top_moves = sorted_moves[:top_n]
    # 按 move 排序
    top_moves.sort(key=lambda x: x.get("move", 0))
    return top_moves


def main():
    """主函数"""
    if len(sys.argv) < 3:
        print("Usage: draw.py <json_file> <output_dir>")
        print("  json_file: JSON 文件路径（包含 moves 数据）")
        print("  output_dir: 输出目录")
        sys.exit(1)

    json_file = sys.argv[1]
    output_dir = sys.argv[2]

    # 读取 JSON 文件
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 获取所有 moves（用于构建棋盘状态）
    all_moves = data.get("moves", [])

    # 过滤并获取 topScoreLossMoves（前 20 个）
    critical_moves = filter_critical_moves(all_moves, threshold=2.0)
    top_moves = get_top_score_loss_moves(critical_moves, top_n=20)

    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 先绘制全局棋盘
    global_board_path = os.path.join(output_dir, "global_board.png")
    draw_global_board(all_moves, global_board_path)

    # 为每个 top move 生成 GIF
    for move_data in top_moves:
        move_number = move_data["move"]
        output_filename = f"move_{move_number}.gif"
        output_path = os.path.join(output_dir, output_filename)

        # 创建 GIF
        create_gif_for_move(move_data, all_moves, output_path)
        print(f"GIF created: {output_path}")


if __name__ == "__main__":
    main()
