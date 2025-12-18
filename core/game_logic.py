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
        
        # OYUN BAŞLANGIÇ ZAMANI
        self.start_time = None

        self._pending_retransmit = False

    def _record_event(self, sender: str, receiver: str, pkt: Packet, kind: str):
        now = time.time()
        self.timeline.append(TimelineEvent(now, sender, receiver, pkt.seq, pkt.ack, pkt.rwnd, pkt.length, kind))

    def _send_packet(self, pkt: Packet):
        self.logger.info(f"Sending: {pkt}")
        self.conn.send_json({"packet": pkt.to_json()})
        self.last_activity_time = time.time()

        kind = pkt.type
        if pkt.type == "DATA":
            if getattr(self, "_pending_retransmit", False):
                kind = "RETX"
                self._pending_retransmit = False
            else:
                kind = "DATA"
        
        self._record_event(self.role, "Peer", pkt, kind)

        if pkt.rwnd == 0:
            if self.zero_window_since is None: self.zero_window_since = time.time()
        else:
            self.zero_window_since = None

        if pkt.seq is not None:
            end_seq = pkt.seq + (pkt.length or 0)
            if end_seq > self.last_sent_seq: self.last_sent_seq = end_seq

    def _receive_packet(self) -> Packet:
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        self.last_activity_time = time.time()

        incoming_comment = (pkt.comment or "").upper()
        if "MISSED_ERROR" in incoming_comment:
            self.scoreboard.my_score += 1
            self.logger.info("🏆 Peer accepted our INVALID packet (Missed Error) → My Score +1")
        elif "FALSE" in incoming_comment and pkt.type == "ERROR":
            self.scoreboard.opponent_score -= 1
            self.logger.info("⬇️ Opponent sent False Alarm (Invalid ERROR) → Opponent -1")
        elif "CORRECT" in incoming_comment and pkt.type == "ERROR":
            self.scoreboard.opponent_score += 1
            self.logger.info("❌ Opponent detected our error (Correct ERROR) → Opponent +1")
        
        return pkt

    def _create_next_data_packet(self) -> Packet:
        raise NotImplementedError("This should be overridden by GameLogicGUI")

    def _respond_to_incoming(self, pkt: Packet) -> bool:
        """
        Gelen paketi işle, skoru güncelle ve karşı tarafa durumu bildir.
        """
        
       # if pkt.type == "DATA" and self.current_rwnd == 0:
       #     self.logger.warning("Peer sent DATA while rwnd=0 → AUTOMATIC ERROR")
       #     self.scoreboard.detected_error()
       #     err = Packet.error(comment="CORRECT: DATA sent while advertised rwnd=0")
       #     self._send_packet(err)
       #     return False

        # --- VALIDATOR'A ZAMAN BİLGİSİ GÖNDERİLİYOR ---
        if self.start_time is None:
            elapsed = 0.0
        else:
            elapsed = time.time() - self.start_time

        is_valid, reason = self.validator.validate(
            pkt, 
            last_sent_seq=self.last_sent_seq,
            elapsed_time=elapsed
        )
        self.logger.info(f"Validation Check: valid={is_valid}, reason={reason}")

        user_decision = self._get_user_decision_for_incoming()
        outgoing_comment_flag = ""

        if user_decision["action"] == "ERROR":
             if not is_valid:
                # Validator FALSE döndü (Paket Hatalı) -> KULLANICI DOĞRU BİLDİ
                self.scoreboard.detected_error()
                outgoing_comment_flag = "CORRECT: User detected error"
                self.logger.info("✅ Correct! Error detected → My Score +1")
             else:
                # Validator TRUE döndü (Paket Doğru) -> KULLANICI YANLIŞ ALARM VERDİ
                self.scoreboard.my_score -= 1
                outgoing_comment_flag = "FALSE: User pressed ERROR but packet was valid"
                self.logger.warning("⚠️ False Alarm! Packet was valid → My Score -1")
             
             err = Packet.error(comment=outgoing_comment_flag)
             self._send_packet(err)
             return False

        else:
            if not is_valid:
                self.scoreboard.opponent_score += 1 
                outgoing_comment_flag = "MISSED_ERROR"
            
            if pkt.type == "DATA":
                if pkt.seq == self.validator.peer.expected_seq:
                    data_len = pkt.length or 0
                    self.recv_buffer_used += data_len
                    #if self.recv_buffer_used > MAX_RWND: self.recv_buffer_used = MAX_RWND
                    self.current_rwnd = MAX_RWND - self.recv_buffer_used
                    self.validator.peer.expected_seq = pkt.seq + data_len
            
            if pkt.type == "ACK" and pkt.ack is not None:
                win_adv, retx_req = self.gbn.on_ack(pkt.ack)
                if retx_req: self._pending_retransmit = True

            self.validator.peer.last_seq = pkt.seq
            self.validator.peer.last_len = pkt.length or 0
            self.validator.peer.last_ack = pkt.ack
            self.validator.peer.last_rwnd = pkt.rwnd

            response_pkt = self._create_response_packet_from_input(user_decision)
            if outgoing_comment_flag:
                response_pkt.comment = f"{response_pkt.comment or ''} [{outgoing_comment_flag}]".strip()
            
            self._send_packet(response_pkt)
            return False
    
    def _get_user_decision_for_incoming(self) -> dict:
        raise NotImplementedError("This should be overridden by GameLogicGUI")
    
    def _create_response_packet_from_input(self, user_input: dict) -> Packet:
        user_seq = user_input["seq"]
        user_ack = user_input["ack"]
        user_rwnd = user_input["rwnd"]
        user_length = user_input["length"]
        
        if user_length == 0:
            return Packet.make_ack(seq=user_seq, ack=user_ack, rwnd=user_rwnd, comment="User ACK")
        else:
            try:
                self.gbn.next_data_segment(length=user_length)
            except RuntimeError:
                pass
            return Packet.data(seq=user_seq, ack=user_ack, rwnd=user_rwnd, length=user_length)

    def run(self):
        self.logger.info("Game starting...")
        self.start_time = time.time()
        start_time = self.start_time
        my_turn_to_send = self.starts_first

        try:
            while True:
                now = time.time()
                
                if now - self.last_window_update >= BUFFER_DRAIN_INTERVAL_SECONDS:
                    if self.recv_buffer_used > 0:
                        drain_amount = 20
                        if self.recv_buffer_used <= drain_amount: self.recv_buffer_used = 0
                        else: self.recv_buffer_used -= drain_amount
                        self.current_rwnd = MAX_RWND - self.recv_buffer_used
                        self.last_window_update = now
                
                elapsed = now - start_time
                remaining = GAME_DURATION_SECONDS - elapsed
                if remaining <= 0: break
                
                # Window Stall ve Timeout kontrolleri...
                if self.window_full_since is not None:
                     if now - self.window_full_since >= WINDOW_STALL_TIMEOUT_SECONDS:
                        self.scoreboard.opponent_timeout()
                        break

                if my_turn_to_send:
                    pkt = self._create_next_data_packet()
                    self._send_packet(pkt)
                    my_turn_to_send = False
                else:
                    try:
                        pkt = self._receive_packet()
                    except (TimeoutError, ConnectionError):
                        break
                    my_turn_to_send = self._respond_to_incoming(pkt)
                    time.sleep(0.2)
                
                if self.zero_window_since is not None:
                     if (now - self.zero_window_since >= WINDOW_STALL_TIMEOUT_SECONDS and 
                         now - self.last_activity_time >= WINDOW_STALL_TIMEOUT_SECONDS):
                        self.scoreboard.opponent_timeout()
                        break

        except Exception as e:
            self.logger.warning(f"Game ended: {e}")
        finally:
            self.conn.close()
            self._plot_timeline()