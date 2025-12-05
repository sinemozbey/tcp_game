# client_B.py

import time
from core.connection import Connection
from utils.config import ROLE_B_NAME
from gui_client import run_gui_client


def main():
    # A (server) ayağa kalksın diye biraz bekle
    time.sleep(1)
    conn = Connection.create_as_client()
    # B tarafı client ve ikinci başlasın
    run_gui_client(role=ROLE_B_NAME, conn=conn, starts_first=False)


if __name__ == "__main__":
    main()
