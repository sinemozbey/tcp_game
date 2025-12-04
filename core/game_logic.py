# core/game_logic.py

import time
from typing import List

from utils.logger import get_logger
from utils.config import (
    GAME_DURATION_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    MAX_RWND,
    TIMELINE_PLOT_FILE,
    MIN_SEGMENT_SIZE,
    MAX_SEGMENT_SIZE,
)

from .packet import Packet
from .validator import PacketValidator
from .gbn import GoBackN
from .scoreboard import Scoreboard
from .connection import Connection, TimeoutError
from utils.timeline_plot import TimelineEvent, plot_timeline


class GameLogic:
    """
    High-level orchestration of the TCP Game.
    One instance runs per client.
    """

    def __init__(self, role_name: str, conn: Connection, starts_first: bool):
        self.role = role_name
        self.conn = conn
        self.starts_first = starts_first

        self.logger = get_logger(role_name)
        self.validator = PacketValidator()
        # NOT: GoBackN, variable-length segmentler için güncellendi varsayılıyor
        self.gbn = GoBackN()
        self.scoreboard = Scoreboard()
        self.timeline: List[TimelineEvent] = []

        self.last_sent_seq = 0

        # Receive window / buffer durumu
        self.current_rwnd = MAX_RWND          # advertise ettiğimiz pencere
        self.recv_buffer_used = 0             # receive buffer'da dolu byte sayısı

        # Timer'lar
        self.last_window_update = time.time()  # en son "uygulama veri işledi" zamanı
        self.window_full_since = None          # rwnd == 0 olduğu an (full pencere)
        self.zero_window_since = None          # rwnd=0 advertisement ne zamandır sürüyor
        self.last_activity_time = time.time()  # son paket alışverişi (send/recv) zamanı

        # GBN duplicate ACK sonrası bir sonraki DATA'nın retransmit olduğunu işaretler
        self._pending_retransmit = False

    # --- Utility methods -------------------------------------------------

    def _record_event(self, sender: str, receiver: str, pkt: Packet, kind: str):
        now = time.time()
        self.timeline.append(
            TimelineEvent(
                timestamp=now,
                sender=sender,
                receiver=receiver,
                seq=pkt.seq,
                ack=pkt.ack,
                rwnd=pkt.rwnd,
                length=pkt.length,
                kind=kind,
            )
        )

    # --- Packet sending helpers -----------------------------------------

    def _send_packet(self, pkt: Packet):
        self.logger.info(f"Sending: {pkt}")
        self.conn.send_json({"packet": pkt.to_json()})
        self.last_activity_time = time.time()

        # Timeline için tür belirleme
        if pkt.type == "DATA":
            # Eğer GBN duplicate ACK yüzünden retransmit tetiklediyse
            if getattr(self, "_pending_retransmit", False):
                kind = "RETX"
                self.logger.warning(f"Retransmitting segment seq={pkt.seq}")
                # Bir kere kullandık, flag'i sıfırla
                self._pending_retransmit = False
            else:
                kind = "DATA"
        elif pkt.type == "ACK":
            kind = "ACK"
        elif pkt.type == "ERROR":
            kind = "ERROR"
        else:
            kind = pkt.type  # ekstra tip olursa diye

        # DATA / ACK / ERROR / RETX ne ise o
        self._record_event(self.role, "Peer", pkt, kind)

        # rwnd=0 advertise ettiysek, zero_window timer'ı tut
        if pkt.rwnd == 0:
            if self.zero_window_since is None:
                self.zero_window_since = time.time()
        else:
            self.zero_window_since = None

        # Son gönderilen seq'i takip et (doğrulama için)
        if pkt.seq is not None:
            end_seq = pkt.seq + (pkt.length or 0)
            if end_seq > self.last_sent_seq:
                self.last_sent_seq = end_seq

    def _receive_packet(self) -> Packet:
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        self.last_activity_time = time.time()
        return pkt

    # --- Game turn logic -------------------------------------------------

    def _create_next_data_packet(self) -> Packet:
        """
        Bir SONRAKİ DATA segmentini insan kontrollü olarak oluşturur.
        Kullanıcıdan paket uzunluğunu alır.
        """

        while True:
            try:
                size_str = input(
                    f"{self.role}: Göndereceğin DATA uzunluğu "
                    f"({MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE}, 0 = sadece ACK): "
                ).strip()

                if size_str == "":
                    # Boş enter: default 1 byte
                    length = 1
                else:
                    length = int(size_str)

            except ValueError:
                print(f"{self.role}: Lütfen sayısal bir değer gir (örn. 1, 2, 3...).")
                continue

            # 0 → sadece ACK gönder (DATA yok)
            if length == 0:
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="User-chosen ACK-only",
                )
                return pkt

            # Negatif ise tekrar sor
            if length < 0:
                print(f"{self.role}: Negatif uzunluk olamaz.")
                continue

            # Aralık dışında ise tekrar sor
            if length < MIN_SEGMENT_SIZE or length > MAX_SEGMENT_SIZE:
                print(
                    f"{self.role}: Uzunluk {MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE} "
                    f"arasında olmalı."
                )
                continue

            # Buraya geldiysek length geçerli
            break

        # Go-Back-N penceresinden bir sonraki DATA segmentini al
        seq, real_length = self.gbn.next_data_segment(length=length)

        # Basitlik için: ack = peer'den son in-order byte
        ack = self.validator.peer.last_seq + self.validator.peer.last_len
        rwnd = self.current_rwnd
        return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length)

    # ------------------------------------------------------------------ #
    # Incoming packet handling
    # ------------------------------------------------------------------ #

    def _respond_to_incoming(self, pkt: Packet):
        """
        Peer'den bir paket geldiğinde nasıl davranacağımız:
        - ERROR      -> sadece logla ve küçük bir ACK gönder (oyun akışı için)
        - ACK        -> SADECE Go-Back-N penceresini güncelle, CEVAP GÖNDERME
        - DATA       -> validate et, geçerliyse buffer'a yaz + ACK gönder;
                        geçersizse ERROR gönder + puan.
        """

        # 1) Peer bize ERROR gönderdiyse
        if pkt.type == "ERROR":
            self.logger.warning("Peer reported ERROR for our packet.")
            # Küçük bir ACK gönderip oyunu devam ettiriyoruz
            ack_pkt = Packet.make_ack(
                seq=self.validator.peer.expected_seq,
                ack=self.gbn.state.base,
                rwnd=self.current_rwnd,
                comment="After ERROR, continue",
            )
            self._send_packet(ack_pkt)
            # Burada GBN state'ini güncellemiyoruz; bu ACK'i biz gönderiyoruz.
            return

        # 2) Peer bize ACK gönderdiyse -> SADECE pencereyi güncelle, cevap gönderme!
        if pkt.type == "ACK":
            self.logger.info("Received pure ACK from peer.")
            if pkt.ack is not None:
                window_advanced, retransmit_required = self.gbn.on_ack(pkt.ack)

                if window_advanced:
                    self.logger.info(
                        f"Go-Back-N window advanced: base={self.gbn.state.base}, "
                        f"next_seq={self.gbn.state.next_seq}"
                    )

                if retransmit_required:
                    self.logger.warning(
                        f"Go-Back-N triggered (duplicate ACK). "
                        f"Next DATA will be retransmitted from seq={self.gbn.state.base}"
                    )
                    # Bir SONRAKİ DATA gönderiminde RETX olarak işaretle
                    self._pending_retransmit = True
            return

        # Eğer biz rwnd=0 advertise etmiş durumdaysak ve karşı taraf DATA gönderiyorsa → o kaybeder
        if self.current_rwnd == 0 and pkt.type == "DATA":
            self.logger.warning(
                "Peer, rwnd=0 durumundayken DATA gönderdi → kural ihlali, peer puan kaybeder."
            )
            # Biz +1 alıyoruz
            self.scoreboard.detected_error()
            err = Packet.error(comment="DATA sent while advertised rwnd=0")
            self._send_packet(err)
            return

        # 3) Geriye sadece DATA kalıyor, onu validate edeceğiz
        is_valid, reason = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
        self.logger.info(f"Validation result: valid={is_valid}, reason={reason}")

        if not is_valid:
            # Geçersiz DATA -> ERROR gönder, +1 puan alıyoruz
            err = Packet.error(comment=reason)
            self._send_packet(err)
            self.scoreboard.detected_error()
            self.logger.info(
                f"Error detected → score updated: {self.scoreboard.snapshot()}"
            )
            return

        # ⬇️ Buradan sonrası SADECE GEÇERLİ DATA için ⬇️
        data_len = pkt.length or 0
        self.recv_buffer_used += data_len
        if self.recv_buffer_used > MAX_RWND:
            self.recv_buffer_used = MAX_RWND  # taşmayı engelle

        self.current_rwnd = MAX_RWND - self.recv_buffer_used

        # window full mü?
        if self.current_rwnd == 0:
            if self.window_full_since is None:
                self.window_full_since = time.time()
        else:
            self.window_full_since = None  # boşaldıysa resetle

        # Geçerli DATA paketi için normal ACK gönder
        new_ack = pkt.seq + (pkt.length or 0)
        ack_pkt = Packet.make_ack(
            seq=self.validator.peer.expected_seq,
            ack=new_ack,
            rwnd=self.current_rwnd,
            comment="Normal ACK",
        )
        self._send_packet(ack_pkt)
        # DİKKAT: Burada GBN state'imizi güncellemiyoruz;
        # bizim GBN sadece ALDIĞIMIZ ACK'lerle güncellenmeli.

    # --- Public game loop -----------------------------------------------

    def run(self):
        self.logger.info("Game starting...")
        start_time = time.time()
        my_turn_to_send = self.starts_first

        try:
            while True:
                now = time.time()

                # Her 30 saniyede bir uygulama tarafı buffer'dan veri işlesin
                if now - self.last_window_update >= 30:
                    if self.recv_buffer_used > 0:
                        processed = max(1, self.recv_buffer_used // 2)
                        self.recv_buffer_used -= processed
                        if self.recv_buffer_used < 0:
                            self.recv_buffer_used = 0

                        self.current_rwnd = MAX_RWND - self.recv_buffer_used
                        self.logger.info(
                            f"Application processed {processed} bytes from receive buffer. "
                            f"recv_buffer_used={self.recv_buffer_used}, rwnd={self.current_rwnd}"
                        )

                        # pencere boşaldıysa full state sıfırlansın
                        if self.current_rwnd > 0:
                            self.window_full_since = None

                    self.last_window_update = now

                elapsed = now - start_time
                remaining = GAME_DURATION_SECONDS - elapsed

                # SÜRE KONTROLÜ
                if remaining <= 0:
                    self.logger.info(
                        f"Remaining time: 0s | {self.scoreboard.snapshot()}"
                    )
                    break

                # Receive window 30 saniye boyunca full kaldıysa
                if self.window_full_since is not None:
                    if now - self.window_full_since >= 30:
                        self.logger.warning(
                            "Receive window 30 saniye boyunca full kaldı → biz suçluyuz, rakibe puan."
                        )
                        # Burada bizim hatamız → scoreboard.py'de bu olaya göre puanlama yapıldığına emin ol
                        self.scoreboard.opponent_timeout()
                        break

                # Buradan sonrası sadece süre > 0 iken çalışır
                self.logger.info(
                    f"Remaining time: {int(remaining)}s | {self.scoreboard.snapshot()}"
                )

                if my_turn_to_send:
                    try:
                        pkt = self._create_next_data_packet()
                    except RuntimeError:
                        # Pencere dolu -> sadece ACK gönder
                        ack = self.validator.peer.last_seq + self.validator.peer.last_len
                        pkt = Packet.make_ack(
                            seq=self.gbn.state.next_seq,
                            ack=ack,
                            rwnd=self.current_rwnd,
                            comment="Window full, ACK-only",
                        )
                    self._send_packet(pkt)
                    my_turn_to_send = False

                else:
                    try:
                        pkt = self._receive_packet()
                    except TimeoutError:
                        self.logger.warning("Peer timeout → skor güncellenecek.")
                        self.scoreboard.opponent_timeout()
                        my_turn_to_send = True
                        continue
                    except ConnectionError as e:
                        self.logger.warning(
                            f"Connection closed by peer: {e}. Ending game loop."
                        )
                        break

                    self._respond_to_incoming(pkt)
                    my_turn_to_send = True
                    time.sleep(0.2)  # 200ms bekle, terminali rahatlatır

                # Eğer biz rwnd=0 durumunda kaldıysak ve 30 saniye hiçbir aktivite yoksa
                if self.zero_window_since is not None:
                    if (
                        now - self.zero_window_since >= 30
                        and now - self.last_activity_time >= 30
                    ):
                        self.logger.warning(
                            "30 saniye boyunca rwnd=0 kaldık ve hiç paket alışverişi olmadı → biz puan kaybediyoruz."
                        )
                        # Burada da bizim hatamız; scoreboard.py'de buna göre puan verildiğinden emin ol
                        self.scoreboard.opponent_timeout()
                        break

        except (TimeoutError, ConnectionError) as e:
            self.logger.warning(f"Game ended due to connection problem: {e}")
        finally:
            self.logger.info("Game duration finished.")
            self._plot_timeline()
            self.conn.close()
            self.logger.info(f"Final score: {self.scoreboard.snapshot()}")

    def _plot_timeline(self):
        """
        Toplanan timeline eventlerini kullanarak grafik oluşturur.
        utils.timeline_plot.plot_timeline fonksiyonunu çağırır.
        """
        if not self.timeline:
            self.logger.info("Timeline boş, çizilecek event yok.")
            return

        try:
            plot_timeline(self.timeline, TIMELINE_PLOT_FILE)
            self.logger.info(f"Timeline grafiği kaydedildi: {TIMELINE_PLOT_FILE}")
        except Exception as e:
            self.logger.warning(f"Timeline çizimi sırasında hata oluştu: {e}")
