# gui_client.py

import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from core.game_logic import GameLogic
from core.packet import Packet
from utils.config import (
    MIN_SEGMENT_SIZE,
    MAX_SEGMENT_SIZE,
    ROLE_A_NAME,
    ROLE_B_NAME,
)


# ===================================================================== #
#  TOP SECTION: ANIMATION PANEL (LOCAL <-> PEER PACKET FLOW)
# ===================================================================== #

class TrafficCanvas(ttk.Frame):
    """
    Top animation area.

    Shows:
      - LOCAL endpoint box on the left
      - PEER endpoint box on the right
      - A moving "ball" in between representing TCP packets
    """

    def __init__(self, master: tk.Misc, local_name: str, peer_name: str, **kwargs):
        super().__init__(master, **kwargs)

        self.local_name = local_name
        self.peer_name = peer_name

        self.canvas = tk.Canvas(self, height=140, bg="#f7f7f7", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=8)

        self.local_box = None
        self.peer_box = None
        self.local_label = None
        self.peer_label = None
        self.line_id = None

        # Current packet (ball) and animation queue
        self.current_ball = None
        self.current_ball_text = None
        self.anim_queue: "queue.Queue[dict]" = queue.Queue()
        self.anim_running = False

        self._build_static()
        # Periodic animation loop
        self.after(40, self._animation_step)

    def _build_static(self):
        # Static positions (like a simple layout)
        x_local = 80
        x_peer = 420
        y_center = 80

        # LOCAL and PEER rectangles
        self.local_box = self.canvas.create_rectangle(
            x_local - 40, y_center - 25, x_local + 40, y_center + 25,
            outline="#4a90e2", width=2, fill="#ffffff"
        )
        self.peer_box = self.canvas.create_rectangle(
            x_peer - 40, y_center - 25, x_peer + 40, y_center + 25,
            outline="#e67e22", width=2, fill="#ffffff"
        )

        self.local_label = self.canvas.create_text(
            x_local, y_center + 38, text=self.local_name, font=("Helvetica", 9, "bold")
        )
        self.peer_label = self.canvas.create_text(
            x_peer, y_center + 38, text=self.peer_name, font=("Helvetica", 9, "bold")
        )

        # Dashed line between LOCAL and PEER
        self.line_id = self.canvas.create_line(
            x_local + 40, y_center, x_peer - 40, y_center,
            dash=(4, 2), fill="#999999"
        )

    # ------------------------------------------------------------------ #
    #  Public API: enqueue a new packet animation
    # ------------------------------------------------------------------ #

    def enqueue_packet(
        self,
        direction: str,
        kind: str,
        seq: int | None,
        ack: int | None,
        length: int | None,
    ):
        """
        direction: "outgoing" (local -> peer) or "incoming" (peer -> local)
        kind: "DATA", "ACK", "ERROR", "RETX"
        """
        self.anim_queue.put(
            {
                "direction": direction,
                "kind": kind,
                "seq": seq,
                "ack": ack,
                "length": length,
            }
        )

    # ------------------------------------------------------------------ #
    #  Animation loop
    # ------------------------------------------------------------------ #

    def _start_next_ball(self, item: dict):
        # Clear previous ball (if any)
        if self.current_ball is not None:
            self.canvas.delete(self.current_ball)
            self.current_ball = None
        if self.current_ball_text is not None:
            self.canvas.delete(self.current_ball_text)
            self.current_ball_text = None

        # Line coordinates
        x1, y1, x2, y2 = self.canvas.coords(self.line_id)
        direction = item["direction"]

        if direction == "outgoing":
            x = x1
            dx = +6
        else:  # "incoming"
            x = x2
            dx = -6

        y = y1

        # Packet ball
        r = 7
        color_map = {
            "DATA": "#3498db",
            "ACK": "#2ecc71",
            "ERROR": "#e74c3c",
            "RETX": "#f39c12",
        }
        color = color_map.get(item["kind"], "#34495e")

        self.current_ball = self.canvas.create_oval(
            x - r, y - r, x + r, y + r, fill=color, outline=""
        )

        label = item["kind"]
        if item["seq"] is not None:
            label += f" s={item['seq']}"
        if item["ack"] is not None:
            label += f" a={item['ack']}"
        if item["length"] is not None:
            label += f" len={item['length']}"

        self.current_ball_text = self.canvas.create_text(
            x, y - 15, text=label, font=("Helvetica", 8)
        )

        self.anim_running = True
        # Store delta as tag (for direction)
        self.canvas.itemconfig(self.current_ball, tags=("ball", str(dx)))
        self.canvas.itemconfig(self.current_ball_text, tags=("ball_text", str(dx)))

    def _animation_step(self):
        """
        Called every 40 ms: moves current ball or starts the next one.
        """
        if self.anim_running and self.current_ball is not None:
            x1, y1, x2, y2 = self.canvas.coords(self.current_ball)
            line_x1, _, line_x2, _ = self.canvas.coords(self.line_id)
            tags = self.canvas.gettags(self.current_ball)
            dx = int(tags[1]) if len(tags) > 1 else 6

            # Move until reaching the other side
            if (dx > 0 and x2 < line_x2) or (dx < 0 and x1 > line_x1):
                self.canvas.move(self.current_ball, dx, 0)
                self.canvas.move(self.current_ball_text, dx, 0)
            else:
                # Reached the target → clear and stop
                self.canvas.delete(self.current_ball)
                self.canvas.delete(self.current_ball_text)
                self.current_ball = None
                self.current_ball_text = None
                self.anim_running = False

        # If no animation is running and there is a queued item → start it
        if not self.anim_running and not self.anim_queue.empty():
            item = self.anim_queue.get_nowait()
            self._start_next_ball(item)

        # Schedule next step
        self.after(40, self._animation_step)


