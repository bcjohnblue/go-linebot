
import sys
import os
import asyncio
from unittest.mock import MagicMock, patch

# Add src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, 'src')
sys.path.append(src_path)

async def debug_one_line_kill():
    # 1. Mock dependencies that are not relevant to game logic or board state
    
    # Mock linebot SDK
    sys.modules['linebot'] = MagicMock()
    sys.modules['linebot.v3'] = MagicMock()
    sys.modules['linebot.v3.messaging'] = MagicMock()
    sys.modules['linebot.v3.webhooks'] = MagicMock()
    # Mock specific models to satisfy imports
    sys.modules['linebot.v3.messaging.models'] = MagicMock()
    # Mock exceptions
    sys.modules['linebot.v3.messaging.exceptions'] = MagicMock()

    # Mock config
    mock_config = {"server": {"public_url": "https://example.com"}, "line": {"channel_access_token": "test_token"}}
    sys.modules['config'] = MagicMock()
    sys.modules['config'].config = mock_config
    
    # Mock logger
    sys.modules['logger'] = MagicMock()
    sys.modules['logger'].logger = MagicMock()
    
    # Mock handlers that are not relevant to loading SGF
    # Mock handlers that are not relevant to loading SGF
    sys.modules['handlers.katago_handler'] = MagicMock()
    sys.modules['handlers.draw_handler'] = MagicMock()
    sys.modules['handlers.board_visualizer'] = MagicMock()
    sys.modules['handlers.board_visualizer'].BoardVisualizer = MagicMock()
    
    # Mock LLM provider
    sys.modules['LLM'] = MagicMock()
    sys.modules['LLM.providers'] = MagicMock()
    sys.modules['LLM.providers.openai_provider'] = MagicMock()
    
    # Mock services (if any)
    # line_handler imports line_bot_api and blob_api which are instantiated in line_handler.py using imported classes.
    # Since we mocked linebot.v3.messaging, the instantiation lines in line_handler.py:
    #   configuration = Configuration(...)
    #   api_client = ApiClient(...)
    #   line_bot_api = MessagingApi(api_client)
    # will use our mocks.
    
    # Note: We do NOT mock handlers.go_engine because we want the real board logic.
    # We do NOT mock handlers.sgf_handler unless it's problematic (it uses sgfmill which we installed).
    # We do NOT mock sgfmill.
    
    try:
        from handlers import line_handler
        
        # 2. Simulate User Input
        # Call handle_one_line_kill_mode('debug_user', 'debug_token')
        target_id = 'debug_user_001'
        print(f"Calling handle_one_line_kill_mode for target_id: {target_id}")
        
        # We need to ensure we can visualize or at least draw board mock
        # line_handler uses 'visualizer' instance.
        # visualizer imports BoardVisualizer which likely uses Pillow, numpy etc.
        # If visualizer fails, the function might crash before printing game state.
        # Let's mock visualizer.draw_board to avoid actual drawing dependency issues if any,
        # but keep the rest real.
        
        with patch('handlers.line_handler.visualizer') as mock_visualizer:
             # Run the handler
             await line_handler.handle_one_line_kill_mode(target_id, 'debug_reply_token')
             
             # 3. Print game_states
             print("\n--- Game States ---")
             if target_id in line_handler.game_states:
                 state = line_handler.game_states[target_id]
                 game = state['game'] # GoBoard instance
                 board = game.board
                 
                 print(f"Game ID: {line_handler.game_ids.get(target_id)}")
                 print(f"Current Turn: {state['current_turn']} (1=Black, 2=White)")
                 print("Board State (0=Empty, 1=Black, 2=White):")
                 
                 # Print simple board representation
                 for r in range(19):
                     line = ""
                     for c in range(19):
                         val = board[r][c]
                         if val == 0: line += "."
                         elif val == 1: line += "B"
                         elif val == 2: line += "W"
                     print(f"{19-r:2d} {line}")
                 print("   " + "ABCDEFGHIJKLMNOPQRST".replace("I", ""))
                 
                 # Verify setup stones
                 # Expecting lots of stones on line 1 (row 18)
                 bottom_row = board[18] # Row 18 is line 1 in engine coords (0-18, 0 is top)
                 # Wait, in GoBoard coordinates, is 0 top or bottom?
                 # restore_game_from_sgf_file converts: r = 18 - sgf_r.
                 # SGF row 0 is usually top-left in SGF spec?
                 # Actually SGF coords 'aa' is top-left usually.
                 # sgfmill says: "In SGF, (0,0) is the top-left corner 'aa'".
                 # node.get_move() returns (row, col).
                 # Wait, sgfmill get_move() returns (row, col) 0-based.
                 # line_handler assumes:
                 #    # move is (sgf_row, sgf_col), where sgf_row 0 is bottom
                 #    r = 18 - sgf_r
                 # This implies line_handler thinks sgf_row 0 is bottom?
                 # Standard SGF 'aa' is row 0, col 0. 'sa' is row 18, col 0.
                 # If sgfmill follows standard SGF, row 0 is top.
                 # If line_handler flips it (18 - sgf_r), then line_handler uses 0 as bottom? OR 0 as top and flips input?
                 # Let's check visualizer or other logic.
                 # Typically Go engines use 0 as top or 0 as bottom.
                 # If 0 is bottom, then standard SGF (0=top) needs flipping.
                 # If 0 is top, then standard SGF (0=top) needs NO flipping.
                 
                 # Whatever the coord system, let's just see if there are stones on the board.
                 black_stones = sum(row.count(1) for row in board)
                 white_stones = sum(row.count(2) for row in board)
                 print(f"Total Black Stones: {black_stones}")
                 print(f"Total White Stones: {white_stones}")
                 
                 if black_stones > 0:
                     print("SUCCESS: Stones found on board.")
                 else:
                     print("FAILURE: No stones found on board.")
             else:
                 print(f"FAILURE: game_states not found for {target_id}")

    except ImportError as e:
        print(f"ImportError: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_one_line_kill())
