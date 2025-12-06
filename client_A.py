# client_A.py

from core.connection import Connection
from utils.config import ROLE_A_NAME
from gui_client import run_gui_client


def main():
    
    conn = Connection.create_as_server()
    run_gui_client(role=ROLE_A_NAME, conn=conn, starts_first=True)


if __name__ == "__main__":
    main()
