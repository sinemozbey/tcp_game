from utils.config import ROLE_A_NAME
from gui_client import run_gui_client


def main():
    run_gui_client(role=ROLE_A_NAME, starts_first=True)


if __name__ == "__main__":
    main()
