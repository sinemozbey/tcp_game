# core/game_logic.py

import time
from typing import List, Optional

from utils.logger import get_logger
from utils.config import (
    GAME_DURATION_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    BUFFER_DRAIN_INTERVAL_SECONDS,
    WINDOW_STALL_TIMEOUT_SECONDS,
    MAX_RWND,
    TIMELINE_PLOT_FILE,
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

    def __init__(
        self,
        role_name: str,
        conn: Connection,
        starts_first: bool,
    ):
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
        self.recv_buffer_used = 0
        self.last_window_update = time.time()
        self.window_full_since = None
        self.zero_window_since = None
        self.last_activity_time = time.time()

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
            if getattr(self, "_pending_retransmit", False):
                kind = "RETX"
                self.logger.warning(f"Retransmitting segment seq={pkt.seq}")
                self._pending_retransmit = False
            else:
                kind = "DATA"
        elif pkt.type == "ACK":
            kind = "ACK"
        elif pkt.type == "ERROR":
            kind = "ERROR"
        else:
            kind = pkt.type

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
        """
        Soketten paketi okur ve SKOR SENKRONİZASYONUNU anında yapar.
        """
        # Paketi ham olarak al
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        self.last_activity_time = time.time()

        # ================================================================== #
        # SKOR SENKRONİZASYONU (BURAYA TAŞINDI)
        # ================================================================== #
        # Paketi alır almaz içindeki notları (comment) işle.
        # Böylece GUI, kullanıcıdan henüz input beklemeden skoru güncelleyebilir.
        
        incoming_comment = (pkt.comment or "").upper()

        if "MISSED_ERROR" in incoming_comment:
            # Biz hatalı yollamışız, rakip yemiş (kabul etmiş). Biz kazanırız.
            self.scoreboard.my_score += 1
            self.logger.info("🏆 Peer accepted our INVALID packet (Missed Error) → My Score +1")

        elif "FALSE" in incoming_comment and pkt.type == "ERROR":
            # Biz doğru yollamışız, rakip yanlış alarm vermiş. Rakip kaybeder.
            self.scoreboard.opponent_score -= 1
            self.logger.info("⬇️ Opponent sent False Alarm (Invalid ERROR) → Opponent -1")

        elif "CORRECT" in incoming_comment and pkt.type == "ERROR":
            # Biz hatalı yollamışız, rakip yakalamış. Rakip kazanır.
            self.scoreboard.opponent_score += 1
            self.logger.info("❌ Opponent detected our error (Correct ERROR) → Opponent +1")
        
        # ================================================================== #

        return pkt

    # --- Packet creation (user controlled) ---------------------------

    def _create_next_data_packet(self) -> Packet:
        """
        Bu metod GUI tarafından override edilecek.
        """
        raise NotImplementedError("This should be overridden by GameLogicGUI")

    # ------------------------------------------------------------------ #
    # Incoming packet handling
    # ------------------------------------------------------------------ #

    def _respond_to_incoming(self, pkt: Packet) -> bool:
        """
        Gelen paketi işle, skoru güncelle ve karşı tarafa durumu bildir.
        """
        # ... (Önceki skor senkronizasyon kodları buraya gelecek - aynı kalıyor) ...

        # ================================================================== #
        # ADIM 1: GELEN PAKETİN GEÇERLİLİĞİNİ KONTROL ET
        # ================================================================== #
        
        # DATA paketleri için rwnd=0 ihlali kontrolü
        if pkt.type == "DATA" and self.current_rwnd == 0:
            self.logger.warning("Peer sent DATA while rwnd=0 → AUTOMATIC ERROR")
            self.scoreboard.detected_error()
            err = Packet.error(comment="CORRECT: DATA sent while advertised rwnd=0")
            self._send_packet(err)
            return False

        # Standart Validator kontrolü
        is_valid, reason = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
        self.logger.info(f"Validation Check: valid={is_valid}, reason={reason}")

        # ================================================================== #
        # ADIM 2: KULLANICI KARARINI AL VE YANIT OLUŞTUR
        # ================================================================== #
        
        user_decision = self._get_user_decision_for_incoming()
        outgoing_comment_flag = ""

        if user_decision["action"] == "ERROR":
             # ... (Hata işleme mantığı aynı kalıyor) ...
             if not is_valid:
                self.scoreboard.detected_error()
                outgoing_comment_flag = "CORRECT: User detected error"
             else:
                self.scoreboard.my_score -= 1
                outgoing_comment_flag = "FALSE: User pressed ERROR but packet was valid"
             
             err = Packet.error(comment=outgoing_comment_flag)
             self._send_packet(err)
             return False

        else:
            # --- KULLANICI "SEND" TUŞUNA BASTI (KABUL ETTİ) ---
            if not is_valid:
                self.scoreboard.opponent_score += 1 
                outgoing_comment_flag = "MISSED_ERROR"
            
            # -- Protokol İşlemleri (Buffer ve Window Güncelleme) --
            if pkt.type == "DATA":
                # DÜZELTME: Sadece YENİ (beklenen) veri geldiğinde buffer artmalı!
                # Eski/Tekrar paketler buffer'da yer kaplamaz (zaten oradadır).
                if pkt.seq == self.validator.peer.expected_seq:
                    data_len = pkt.length or 0
                    self.recv_buffer_used += data_len
                    
                    if self.recv_buffer_used > MAX_RWND: 
                        self.recv_buffer_used = MAX_RWND
                    
                    self.current_rwnd = MAX_RWND - self.recv_buffer_used
                else:
                    self.logger.info(f"Duplicate/Old data (seq={pkt.seq}), buffer not changed.")

                # Beklenen seq güncellemesi
                new_ack = pkt.seq + (pkt.length or 0)
                # Sadece ileri gidiyorsak güncelle (Validator zaten gap kontrolü yaptı)
                if new_ack > self.validator.peer.expected_seq:
                    self.validator.peer.expected_seq = new_ack
            
            if pkt.type == "ACK":
                if pkt.ack is not None:
                    win_adv, retx_req = self.gbn.on_ack(pkt.ack)
                    if retx_req: self._pending_retransmit = True

            # State güncelle
            self.validator.peer.last_seq = pkt.seq
            self.validator.peer.last_len = pkt.length or 0
            self.validator.peer.last_ack = pkt.ack
            self.validator.peer.last_rwnd = pkt.rwnd

            # Yanıt paketini oluştur
            response_pkt = self._create_response_packet_from_input(user_decision)
            
            if outgoing_comment_flag:
                response_pkt.comment = f"{response_pkt.comment or ''} [{outgoing_comment_flag}]".strip()
            
            self._send_packet(response_pkt)
            return False
    
    def _get_user_decision_for_incoming(self) -> dict:
        """
        Gelen paket için kullanıcıdan karar al: SEND veya ERROR?
        Bu metod GUI tarafından override edilecek.
        Dönüş: {"action": "SEND" veya "ERROR", "seq": ..., "ack": ..., "rwnd": ..., "length": ...}
        """
        raise NotImplementedError("This should be overridden by GameLogicGUI")
    
    def _create_response_packet_from_input(self, user_input: dict) -> Packet:
        """
        Kullanıcının girdiği değerlerden yanıt paketi oluştur.
        """
        user_seq = user_input["seq"]
        user_ack = user_input["ack"]
        user_rwnd = user_input["rwnd"]
        user_length = user_input["length"]
        
        auto_seq = self.gbn.state.next_seq
        auto_ack = self.validator.peer.expected_seq
        auto_rwnd = self.current_rwnd
        
        is_correct = (user_seq == auto_seq and user_ack == auto_ack and user_rwnd == auto_rwnd)
        
        if not is_correct:
            self.logger.warning(
                f"User sent INCORRECT! Expected: seq={auto_seq}, ack={auto_ack}, rwnd={auto_rwnd}"
            )
        
        if user_length == 0:
            return Packet.make_ack(seq=user_seq, ack=user_ack, rwnd=user_rwnd, comment="User ACK")
        else:
            try:
                self.gbn.next_data_segment(length=user_length)
            except RuntimeError:
                self.logger.warning("GBN window full")
            
            return Packet.data(seq=user_seq, ack=user_ack, rwnd=user_rwnd, length=user_length)

    # --- Public game loop -----------------------------------------------

    def run(self):
        self.logger.info("Game starting...")
        start_time = time.time()
        my_turn_to_send = self.starts_first

        try:
            while True:
                now = time.time()

                # Her 15 saniyede buffer'dan veri işle
                if now - self.last_window_update >= BUFFER_DRAIN_INTERVAL_SECONDS:
                    if self.recv_buffer_used > 0:
                        drain_amount = 20
                        
                        if self.recv_buffer_used <= drain_amount:
                            self.recv_buffer_used = 0
                        else:
                            self.recv_buffer_used -= drain_amount

                        self.current_rwnd = MAX_RWND - self.recv_buffer_used
                        self.logger.info(
                            f"Application processed {drain_amount} bytes. "
                            f"recv_buffer_used={self.recv_buffer_used}, rwnd={self.current_rwnd}"
                        )

                        if self.current_rwnd > 0:
                            self.window_full_since = None

                    self.last_window_update = now

                elapsed = now - start_time
                remaining = GAME_DURATION_SECONDS - elapsed

                # Süre kontrolü
                if remaining <= 0:
                    self.logger.info(f"Remaining time: 0s | {self.scoreboard.snapshot()}")
                    break

                # Window stall kontrolü
                if self.window_full_since is not None:
                    if now - self.window_full_since >= WINDOW_STALL_TIMEOUT_SECONDS:
                        self.logger.warning("Receive window full timeout → opponent gains point")
                        self.scoreboard.opponent_timeout()
                        break

                self.logger.info(f"Remaining time: {int(remaining)}s | {self.scoreboard.snapshot()}")

                if my_turn_to_send:
                    pkt = self._create_next_data_packet()
                    self._send_packet(pkt)
                    my_turn_to_send = False

                else:
                    try:
                        pkt = self._receive_packet()
                    except TimeoutError:
                        self.logger.warning("Peer timeout → score updated")
                        self.scoreboard.opponent_timeout()
                        my_turn_to_send = True
                        continue
                    except ConnectionError as e:
                        self.logger.warning(f"Connection closed: {e}")
                        break

                    # Gelen pakete yanıt ver (_respond_to_incoming dönüşü sıranın kimde olduğunu belirtir)
                    my_turn_to_send = self._respond_to_incoming(pkt)
                    time.sleep(0.2)

                # Zero window kontrolü
                if self.zero_window_since is not None:
                    if (
                        now - self.zero_window_since >= WINDOW_STALL_TIMEOUT_SECONDS
                        and now - self.last_activity_time >= WINDOW_STALL_TIMEOUT_SECONDS
                    ):
                        self.logger.warning("Zero window timeout → we lose point")
                        self.scoreboard.opponent_timeout()
                        break

        except (TimeoutError, ConnectionError) as e:
            self.logger.warning(f"Game ended: {e}")
        finally:
            self.logger.info("Game finished.")
            self._plot_timeline()
            self.conn.close()
            self.logger.info(f"Final score: {self.scoreboard.snapshot()}")

    def _plot_timeline(self):
        if not self.timeline:
            self.logger.info("No timeline events to plot.")
            return

        try:
            plot_timeline(self.timeline, TIMELINE_PLOT_FILE)
            self.logger.info(f"Timeline saved: {TIMELINE_PLOT_FILE}")
        except Exception as e:
            self.logger.warning(f"Timeline plot error: {e}")