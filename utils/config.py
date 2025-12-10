import os

# Sunucu (ClientA) hangi arayüzde dinleyecek?
# Varsayılan: tüm arayüzler (0.0.0.0) ki başka makineden erişilebilsin.
HOST_BIND = os.getenv("TCP_GAME_BIND_HOST", "0.0.0.0")

# İstemcinin (ClientB) hangi IP'ye bağlanacağı.
# Varsayılan: localhost. Farklı makineden bağlanacaksanız TCP_GAME_HOST=<sunucu_ip>
# olarak geçici veya kalıcı şekilde ayarlayın.
HOST_CONNECT = os.getenv("TCP_GAME_HOST", "127.0.0.1")

PORT = int(os.getenv("TCP_GAME_PORT", "50000"))

GAME_DURATION_SECONDS = 300      
RESPONSE_TIMEOUT_SECONDS = 5    

MAX_RWND = 5
TIMELINE_PLOT_FILE = "timeline.png"
INITIAL_SEQ = 0
WINDOW_SIZE = 4 

LOG_DIR = "logs"


ROLE_A_NAME = "ClientA"
ROLE_B_NAME = "ClientB"

MIN_SEGMENT_SIZE = 1
MAX_SEGMENT_SIZE = 5  
