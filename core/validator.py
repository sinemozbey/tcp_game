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
            # ERROR paketini yapısal olarak her zaman kabul ediyoruz.
            # Oyun mantığı bu bilgiyi ayrıca işleyecek.
            return True, "Peer reports ERROR"

        # rwnd range
        if pkt.rwnd is None or pkt.rwnd < 0 or pkt.rwnd > MAX_RWND:
            return False, "Invalid rwnd"

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

        # ACK bizim göndermediğimiz bir şeyi onaylayamaz
        if pkt.ack > last_sent_seq:
            return False, "ACK acknowledges unsent data"
        
        # ---------------------- ACK doğruluğu kontrolü ---------------------- #
        # Eğer biz veri göndermişsek (last_sent_seq > 0), gelen ACK doğru olmalı
        # Expected ACK = bizim son gönderdiğimiz seq + length
        if last_sent_seq > 0:
            # Karşı taraf bizim gönderdiğimiz veriyi acknowledge etmeli
            # ACK değeri last_sent_seq'e eşit olmalı (cumulative acknowledgment)
            # Çünkü biz sırayla gönderiyoruz ve karşı taraf hepsini almış olmalı
            
            # DATA veya ACK paketlerinde ack alanı kontrol edilmeli
            if pkt.ack != last_sent_seq:
                return False, f"Incorrect ACK value: got {pkt.ack}, expected {last_sent_seq}"

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
            # State'i agresif şekilde değiştirmiyoruz; sadece ack/rwnd'i güncellemek güvenli.
            self.peer.last_ack = pkt.ack
            self.peer.last_rwnd = pkt.rwnd
            # expected_seq'i değiştirmiyoruz ki ileride gelen yeni seq'leri
            # doğru şekilde değerlendirebilelim.
            return True, "Old or retransmitted segment"
        
        # ---------------------- Seq atlaması kontrolü ---------------------- #
        # Eğer seq expected_seq'ten büyükse, bir paket atlandı demektir
        # TCP'de paketler sırayla gelmeli (bizim oyunumuzda)
        if pkt.seq > self.peer.expected_seq:
            return False, f"Sequence number gap: got {pkt.seq}, expected {self.peer.expected_seq}"

        # ---------------------- Normal, ileri yönde ilerleme ------------------ #
        # Buraya geliyorsak:
        # - seq >= expected_seq
        # - Yapısal olarak da her şey yolunda → paketi kabul ediyoruz.

        self.peer.last_seq = pkt.seq
        self.peer.last_len = pkt.length
        self.peer.last_ack = pkt.ack
        self.peer.last_rwnd = pkt.rwnd
        self.peer.expected_seq = pkt.seq + pkt.length

        return True, "OK"
