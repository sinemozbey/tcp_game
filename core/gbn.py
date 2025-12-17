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

    Notlar (raporda da yazabilirsin):
      • Mantıksal pencere boyutu WINDOW_SIZE * segment_length byte olarak ele alınır.
      • Fast retransmit: aynı ACK'in art arda gelmesi sayısı 3'e ulaştığında
        (3 duplicate ACK) base'ten itibaren Go-Back-N retransmission tetiklenir.
    """

    def __init__(self, segment_length: int = 1) -> None:
        # segment_length: "tipik" segment uzunluğu (1 byte, 2 byte vs.)
        # Değişken boyutlu segmentleri destekliyoruz ama pencere kapasitesini
        # hesaplamak için referans olarak kullanıyoruz.
        self.state = GBNState()
        self.window_size = WINDOW_SIZE          # pencere: kaç segment
        self.segment_length = segment_length    # her segment için referans uzunluk

    # ------------------------------------------------------------------ #
    # Yardımcı metodlar
    # ------------------------------------------------------------------ #

    def _window_capacity_bytes(self) -> int:
        """
        Pencerenin toplam kapasitesi (byte cinsinden).

        Burada:
          • WINDOW_SIZE: segment sayısı
          • segment_length: her segmentin yaklaşık uzunluğu

        Gerçekte next_data_segment(length) ile değişken uzunlukta
        segmentler gönderebiliyoruz ama pencere hesabını byte bazlı yapıyoruz.
        """
        return self.window_size * self.segment_length

    def bytes_in_flight(self) -> int:
        """
        Henüz ACK almamış, gönderilmiş veri miktarı (byte).

        base: en eski, henüz tamamen ACK almamış byte index
        next_seq: bir sonrakinde kullanılacak byte index
        """
        return self.state.next_seq - self.state.base

    def can_send(self) -> bool:
        """
        GBN penceresine göre yeni bir segment gönderebilir miyiz?
        """
        return self.bytes_in_flight() < self._window_capacity_bytes()

    # ------------------------------------------------------------------ #
    # Gönderici tarafı
    # ------------------------------------------------------------------ #

    def next_data_segment(self, length: int) -> tuple[int, int]:
        """
        Bir sonraki DATA segmenti için (seq, length) döndürür.

        length: bu segmentte kaç byte göndermek istiyoruz (değişken boyut).
        Pencere kontrolünü BYTE bazlı yapar:
          bytes_in_flight + length <= window_capacity_bytes olmalı.
        """
        window_bytes = self._window_capacity_bytes()
        in_flight = self.bytes_in_flight()

        if in_flight + length > window_bytes:
            raise RuntimeError("Send window is full (Go-Back-N)")

        seq = self.state.next_seq
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

          • window_advanced: base ileri taşındı mı?
          • retransmit_required: double-ACK nedeniyle Go-Back-N
            retransmission tetiklenmeli mi?
        """
        retransmit = False
        advanced = False

        if ack_num > self.state.base:
            # Yeni (ilerlemiş) bir ACK aldık → pencereyi kaydır.
            self.state.base = ack_num
            self.state.duplicate_ack_count = 0
            advanced = True

            # Pencere ilerlediyse ve next_seq gerideyse (örn. reset vb.),
            # onu da en az base'e kadar çekiyoruz.
            if self.state.next_seq < self.state.base:
                self.state.next_seq = self.state.base

        elif ack_num == self.state.base:
            # Duplicate ACK:
            # Aynı base için tekrar ACK geliyorsa, karşı tarafta bir kayıp
            # algılanmış olabilir → sayacı arttır.
            self.state.duplicate_ack_count += 1

            # 3 (veya daha fazla) duplicate ACK → base'ten itibaren
            # Go-Back-N retransmission tetiklenir.
            if self.state.duplicate_ack_count >= 3:
                retransmit = True
                # GBN mantığı: yeniden gönderilecek ilk seq = base.
                # next_seq'i base'e çekiyoruz; dışarıdaki kod
                # buradan itibaren segmentleri tekrar üretecek.
                self.state.next_seq = self.state.base

        else:
            # Eski bir ACK – yok sayıyoruz.
            # (ack_num < base durumu)
            pass

        return advanced, retransmit

    # ------------------------------------------------------------------ #
    # Reset
    # ------------------------------------------------------------------ #

    def reset(self, seq: int = INITIAL_SEQ) -> None:
        """
        GBN durumunu sıfırla (örn. oyun resetlenirken).
        """
        self.state = GBNState(base=seq, next_seq=seq)