# ===================================================================== #
#  SINGLE CLIENT WINDOW (LOCAL FORM + LOG PANEL)
# ===================================================================== #

class SingleClientGUI(ttk.Frame):
    """
    UI for a single endpoint (ClientA or ClientB).

    Layout:
      - Top: packet animation (TrafficCanvas)
      - Middle: packet control (DATA length input)
      - Bottom: local log area
    """

    def __init__(self, root: tk.Tk, role: str):
        super().__init__(root, padding=10)

        self.root = root
        self.role = role
        self.min_size = MIN_SEGMENT_SIZE
        self.max_size = MAX_SEGMENT_SIZE

        # Resolve peer name from config
        self.peer_name = ROLE_B_NAME if role == ROLE_A_NAME else ROLE_A_NAME

        self._length_queue: "queue.Queue[int]" = queue.Queue()

        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI setup
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self.root.title(f"TCP Game – {self.role}")
        self.root.geometry("900x600")

        # Header
        header = ttk.Label(
            self,
            text=f"TCP Game – {self.role}",
            font=("Helvetica", 16, "bold"),
        )
        header.pack(anchor="center", pady=(0, 4))

        sub = ttk.Label(
            self,
            text=(
                f"This window represents one endpoint. LOCAL side: {self.role}.\n"
                f"The left box is LOCAL, the right box is PEER ({self.peer_name}). "
                f"The moving ball between them visualizes the TCP packet flow."
            ),
            justify="center",
        )
        sub.pack(pady=(0, 10))

        # Top: animation area
        self.traffic = TrafficCanvas(self, local_name=self.role, peer_name=self.peer_name)
        self.traffic.pack(fill="x", pady=(0, 8))

        # Middle: Packet Control
        mid_frame = ttk.LabelFrame(self, text="Packet Control", padding=10)
        mid_frame.pack(fill="x", pady=(4, 8))

        inner = ttk.Frame(mid_frame)
        inner.pack(fill="x")

        info = ttk.Label(
            inner,
            text=(
                f"DATA length: {self.min_size}-{self.max_size}\n"
                f"0 = send ACK only (no DATA)"
            ),
            justify="left",
        )
        info.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self.length_var = tk.StringVar()
        entry = ttk.Entry(inner, textvariable=self.length_var, width=10, justify="center")
        entry.grid(row=1, column=0, padx=(0, 8))
        entry.focus_set()

        send_btn = ttk.Button(inner, text="Send", command=self._on_send_clicked)
        send_btn.grid(row=1, column=1, padx=(0, 8))

        self.last_len_var = tk.StringVar(value="Last entered length = -")
        last_lbl = ttk.Label(inner, textvariable=self.last_len_var)
        last_lbl.grid(row=1, column=2, sticky="w")

        # Bottom: Local Info (log panel)
        bottom = ttk.LabelFrame(self, text="Local Info", padding=8)
        bottom.pack(fill="both", expand=True)

        self.log = tk.Text(bottom, height=14, wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "GUI initialized. Waiting for game loop...\n")
        self.log.configure(state="disabled")

        self.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ #
    #  User events
    # ------------------------------------------------------------------ #

    def _on_send_clicked(self):
        text = self.length_var.get().strip()

        if text == "":
            length = 1
        else:
            try:
                length = int(text)
            except ValueError:
                messagebox.showerror(
                    "Invalid value",
                    "Please enter a numeric value (0, 1, 2, 3 ...).",
                )
                return

        if length < 0:
            messagebox.showerror(
                "Invalid value",
                "Negative length is not allowed.",
            )
            return

        if length != 0 and (length < self.min_size or length > self.max_size):
            messagebox.showerror(
                "Invalid value",
                f"Length must be between {self.min_size}-{self.max_size} "
                f"or 0 for ACK-only.",
            )
            return

        # Valid input
        self._length_queue.put(length)
        self.last_len_var.set(f"Last entered length = {length}")
        self.length_var.set("")

        self.append_log(f"User input: length={length}")

    # ------------------------------------------------------------------ #
    #  API used by GameLogic
    # ------------------------------------------------------------------ #

    def get_next_length_blocking(self) -> int:
        """Block until the user provides a length."""
        return self._length_queue.get()

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # Convenience methods for animation
    def animate_send(self, pkt: Packet):
        self.traffic.enqueue_packet(
            direction="outgoing",
            kind=pkt.type,
            seq=pkt.seq,
            ack=pkt.ack,
            length=pkt.length,
        )

    def animate_recv(self, pkt: Packet):
        self.traffic.enqueue_packet(
            direction="incoming",
            kind=pkt.type,
            seq=pkt.seq,
            ack=pkt.ack,
            length=pkt.length,
        )


