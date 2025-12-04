# core/gbn.py

from dataclasses import dataclass
from utils.config import WINDOW_SIZE, INITIAL_SEQ


@dataclass
class GBNState:
    # base: henüz tam olarak ACK almamış olduğumuz en düşük seq
    base: int = INITIAL_SEQ
    # next_seq: bir sonraki DATA segmenti için kullanılacak seq
    next_seq: int = INITIAL_SEQ
    # aynı ACK üst üste kaç kez geldi (duplicate ACK sayacı)
    duplicate_ack_count: int = 0


class GoBackN:
    """
    Basit bir Go-Back-N (GBN) pencere durumu.

    Gerçek payload yerine sadece:
      - seq (başlangıç sequence numarası)
      - length (segment uzunluğu)
    ile segmentleri simüle ediyoruz.
    """

    def __init__(self, segment_length: int = 1) -> None:
        self.state = GBNState()
        self.window_size = WINDOW_SIZE
        self.segment_length = segment_length

    # ------------------------------------------------------------------ #
    # Gönderici tarafı
    # ------------------------------------------------------------------ #

    def next_data_segment(self) -> tuple[int, int]:
        """
        Bir sonraki DATA segmenti için (seq, length) döndürür.

        Eğer pencere doluysa RuntimeError fırlatır.
        """
        window_bytes = self.window_size * self.segment_length
        if self.state.next_seq >= self.state.base + window_bytes:
            raise RuntimeError("Send window is full (Go-Back-N)")

        seq = self.state.next_seq
        length = self.segment_length
        self.state.next_seq += length
        return seq, length

    # ------------------------------------------------------------------ #
    # ACK işleme
    # ------------------------------------------------------------------ #

    def on_ack(self, ack_num: int) -> tuple[bool, bool]:
        """
        ACK alındığında state'i günceller.

        Dönüş:
          (window_advanced, retransmit_required)
        """
        retransmit = False
        advanced = False

        if ack_num > self.state.base:
            # Yeni (ilerlemiş) bir ACK aldık
            self.state.base = ack_num
            self.state.duplicate_ack_count = 0
            advanced = True

            # Pencere ilerledi, fakat next_seq geride kalmışsa
            # (örn. timeout sonrası resetlemiş olabiliriz) onu da ileri taşıyalım.
            if self.state.next_seq < self.state.base:
                self.state.next_seq = self.state.base

        elif ack_num == self.state.base:
            # Duplicate ACK
            self.state.duplicate_ack_count += 1
            if self.state.duplicate_ack_count >= 2:
                # Çift (veya daha fazla) duplicate ACK → base'ten itibaren retransmit
                retransmit = True
                self.state.next_seq = self.state.base

        else:
            # Eski bir ACK – yok sayıyoruz
            pass

        return advanced, retransmit

    # ------------------------------------------------------------------ #
    # Yardımcı metodlar
    # ------------------------------------------------------------------ #

    def reset(self, seq: int = INITIAL_SEQ) -> None:
        """
        GBN durumunu sıfırla (örn. oyun resetlenirken).
        """
        self.state = GBNState(base=seq, next_seq=seq)
