# core/validator.py

from dataclasses import dataclass
from .packet import Packet
from utils.config import MAX_RWND


@dataclass
class PeerState:
    # En son gördüğümüz paket bilgileri
    last_seq: int = 0
    last_len: int = 0
    last_ack: int = 0
    last_rwnd: int = MAX_RWND
    # Bir sonrakinde beklediğimiz seq (last_seq + last_len)
    expected_seq: int = 0


class PacketValidator:
    """
    Basit mantıksal tutarlılık kontrolleri.
    """

    def __init__(self) -> None:
        self.peer = PeerState()

    def reset(self) -> None:
        """İstendiğinde karşı tarafın state'ini sıfırlamak için yardımcı metod."""
        self.peer = PeerState()

    def validate(self, pkt: Packet, last_sent_seq: int) -> tuple[bool, str]:
        """
        Gelen paketi doğrula.

        last_sent_seq: bizim şu ana kadar kullandığımız en yüksek sequence number.
        Dönüş: (is_valid, reason)
        """

        # ---------------------- Temel yapısal kontroller ---------------------- #
        if pkt.type not in {"DATA", "ACK", "ERROR"}:
            return False, "Unknown packet type"

        if pkt.type == "ERROR":
            return True, "Peer reports ERROR"

        # rwnd range
        if pkt.rwnd is None or pkt.rwnd < 0 or pkt.rwnd > MAX_RWND:
            return False, "Invalid rwnd (out of 0-50 range)"

        # ---------------------- İMKANSIZ RWND KONTROLÜ ---------------------- #
        # Mantık: Karşı tarafın buffer'ı, bizim gönderdiğimiz veriden daha fazla dolamaz.
        # Örneğin biz toplam 10 byte yolladıysak (last_sent_seq=10), 
        # karşı tarafın rwnd değeri en az 40 olabilir (50 - 10). 
        # Eğer karşı taraf rwnd=30 derse (20 byte dolu), bu imkansızdır.
        # (Bu kontrol özellikle oyunun başlarında kritiktir)
        
        min_possible_rwnd = max(0, MAX_RWND - last_sent_seq)
        
        if pkt.rwnd < min_possible_rwnd:
            return False, f"Impossible rwnd: {pkt.rwnd} (sent {last_sent_seq} bytes, min expected {min_possible_rwnd})"

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

        # Go-Back-N kontrolleri (Eski paketler)
        if pkt.seq < self.peer.expected_seq:
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            return True, "Old or retransmitted segment"
        
        # Seq Gap kontrolü
        if pkt.seq > self.peer.expected_seq:
            return False, f"Sequence number gap: got {pkt.seq}, expected {self.peer.expected_seq}"

        # State güncelleme
        self.peer.last_seq = pkt.seq
        self.peer.last_len = pkt.length
        self.peer.last_ack = pkt.ack
        self.peer.last_rwnd = pkt.rwnd
        self.peer.expected_seq = pkt.seq + pkt.length

        return True, "OK"