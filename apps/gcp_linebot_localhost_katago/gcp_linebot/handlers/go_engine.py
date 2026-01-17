import sys


class GoBoard:
    def __init__(self, size=19):
        self.size = size
        # 0: 空, 1: 黑, 2: 白
        self.board = [[0 for _ in range(size)] for _ in range(size)]

        # 定義圍棋座標的橫軸字母 (跳過 'I')
        self.col_labels = "ABCDEFGHJKLMNOPQRST"

        # === 新增：紀錄打劫的禁著點 ===
        # 格式：(row, col) 或 None
        self.ko_point = None

    def display(self):
        """
        在 Console 印出目前棋盤 (除錯用)
        """
        print("   " + " ".join(self.col_labels))
        for r in range(self.size):
            # 圍棋盤面通常 19 在最上面，1 在最下面
            row_label = self.size - r
            row_str = f"{row_label:2d} "
            for c in range(self.size):
                stone = self.board[r][c]
                if stone == 0:
                    char = "."
                elif stone == 1:
                    char = "X"  # 黑棋
                elif stone == 2:
                    char = "O"  # 白棋
                row_str += char + " "
            print(row_str + f"{row_label}")
        print("   " + " ".join(self.col_labels))

    def parse_coordinates(self, text):
        """
        將 LINE 使用者輸入的 "D4", "Q16" 轉換為陣列索引 (row, col)
        """
        text = text.upper().strip()
        if len(text) < 2:
            return None

        # 1. 處理字母 (Column)
        col_char = text[0]
        if col_char not in self.col_labels:
            return None
        col = self.col_labels.index(col_char)

        # 2. 處理數字 (Row)
        try:
            row_num = int(text[1:])
            # 圍棋座標 1 在最下方 (index 18)，19 在最上方 (index 0)
            row = self.size - row_num
        except ValueError:
            return None

        if not (0 <= row < self.size and 0 <= col < self.size):
            return None

        return row, col

    def get_group_and_liberties(self, r, c):
        """
        核心演算法：找出 (r, c) 這顆棋子所在的「整串棋子」以及它們的「氣」。
        回傳: (棋串座標Set, 氣的數量)
        """
        color = self.board[r][c]
        if color == 0:
            return set(), 0

        # 使用 BFS (廣度優先搜尋) 找尋相連同色棋子
        stack = [(r, c)]
        visited_stones = {(r, c)}  # 記錄這個 group 的所有棋子位置
        liberties = set()  # 記錄所有氣的位置 (去重複)

        while stack:
            cur_r, cur_c = stack.pop()

            # 檢查上下左右
            neighbors = [
                (cur_r - 1, cur_c),
                (cur_r + 1, cur_c),
                (cur_r, cur_c - 1),
                (cur_r, cur_c + 1),
            ]

            for nr, nc in neighbors:
                # 邊界檢查
                if 0 <= nr < self.size and 0 <= nc < self.size:
                    neighbor_color = self.board[nr][nc]

                    if neighbor_color == 0:
                        # 這是氣
                        liberties.add((nr, nc))
                    elif neighbor_color == color:
                        # 是同伴，且沒被訪問過，加入搜尋隊列
                        if (nr, nc) not in visited_stones:
                            visited_stones.add((nr, nc))
                            stack.append((nr, nc))

        return visited_stones, len(liberties)

    def place_stone(self, coord_text, color):
        """
        主功能：落子並處理提子
        coord_text: "D4"
        color: 1(黑) 或 2(白)
        回傳: (Boolean 成功與否, String 訊息)
        """
        coords = self.parse_coordinates(coord_text)
        if not coords:
            return False, "座標格式錯誤 (例如: D4, Q16)"

        r, c = coords

        if self.board[r][c] != 0:
            return False, "這裡已經有棋子了"

        # === 1. 檢查是否為打劫禁著點 (Ko) ===
        if self.ko_point == (r, c):
            return False, "打劫：不能立即回提，請先找劫材！"

        # 嘗試落子 (暫時改變狀態)
        self.board[r][c] = color

        captured_stones = []
        opponent = 2 if color == 1 else 1

        # 2. 檢查四周對手棋子是否氣絕 (提子邏輯)
        neighbors = [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
        for nr, nc in neighbors:
            if 0 <= nr < self.size and 0 <= nc < self.size:
                if self.board[nr][nc] == opponent:
                    # 計算對手這串棋子的氣
                    group, libs = self.get_group_and_liberties(nr, nc)
                    if libs == 0:
                        # 氣盡，加入提子名單
                        for gr, gc in group:
                            captured_stones.append((gr, gc))

        # 3. 執行提子 (從棋盤移除)
        for cr, cc in captured_stones:
            self.board[cr][cc] = 0

        # 4. 檢查自殺規則 (禁手)
        # 如果沒有提吃對手，且自己落下後氣為 0，則為自殺 (不允許)
        my_group, my_libs = self.get_group_and_liberties(r, c)
        if my_libs == 0 and not captured_stones:
            self.board[r][c] = 0  # 還原
            return False, "禁手：禁止自殺"

        # === 5. 計算新的打劫禁著點 (核心邏輯) ===
        # 條件A: 剛才提吃了「正好一顆」子
        # 條件B: 自己這顆子下下去後「正好剩一口氣」
        # 如果符合，被提吃的那格就是對手下一手的禁著點
        if len(captured_stones) == 1 and my_libs == 1:
            self.ko_point = captured_stones[0]
        else:
            # 如果不是打劫狀態（例如提吃多子、或自己氣很多），就解除禁手
            self.ko_point = None

        # 成功落子
        msg = f"{'黑' if color==1 else '白'}棋落在 {coord_text}。"
        if captured_stones:
            msg += f" 提吃了 {len(captured_stones)} 顆子！"

        return True, msg
