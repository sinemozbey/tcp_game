# core/validator.py

from dataclasses import dataclass, replace
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

    NOT:
    - Bu sınıf tam bir TCP implementation'ı DEĞİL.
    - Oyunun sonsuz ERROR döngüsüne girmemesi için
      Go-Back-N senaryosundaki *retransmission/duplicate segment*'leri
      HATA olarak değil, "valid ama eski paket" olarak kabul ediyoruz.
    """

    def __init__(self) -> None:
        self.peer = PeerState()

    def reset(self) -> None:
        """İstendiğinde karşı tarafın state'ini sıfırlamak için yardımcı metod."""
        self.peer = PeerState()

    def peek_validate(self, pkt: Packet, last_sent_seq: int) -> tuple[bool, str, str]:
        """
        validate() ile aynı kontrolleri yapar ama state'i MUTATE etmez.
        UI'da "accept/reject" kararını verirken puanlamayı doğru yapmak için kullanılır.
        """
        snapshot = replace(self.peer)
        try:
            ok, reason, cls = self.validate(pkt, last_sent_seq)
        finally:
            self.peer = snapshot
        return ok, reason, cls

    def validate(self, pkt: Packet, last_sent_seq: int) -> tuple[bool, str, str]:
        """
        Gelen paketi doğrula.

        last_sent_seq: bizim şu ana kadar kullandığımız en yüksek sequence number.
        Dönüş: (is_valid, reason, classification)
          classification:
            - "ACK"         : ACK paketi
            - "IN_ORDER"    : DATA paketi, seq == expected_seq
            - "OLD"         : DATA paketi, seq < expected_seq (retransmit/duplicate)
            - "OUT_OF_ORDER": DATA paketi, seq > expected_seq (GBN discard + dup ACK)
        """

        # ---------------------- Temel yapısal kontroller ---------------------- #
        if pkt.type not in {"DATA", "ACK", "ERROR"}:
            return False, "Unknown packet type", "INVALID"

        if pkt.type == "ERROR":
            # ERROR paketini yapısal olarak her zaman kabul ediyoruz.
            # Oyun mantığı bu bilgiyi ayrıca işleyecek.
            return True, "Peer reports ERROR", "ERROR"

        if pkt.type == "ACK":
            # ACK paketleri için sadece temel alan kontrolleri yapıyoruz.
            # (DATA gibi seq progression beklemiyoruz.)
            classification = "ACK"
        else:
            classification = "DATA"

        # rwnd range
        if pkt.rwnd is None or pkt.rwnd < 0 or pkt.rwnd > MAX_RWND:
            return False, "Invalid rwnd", "INVALID"

        # length rules
        if pkt.length is None or pkt.length < 0:
            return False, "Invalid length", "INVALID"

        if pkt.length > pkt.rwnd:
            return False, "length > rwnd", "INVALID"

        # sequence rules
        if pkt.seq is None or pkt.seq < 0:
            return False, "Invalid seq", "INVALID"

        # ack rules
        if pkt.ack is None or pkt.ack < 0:
            return False, "Invalid ack", "INVALID"

        # ACK bizim göndermediğimiz bir şeyi onaylayamaz
        if pkt.ack > last_sent_seq:
            return False, "ACK acknowledges unsent data", "INVALID"

        if pkt.type == "ACK":
            # ACK'lerde DATA-specific sıralama kontrolü yok.
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            return True, "OK", "ACK"

        # ---------------------- Go-Back-N / retransmission -------------------- #
        # Burada asıl kritik kısım:
        # - expected_seq: sırayla ve hatasız gittiğimizde beklediğimiz next seq
        # - GBN'de timeout olduğunda gönderici base'den itibaren tekrar
        #   gönderim yapacağından seq geri gelebilir (retransmission).
        #
        # Bu durumu "protocol error" olarak değil,
        # "eski veya tekrar gönderilen segment" olarak kabul ediyoruz.

        if pkt.seq < self.peer.expected_seq:
            # Beklediğimiz seq'ten küçük -> eski veya retransmitted segment
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            return True, "Old or retransmitted segment", "OLD"

        if pkt.seq > self.peer.expected_seq:
            # Go-Back-N receiver: out-of-order segment discard, dup ACK gönderilir.
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            return True, "Out-of-order segment (GBN discard)", "OUT_OF_ORDER"

        # ---------------------- Normal, ileri yönde ilerleme ------------------ #
        # Buraya geliyorsak:
        # - seq >= expected_seq
        # - Yapısal olarak da her şey yolunda → paketi kabul ediyoruz.

        self.peer.last_seq = pkt.seq
        self.peer.last_len = pkt.length
        self.peer.last_ack = pkt.ack
        self.peer.last_rwnd = pkt.rwnd
        self.peer.expected_seq = pkt.seq + pkt.length

        return True, "OK", "IN_ORDER"
