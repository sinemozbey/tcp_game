from gui_client import run_gui_client
from utils.config import ROLE_B_NAME

def main():
    run_gui_client(role=ROLE_B_NAME, starts_first=False)

if __name__ == "__main__":
    main()
