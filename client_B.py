# client_B.py

import time
from core.connection import Connection
from core.game_logic import GameLogic
from utils.config import ROLE_B_NAME


def main():
    # Small delay to e1nsure server is ready
    time.sleep(1)
    conn = Connection.create_as_client()
    game = GameLogic(role_name=ROLE_B_NAME, conn=conn, starts_first=False)
    game.run()


if __name__ == "__main__":
    main()
