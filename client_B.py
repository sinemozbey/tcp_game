# client_B.py
import time
from core.connection import Connection
from gui_client import run_gui_client
from utils.config import ROLE_B_NAME

def main():
    time.sleep(1)
    conn = Connection.create_as_client()
    run_gui_client(role=ROLE_B_NAME, conn=conn, starts_first=False)

if __name__ == "__main__":
    main()
