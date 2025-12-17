HOST = "127.0.0.1"
PORT = 50000  

GAME_DURATION_SECONDS = 300
# Beklenen yanıt süresi (tepki süresi): 45s
RESPONSE_TIMEOUT_SECONDS = 45
# Zero-window / window-full gibi durumlarda buffer'ı boşaltma aralığı: 15s
BUFFER_DRAIN_INTERVAL_SECONDS = 15
# rwnd=0 veya pencere full kaldığında verilecek maksimum tolerans: 45s
WINDOW_STALL_TIMEOUT_SECONDS = 45

# Oyun başlangıcında rwnd = 50 (doküman)
MAX_RWND = 50
# Her 15 saniyede rwnd artışını temsil eden adım (doküman: +20)
RWND_INCREASE_STEP = 20
TIMELINE_PLOT_FILE = "timeline.png"
INITIAL_SEQ = 0
# Go-Back-N gönderim penceresi (byte bazlı kapasite ~ WINDOW_SIZE * segment_length).
# segment_length=1 kullanıldığı için kapasiteyi 50'ye çekiyoruz.
WINDOW_SIZE = 50

LOG_DIR = "logs"


ROLE_A_NAME = "ClientA"
ROLE_B_NAME = "ClientB"

MIN_SEGMENT_SIZE = 1
MAX_SEGMENT_SIZE = 50
