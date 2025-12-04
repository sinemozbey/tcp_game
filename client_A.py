# client_A.py

from core.connection import Connection
from core.game_logic import GameLogic
from utils.config import ROLE_A_NAME


def main():
    conn = Connection.create_as_server()
    game = GameLogic(role_name=ROLE_A_NAME, conn=conn, starts_first=True)
    game.run()


if __name__ == "__main__":
    main()
