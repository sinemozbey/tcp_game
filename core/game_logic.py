# core/game_logic.py

import time
from typing import List
from utils.logger import get_logger
from utils.config import (
    GAME_DURATION_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    MAX_RWND,TIMELINE_PLOT_FILE,
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
        self.gbn = GoBackN(segment_length=1)
        self.scoreboard = Scoreboard()
        self.timeline: List[TimelineEvent] = []

        self.last_sent_seq = 0
        self.current_rwnd = MAX_RWND

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
        kind = pkt.type
        if kind == "DATA":
            k = "DATA"
        elif kind == "ACK":
            k = "ACK"
        else:
            k = "ERROR"
        self._record_event(self.role, "Peer", pkt, k)

        if pkt.seq is not None:
            # Track last sent seq (highest byte index sent)
            end_seq = pkt.seq + (pkt.length or 0)
            if end_seq > self.last_sent_seq:
                self.last_sent_seq = end_seq

    def _receive_packet(self) -> Packet:
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        return pkt

    # --- Game turn logic -------------------------------------------------

    def _create_next_data_packet(self) -> Packet:
        seq, length = self.gbn.next_data_segment()
        # for simplicity, ack = last_sent_seq (cumulative)
        ack = self.validator.peer.last_seq + self.validator.peer.last_len
        rwnd = self.current_rwnd
        return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=length)

    
    def _respond_to_incoming(self, pkt: Packet):
        """
        Peer'den bir paket geldiğinde nasıl davranacağımız:
        - ERROR      -> sadece logla ve küçük bir ACK gönder (oyun akışı için)
        - ACK        -> SADECE Go-Back-N penceresini güncelle, CEVAP GÖNDERME
        - DATA       -> validate et, geçerliyse ACK gönder; geçersizse ERROR gönder
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
            # Go-Back-N'i güncelle
            self.gbn.on_ack(ack_pkt.ack or 0)
            return

        # 2) Peer bize ACK gönderdiyse -> SADECE pencereyi güncelle, cevap gönderme!
        if pkt.type == "ACK":
            self.logger.info("Received pure ACK from peer.")
            if pkt.ack is not None:
                self.gbn.on_ack(pkt.ack)
            return

        # 3) Geriye sadece DATA kalıyor, onu validate edeceğiz
        is_valid, reason = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
        self.logger.info(f"Validation result: valid={is_valid}, reason={reason}")

        if not is_valid:
            # Geçersiz DATA -> ERROR gönder, +1 puan alıyoruz
            err = Packet.error(comment=reason)
            self._send_packet(err)
            self.scoreboard.detected_error()
            self.logger.info(f"Error detected → score updated: {self.scoreboard.snapshot()}")
            return

        # Geçerli DATA paketi için normal ACK gönder
        new_ack = pkt.seq + (pkt.length or 0)
        ack_pkt = Packet.make_ack(
            seq=self.validator.peer.expected_seq,
            ack=new_ack,
            rwnd=self.current_rwnd,
            comment="Normal ACK",
        )
        self._send_packet(ack_pkt)

        # Bizim Go-Back-N tarafımızı da güncelle (simülasyon)
        self.gbn.on_ack(new_ack)


    # --- Public game loop -----------------------------------------------

    def run(self):
        self.logger.info("Game starting...")
        start_time = time.time()
        my_turn_to_send = self.starts_first

        try:
            while True:
                elapsed = time.time() - start_time
                remaining = GAME_DURATION_SECONDS - elapsed

                # SÜRE KONTROLÜ: önce bunu yap
                if remaining <= 0:
                    self.logger.info(f"Remaining time: 0s | {self.scoreboard.snapshot()}")
                    break

                # Buradan sonrası sadece süre > 0 iken çalışır
                self.logger.info(f"Remaining time: {int(remaining)}s | {self.scoreboard.snapshot()}")

                if my_turn_to_send:
                    try:
                        pkt = self._create_next_data_packet()
                    except RuntimeError:
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
                        self.logger.warning("Peer timeout → we gain +1")
                        self.scoreboard.opponent_timeout()
                        my_turn_to_send = True
                        continue
                    except ConnectionError as e:
                        self.logger.warning(f"Connection closed by peer: {e}. Ending game loop.")
                        break

                    self._respond_to_incoming(pkt)
                    my_turn_to_send = True
                    time.sleep(0.01)  # 10ms bekle, terminali rahatlatır


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
            # Burada senin plot_timeline(events, output_file="timeline.png") fonksiyonunu kullanıyoruz
            plot_timeline(self.timeline, TIMELINE_PLOT_FILE)
            self.logger.info(f"Timeline grafiği kaydedildi: {TIMELINE_PLOT_FILE}")
        except Exception as e:
            self.logger.warning(f"Timeline çizimi sırasında hata oluştu: {e}")




        
