HOST = "127.0.0.1"
PORT = 50000  

GAME_DURATION_SECONDS = 300
# Beklenen yanıt süresi (tepki süresi): 45s
RESPONSE_TIMEOUT_SECONDS = 45
# Zero-window / window-full gibi durumlarda buffer'ı boşaltma aralığı: 15s
BUFFER_DRAIN_INTERVAL_SECONDS = 2000000
# rwnd=0 veya pencere full kaldığında verilecek maksimum tolerans: 45s
WINDOW_STALL_TIMEOUT_SECONDS = 45

MAX_RWND = 50
TIMELINE_PLOT_FILE = "timeline.png"
INITIAL_SEQ = 0
WINDOW_SIZE = 10 

LOG_DIR = "logs"


ROLE_A_NAME = "ClientA"
ROLE_B_NAME = "ClientB"

MIN_SEGMENT_SIZE = 1
MAX_SEGMENT_SIZE = 5  