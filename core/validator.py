# core/validator.py

from dataclasses import dataclass
from .packet import Packet
from utils.config import MAX_RWND, BUFFER_DRAIN_INTERVAL_SECONDS


@dataclass
class PeerState:
    last_seq: int = 0
    last_len: int = 0
    last_ack: int = 0
    last_rwnd: int = MAX_RWND
    expected_seq: int = 0


class PacketValidator:
    """
    Mantıksal tutarlılık kontrolleri.
    Artık 'rwnd' için TAM EŞİTLİK (Strict Equality) kontrolü yapıyor.
    """

    def __init__(self) -> None:
        self.peer = PeerState()

    def reset(self) -> None:
        self.peer = PeerState()

    def validate(self, pkt: Packet, last_sent_seq: int, elapsed_time: float = 0.0) -> tuple[bool, str]:
        """
        Gelen paketi doğrula.
        """

        # --- Temel yapısal kontroller ---
        if pkt.type not in {"DATA", "ACK", "ERROR"}:
            return False, "Unknown packet type"

        if pkt.type == "ERROR":
            return True, "Peer reports ERROR"

        # 1. KURAL: RWND 50'DEN BÜYÜK OLAMAZ
        # Eğer girilen window size maksimumdan (50) büyükse -> INVALID
        if pkt.rwnd is None or pkt.rwnd > MAX_RWND:
            return False, f"Invalid rwnd: {pkt.rwnd} > {MAX_RWND}"
            
        if pkt.rwnd < 0:
            return False, "Invalid rwnd: negative value"
        
        # 2. KURAL: DOĞRU DEĞER İLE KARŞILAŞTIRMA (Strict Check)
        # Eğer rwnd <= 50 ise, olması gereken değerle birebir uyuşmalı.
        if pkt.ack is not None:
            # Geçen süreye göre ne kadar veri silinmiş (drain) olmalı?
            # Config'de 20000 olduğu için testte burası 0 gelir.
            max_drained = int(elapsed_time / BUFFER_DRAIN_INTERVAL_SECONDS) * 20
            
            # Tamponda (Buffer) şu an ne kadar veri var?
            # Buffer = Toplam Alınan (ack) - Toplam Silinen (drained)
            bytes_in_buffer = max(0, pkt.ack - max_drained)
            
            # Olması gereken TEK doğru rwnd değeri
            expected_rwnd = max(0, MAX_RWND - bytes_in_buffer)
            
            # Gelen değer, hesaplanan değerle AYNI DEĞİLSE -> INVALID
            if pkt.rwnd != expected_rwnd:
                return False, f"Rwnd mismatch: got {pkt.rwnd}, expected {expected_rwnd} (ack={pkt.ack})"

        # -------------------------------------------------------------------- #

        # length rules
        if pkt.length is None or pkt.length < 0:
            return False, "Invalid length"

        if pkt.length > pkt.rwnd:
            return False, "length > rwnd"

        # sequence rules
        if pkt.seq is None or pkt.seq < 0:
            return False, "Invalid seq"

        # ack rules
        if pkt.ack is None or pkt.ack < 0:
            return False, "Invalid ack"

        if pkt.ack > last_sent_seq:
            return False, "ACK acknowledges unsent data"
        
        if last_sent_seq > 0 and pkt.ack != last_sent_seq:
             return False, f"Incorrect ACK value: got {pkt.ack}, expected {last_sent_seq}"

        # GBN Check
        if pkt.seq < self.peer.expected_seq:
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            return True, "Old or retransmitted segment"
        
        if pkt.seq > self.peer.expected_seq:
            return False, f"Sequence number gap: got {pkt.seq}, expected {self.peer.expected_seq}"

        # State Update
        self.peer.last_seq = pkt.seq
        self.peer.last_len = pkt.length
        self.peer.last_ack = pkt.ack
        self.peer.last_rwnd = pkt.rwnd
        self.peer.expected_seq = pkt.seq + pkt.length

        return True, "OK"