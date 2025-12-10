# core/connection.py

import socket
import json
from typing import Optional
from utils.logger import get_logger
from utils.config import HOST_BIND, HOST_CONNECT, PORT, RESPONSE_TIMEOUT_SECONDS

logger = get_logger("connection")


class TimeoutError(Exception):
    """Karşı taraftan belirlenen süre içinde cevap gelmezse atılır."""
    pass


class Connection:
    """
    JSON paketleri göndermek/almak için TCP soket wrapper'ı.

    Protokol:
      - Her mesaj tek satır JSON.
      - Satır sonu: '\\n'
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock
        # Henüz tamamen parse edilmemiş gelen veriler
        self._buffer = b""

    # ------------------------------------------------------------------ #
    # Factory methods
    # ------------------------------------------------------------------ #

    @classmethod
    def create_as_server(cls) -> "Connection":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST_BIND, PORT))
        s.listen(1)
        logger.info(f"Listening on {HOST_BIND}:{PORT}")
        conn, addr = s.accept()
        logger.info(f"Accepted connection from {addr}")
        s.close()
        return cls(conn)

    @classmethod
    def create_as_client(cls) -> "Connection":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        logger.info(f"Connecting to {HOST_CONNECT}:{PORT} ...")
        s.connect((HOST_CONNECT, PORT))
        logger.info("Connected.")
        return cls(s)

    # ------------------------------------------------------------------ #
    # Send / Receive
    # ------------------------------------------------------------------ #

    def send_json(self, data: dict) -> None:
        """
        Verilen dict'i JSON'a çevirip satır sonu ile birlikte gönderir.
        """
        raw = json.dumps(data).encode("utf-8") + b"\n"
        try:
            self.sock.sendall(raw)
        except (socket.timeout, BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            logger.warning(f"Send failed, closing connection: {e}")
            raise ConnectionError("Send failed, peer probably closed the connection")

    def recv_json(self, timeout: Optional[float] = RESPONSE_TIMEOUT_SECONDS) -> dict:
        """
        Karşı taraftan bir satır JSON mesajı okur ve dict olarak döner.

        timeout: saniye cinsinden response bekleme süresi.
        """
        # Bu çağrı için timeout ayarla
        self.sock.settimeout(timeout)

        try:
            # Buffer'da '\n' görene kadar okumaya devam et
            while b"\n" not in self._buffer:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Peer closed the connection")
                self._buffer += chunk
        except socket.timeout:
            raise TimeoutError("No response within timeout")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            raise ConnectionError("Peer reset the connection")
        finally:
            # Bu çağrı bittikten sonra socket'i tekrar blocking moda al
            try:
                self.sock.settimeout(None)
            except Exception:
                pass

        # Satır satır parse et
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = rest

        text = line.decode("utf-8").strip()
        if not text:
            # Boş satır geldiyse, bir sonrakini oku
            return self.recv_json(timeout=timeout)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON received: {text!r} ({e})")
            raise ConnectionError("Received invalid JSON from peer") from e

    def close(self) -> None:
        """
        Bağlantıyı güvenli şekilde kapat.
        """
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            # Zaten kapalı olabilir, sorun değil
            pass
        finally:
            try:
                self.sock.close()
            except Exception:
                pass
