# utils/config.py

HOST = "127.0.0.1"
PORT = 50000  # Client A listen, Client B connects

GAME_DURATION_SECONDS = 30      # 30 saniyelik oyun
RESPONSE_TIMEOUT_SECONDS = 5    # cevap bekleme süresi 5 sn

MAX_RWND = 5
INITIAL_SEQ = 0
WINDOW_SIZE = 4  # Go-Back-N sliding window size

LOG_DIR = "logs"
TIMELINE_PLOT_FILE = "timeline.png"

ROLE_A_NAME = "ClientA"
ROLE_B_NAME = "ClientB"
