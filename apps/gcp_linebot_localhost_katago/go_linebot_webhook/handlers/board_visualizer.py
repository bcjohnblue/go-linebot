from PIL import Image
import os
from pathlib import Path


class BoardVisualizer:
    def __init__(self, assets_dir="assets"):
        """
        初始化：載入圖片素材並設定關鍵測量值
        """
        # Get project root and assets path
        current_file = Path(__file__)
        project_root = current_file.parent.parent
        assets_path = project_root / assets_dir

        # --- 1. 載入素材路徑 ---
        board_path = assets_path / "board.png"
        black_path = assets_path / "black.png"
        white_path = assets_path / "white.png"

        # ### 新增：載入 Last Move 素材路徑
        b_last_path = assets_path / "black_lastmove.png"
        w_last_path = assets_path / "white_lastmove.png"

        # 檢查基本檔案是否存在
        if not all(
            p.exists()
            for p in [board_path, black_path, white_path, b_last_path, w_last_path]
        ):
            raise FileNotFoundError(
                "請確保 assets 資料夾中包含 board, black, white 以及 lastmove 系列圖片"
            )

        # 載入圖片
        self.base_img = Image.open(board_path).convert("RGBA")
        self.b_stone_orig = Image.open(black_path).convert("RGBA")
        self.w_stone_orig = Image.open(white_path).convert("RGBA")

        # ### 新增：載入 Last Move 原始圖片
        self.b_last_orig = Image.open(b_last_path).convert("RGBA")
        self.w_last_orig = Image.open(w_last_path).convert("RGBA")

        # --- 2.【關鍵校準參數】(請沿用你校準好的數值) ---
        self.MARGIN_X = 75
        self.MARGIN_Y = 73
        self.GRID_SIZE = 62

        # --- 3. 自動調整大小 ---
        stone_diameter = int(self.GRID_SIZE * 1.08)
        self.stone_size = (stone_diameter, stone_diameter)

        # 縮放普通棋子
        self.b_stone = self.b_stone_orig.resize(self.stone_size, Image.LANCZOS)
        self.w_stone = self.w_stone_orig.resize(self.stone_size, Image.LANCZOS)

        # ### 新增：縮放 Last Move 圖片
        # 假設你的 lastmove 圖片大小跟棋子一樣大 (如果是小的標記圖，邏輯也通)
        self.b_last_img = self.b_last_orig.resize(self.stone_size, Image.LANCZOS)
        self.w_last_img = self.w_last_orig.resize(self.stone_size, Image.LANCZOS)

        self.offset_x = stone_diameter // 2
        self.offset_y = stone_diameter // 2

    def get_pixel_coords(self, r, c):
        """
        輔助函式：將陣列索引 (row, col) 轉為像素座標 (paste_x, paste_y)
        """
        center_x = self.MARGIN_X + (c * self.GRID_SIZE)
        center_y = self.MARGIN_Y + (r * self.GRID_SIZE)
        paste_x = center_x - self.offset_x
        paste_y = center_y - self.offset_y
        return paste_x, paste_y

    def draw_board(
        self, board_state, last_move=None, output_filename="current_board.png"
    ):
        """
        :param board_state: 19x19 二維陣列
        :param last_move: Tuple (row, col) 代表最後一手的位置，若無則傳入 None
        :param output_filename: 檔名
        """
        canvas = self.base_img.copy()
        size = 19

        # 1. 先畫所有「普通」棋子
        for r in range(size):
            for c in range(size):
                stone_color = board_state[r][c]
                if stone_color == 0:
                    continue

                # 如果這個位置是 last_move，我們先跳過不畫？
                # 不，通常建議先畫普通棋子當底，最後再蓋上 last_move 標記比較保險，
                # 除非你的 last_move 圖片本身就是一顆完整的棋子。
                # 這裡我們採取：先畫所有棋子，最後再覆蓋 last_move。

                stone_img = self.b_stone if stone_color == 1 else self.w_stone
                px, py = self.get_pixel_coords(r, c)
                canvas.paste(stone_img, (px, py), stone_img)

        # 2. ### 新增：處理最後一手 (Last Move)
        if last_move is not None:
            lr, lc = last_move
            # 確保座標在範圍內，且該位置真的有棋子 (防呆)
            if 0 <= lr < size and 0 <= lc < size:
                color = board_state[lr][lc]
                if color != 0:
                    # 選擇對應的標記圖片
                    marker_img = self.b_last_img if color == 1 else self.w_last_img

                    # 取得座標
                    px, py = self.get_pixel_coords(lr, lc)

                    # 貼上標記 (這會覆蓋原本畫在該位置的普通棋子)
                    canvas.paste(marker_img, (px, py), marker_img)

        # 儲存
        canvas.save(output_filename, format="PNG")
        return output_filename
