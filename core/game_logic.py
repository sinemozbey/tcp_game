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
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        self.last_activity_time = time.time()
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
        Peer'den paket geldiğinde kullanıcıya göster ve kararını bekle.
        
        Dönüş: True ise sıra bize geçti (paket göndereceğiz), False ise bekleyeceğiz
        """

        # 1) ERROR paketi geldiyse
        if pkt.type == "ERROR":
            self.logger.warning("Peer reported ERROR for our packet.")
            self.logger.info("Our last packet was rejected. We should resend.")
            
            # Karşı taraf bizim paketimizi reddetti
            # Biz önceki paketimizi tekrar göndereceğiz (kullanıcı tekrar girecek)
            # Sıra bizde
            return True

        # 2) ACK paketi → validate et ve GBN'i güncelle
        if pkt.type == "ACK":
            self.logger.info("Received ACK from peer.")
            
            # ACK paketini validate et
            is_valid, reason = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
            self.logger.info(f"ACK Validation: valid={is_valid}, reason={reason}")
            
            # Kullanıcıdan karar al: ERROR mi SEND mi?
            user_decision = self._get_user_decision_for_incoming()
            
            if user_decision["action"] == "ERROR":
                # Kullanıcı ERROR bastı
                err = Packet.error(comment="User detected error in ACK")
                self._send_packet(err)
                
                if not is_valid:
                    # Paket gerçekten geçersizdi → +1 puan
                    self.scoreboard.detected_error()
                    self.logger.info("✅ Correct! Error detected in ACK → +1 point")
                else:
                    # Paket geçerliydi ama kullanıcı ERROR bastı → -1 puan
                    self.scoreboard.my_score -= 1
                    self.logger.warning("⚠️ User pressed ERROR but ACK was valid → -1 point")
                # ERROR gönderdik, karşı taraf tekrar gönderecek, biz bekleyeceğiz
                return False
            
            # Kullanıcı SEND bastı (ACK'yi kabul etti)
            if not is_valid:
                # ACK geçersizdi ama kullanıcı fark etmedi → rakip +1 puan
                self.scoreboard.opponent_score += 1
                self.logger.warning("❌ ACK was INVALID but user didn't detect → opponent +1 point")
            
            # GBN'i güncelle
            if pkt.ack is not None:
                window_advanced, retransmit_required = self.gbn.on_ack(pkt.ack)

                if window_advanced:
                    self.logger.info(
                        f"Go-Back-N window advanced: base={self.gbn.state.base}, "
                        f"next_seq={self.gbn.state.next_seq}"
                    )

                if retransmit_required:
                    self.logger.warning("Go-Back-N triggered (duplicate ACK).")
                    self._pending_retransmit = True
            
            # Validator state'i güncelle
            self.validator.peer.last_seq = pkt.seq
            self.validator.peer.last_len = pkt.length or 0
            self.validator.peer.last_ack = pkt.ack
            self.validator.peer.last_rwnd = pkt.rwnd
            if pkt.ack is not None:
                self.validator.peer.expected_seq = pkt.seq + (pkt.length or 0)
            
            # Yanıt paketi gönder
            response_pkt = self._create_response_packet_from_input(user_decision)
            self._send_packet(response_pkt)
            
            # Yanıt gönderdik, sıra karşı tarafta
            return False

        # 3) rwnd=0 iken DATA geldiyse otomatik ERROR
        if self.current_rwnd == 0 and pkt.type == "DATA":
            self.logger.warning("Peer sent DATA while rwnd=0 → rule violation")
            self.scoreboard.detected_error()
            err = Packet.error(comment="DATA sent while advertised rwnd=0")
            self._send_packet(err)
            # ERROR gönderdik, sıra karşı tarafta
            return False

        # 4) DATA paketi → validate et (ama henüz yanıt verme!)
        is_valid, reason = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
        self.logger.info(f"Validation: valid={is_valid}, reason={reason}")

        # ⚠️ KULLANICIYA GÖSTER VE KARARINI BEKLE ⚠️
        user_decision = self._get_user_decision_for_incoming()

        if user_decision["action"] == "ERROR":
            # Kullanıcı ERROR bastı
            err = Packet.error(comment="User detected error")
            self._send_packet(err)
            
            if not is_valid:
                # Paket gerçekten geçersizdi → +1 puan
                self.scoreboard.detected_error()
                self.logger.info("✅ Correct! Error detected → +1 point")
            else:
                # Paket geçerliydi ama kullanıcı ERROR bastı → -1 puan
                self.scoreboard.my_score -= 1
                self.logger.warning("⚠️ User pressed ERROR but packet was valid → -1 point")
            # ERROR gönderdik, karşı taraf tekrar gönderecek, biz bekleyeceğiz
            return False

        # Kullanıcı SEND bastı (normal yanıt gönderecek)
        if not is_valid:
            # Paket geçersizdi ama kullanıcı fark etmedi → rakip +1 puan
            self.scoreboard.opponent_score += 1
            self.logger.warning("❌ Packet was INVALID but user didn't detect → opponent +1 point")
        
        # Buffer ve window state'i güncelle
        data_len = pkt.length or 0
        self.recv_buffer_used += data_len
        if self.recv_buffer_used > MAX_RWND:
            self.recv_buffer_used = MAX_RWND
        
        self.current_rwnd = MAX_RWND - self.recv_buffer_used
        
        # Validator state'i güncelle
        new_ack = pkt.seq + (pkt.length or 0)
        self.validator.peer.last_seq = pkt.seq
        self.validator.peer.last_len = pkt.length
        self.validator.peer.expected_seq = new_ack
        
        # ⚠️ YANIT PAKETİNİ OLUŞTUR VE GÖNDER ⚠️
        # user_decision içinde zaten seq, ack, rwnd, length bilgileri var
        response_pkt = self._create_response_packet_from_input(user_decision)
        self._send_packet(response_pkt)
        
        # Yanıt gönderdik, sıra karşı tarafta (onlar yeni paket gönderecek)
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