# ===================================================================== #
#  GAME LOGIC + GUI INTEGRATION
# ===================================================================== #

class GameLogicGUI(GameLogic):
    """
    GameLogic + single-window GUI integration.

    - _create_next_data_packet: gets DATA length from the GUI.
    - _send_packet / _receive_packet: add logging + animation hooks.
    """

    def __init__(self, role_name, conn, starts_first, ui: SingleClientGUI):
        super().__init__(role_name, conn, starts_first)
        self.ui = ui

    # ---- SENDER SIDE ----------------------------------------------------

    def _create_next_data_packet(self) -> Packet:
        """
        Overridden version that gets input from the GUI instead of CLI.
        """
        while True:
            length = self.ui.get_next_length_blocking()

            # 0 → send ACK-only (no DATA)
            if length == 0:
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="GUI ACK-only",
                )
                self.ui.append_log(
                    f"Preparing ACK-only packet: seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}"
                )
                return pkt

            # Normal DATA
            try:
                seq, real_length = self.gbn.next_data_segment(length=length)
            except RuntimeError:
                # Send ACK-only when window is full
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="Window full, ACK-only (GUI)",
                )
                self.ui.append_log(
                    "Window full → sending ACK-only instead of DATA."
                )
                return pkt

            ack = self.validator.peer.last_seq + self.validator.peer.last_len
            rwnd = self.current_rwnd
            self.ui.append_log(
                f"Preparing DATA packet: seq={seq}, len={real_length}, ack={ack}, rwnd={rwnd}"
            )
            return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length)

    def _send_packet(self, pkt: Packet):
        # Log + animation first
        self.ui.append_log(
            f"SEND → {pkt.type} | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
        )
        self.ui.animate_send(pkt)
        # Then call original behavior
        super()._send_packet(pkt)

    # ---- RECEIVER SIDE --------------------------------------------------

    def _receive_packet(self) -> Packet:
        pkt = super()._receive_packet()
        self.ui.append_log(
            f"RECV ← {pkt.type} | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
        )
        self.ui.animate_recv(pkt)
        return pkt


# ===================================================================== #
#  PUBLIC ENTRY POINT
# ===================================================================== #

def run_gui_client(role: str, conn, starts_first: bool):
    """
    Entry point used by client_A.py and client_B.py.

    Each process opens its own window representing one TCP endpoint.
    """
    root = tk.Tk()
    ui = SingleClientGUI(root, role=role)
    game = GameLogicGUI(role, conn, starts_first, ui)

    t = threading.Thread(target=game.run, daemon=True)
    t.start()

    try:
        root.mainloop()
    finally:
        try:
            conn.close()
        except Exception:
            pass
