# core/game_logic.py

import time
from typing import List, Callable, Optional, Dict

from utils.logger import get_logger
from utils.config import (
    GAME_DURATION_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    BUFFER_DRAIN_INTERVAL_SECONDS,
    WINDOW_STALL_TIMEOUT_SECONDS,
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
    TCP Game (Manual mode).
    GUI + CLI uyumlu SON HAL.
    """

    def __init__(
        self,
        role_name: str,
        conn: Connection,
        starts_first: bool,
        manual_packet_provider: Optional[Callable[[], Dict[str, int]]] = None,
        error_decision_provider: Optional[Callable[[Packet, bool, str], bool]] = None,
    ):
        self.role = role_name
        self.conn = conn
        self.starts_first = starts_first

        self.logger = get_logger(role_name)
        self.validator = PacketValidator()
        self.gbn = GoBackN(segment_length=1)
        self.scoreboard = Scoreboard()
        self.timeline: List[TimelineEvent] = []

        # --- State ---
        self.last_sent_seq = 0
        self.current_rwnd = MAX_RWND
        self.recv_buffer_used = 0
        self.last_window_update = time.time()
        self.zero_window_since: Optional[float] = None
        self.last_activity_time = time.time()

        # GUI hooks
        self.manual_packet_provider = manual_packet_provider
        self.error_decision_provider = error_decision_provider

    # ==================================================
    # GUI SAFE HOOKS (GUI yoksa sessizce geçer)
    # ==================================================

    def _gui_turn(self, msg: str):
        if hasattr(self, "ui"):
            self.ui.set_your_turn(msg)

    def _gui_wait(self, msg: str):
        if hasattr(self, "ui"):
            self.ui.set_waiting(msg)

    def _gui_score(self):
        if hasattr(self, "ui"):
            self.ui.update_score(self.scoreboard.my_score)

    # ==================================================
    # Timeline
    # ==================================================

    def _record_event(self, sender: str, receiver: str, pkt: Packet, kind: str):
        self.timeline.append(
            TimelineEvent(
                timestamp=time.time(),
                sender=sender,
                receiver=receiver,
                seq=pkt.seq,
                ack=pkt.ack,
                rwnd=pkt.rwnd,
                length=pkt.length,
                kind=kind,
            )
        )

    # ==================================================
    # Send / Receive
    # ==================================================

    def _send_packet(self, pkt: Packet):
        self.logger.info(f"Sending: {pkt}")
        self.conn.send_json({"packet": pkt.to_json()})
        self.last_activity_time = time.time()

        self._record_event(self.role, "Peer", pkt, pkt.type)
        self._gui_wait("Packet sent. Waiting for peer…")

        if pkt.rwnd == 0:
            if self.zero_window_since is None:
                self.zero_window_since = time.time()
        else:
            self.zero_window_since = None

        if pkt.type == "DATA" and pkt.seq is not None:
            end_seq = pkt.seq + (pkt.length or 0)
            self.last_sent_seq = max(self.last_sent_seq, end_seq)
            self.gbn.state.next_seq = max(self.gbn.state.next_seq, end_seq)

    def _receive_packet(self) -> Packet:
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.last_activity_time = time.time()
        self._record_event("Peer", self.role, pkt, pkt.type)
        return pkt

    # ==================================================
    # Manual packet creation
    # ==================================================

    def _create_manual_packet(self) -> Packet:
        data = self.manual_packet_provider()
        seq = int(data["seq"])
        ack = int(data["ack"])
        rwnd = max(0, min(MAX_RWND, int(data["rwnd"])))
        length = int(data["length"])

        if length == 0:
            return Packet.make_ack(seq=seq, ack=ack, rwnd=rwnd, comment="Manual ACK")

        length = max(MIN_SEGMENT_SIZE, min(MAX_SEGMENT_SIZE, length))
        return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=length, comment="Manual DATA")

    # ==================================================
    # Incoming handling
    # ==================================================

    def _respond_to_incoming(self, pkt: Packet):
        if pkt.type == "ERROR":
            self.logger.warning("Peer sent ERROR.")
            return

        if pkt.type == "ACK":
            self.gbn.on_ack(pkt.ack)
            return

        is_valid, reason = self.validator.validate(pkt, self.last_sent_seq)

        self._gui_turn("Incoming DATA – decide ACCEPT or ERROR")
        send_error = self.error_decision_provider(pkt, is_valid, reason)

        if send_error:
            self._send_packet(Packet.error("Manual ERROR"))
            if not is_valid:
                self.scoreboard.opponent_made_error()
            else:
                self.scoreboard.i_made_error()
            self._gui_score()
            return

        if not is_valid:
            self.scoreboard.my_error_undetected()
            self._gui_score()

        data_len = pkt.length or 0
        self.recv_buffer_used = min(MAX_RWND, self.recv_buffer_used + data_len)
        self.current_rwnd = MAX_RWND - self.recv_buffer_used

        ack_val = (pkt.seq or 0) + (pkt.length or 0)
        self._send_packet(
            Packet.make_ack(
                seq=self.validator.peer.expected_seq,
                ack=ack_val,
                rwnd=self.current_rwnd,
                comment="Auto ACK",
            )
        )

    # ==================================================
    # Main loop
    # ==================================================

    def run(self):
        self.logger.info("🎮 Game starting (MANUAL mode)...")

        my_turn = self.starts_first
        if my_turn:
            self._gui_turn("Game start. Your turn.")
        else:
            self._gui_wait("Peer starts.")

        start_time = time.time()

        while True:
            now = time.time()

            if now - start_time > GAME_DURATION_SECONDS:
                break

            if now - self.last_window_update >= BUFFER_DRAIN_INTERVAL_SECONDS:
                if self.recv_buffer_used > 0:
                    self.recv_buffer_used = max(0, self.recv_buffer_used // 2)
                    self.current_rwnd = MAX_RWND - self.recv_buffer_used
                self.last_window_update = now

            if self.zero_window_since and now - self.zero_window_since >= WINDOW_STALL_TIMEOUT_SECONDS:
                self.scoreboard.my_timeout()
                self._gui_score()
                break

            if my_turn:
                self._gui_turn("Your turn – create packet")
                pkt = self._create_manual_packet()
                self._send_packet(pkt)
                my_turn = False
            else:
                try:
                    pkt = self._receive_packet()
                except TimeoutError:
                    self.scoreboard.opponent_timeout()
                    self._gui_score()
                    my_turn = True
                    continue

                self._respond_to_incoming(pkt)
                my_turn = True

        self.logger.info("🏁 Game finished.")
        self.conn.close()
        self._plot_timeline()

    # ==================================================
    # Timeline plot
    # ==================================================

    def _plot_timeline(self):
        if not self.timeline:
            return
        try:
            plot_timeline(self.timeline, TIMELINE_PLOT_FILE)
        except Exception as e:
            self.logger.warning(f"Timeline plot error: {e}")
