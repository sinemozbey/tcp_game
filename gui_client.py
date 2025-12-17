# gui_client.py

import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core.game_logic import GameLogic
from core.packet import Packet
from core.connection import Connection
from utils.config import (
    MIN_SEGMENT_SIZE,
    MAX_SEGMENT_SIZE,
    HOST,
    PORT,
    RESPONSE_TIMEOUT_SECONDS,
    MAX_RWND,
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

        self._send_queue: "queue.Queue[dict]" = queue.Queue()
        self._pretend_error_next = False
        self._pretend_lock = threading.Lock()

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

        self.invalid_rwnd_var = tk.BooleanVar(value=False)
        invalid_chk = ttk.Checkbutton(
            inner,
            text="Send invalid rwnd (rwnd > 50) once",
            variable=self.invalid_rwnd_var,
        )
        invalid_chk.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        send_btn = ttk.Button(inner, text="Send", command=self._on_send_clicked)
        send_btn.grid(row=1, column=1, padx=(0, 8))

        self.last_len_var = tk.StringVar(value="Last entered length = -")
        last_lbl = ttk.Label(inner, textvariable=self.last_len_var)
        last_lbl.grid(row=1, column=2, sticky="w")

        pretend_btn = ttk.Button(
            inner,
            text="Pretend ERROR (next incoming DATA)",
            command=self._on_pretend_error_clicked,
        )
        pretend_btn.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

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

        invalid_rwnd = bool(self.invalid_rwnd_var.get())
        # Valid input
        self._send_queue.put({"length": length, "invalid_rwnd": invalid_rwnd})
        self.last_len_var.set(f"Last entered length = {length}")
        self.length_var.set("")
        self.invalid_rwnd_var.set(False)

        if invalid_rwnd:
            self.append_log(f"User input: length={length} (will send invalid rwnd)")
        else:
            self.append_log(f"User input: length={length}")

    def _on_pretend_error_clicked(self):
        with self._pretend_lock:
            self._pretend_error_next = True
        self.append_log("User action: will pretend ERROR on next incoming DATA.")

    def consume_pretend_error_flag(self) -> bool:
        with self._pretend_lock:
            if self._pretend_error_next:
                self._pretend_error_next = False
                return True
        return False

    # ------------------------------------------------------------------ #
    #  API used by GameLogic
    # ------------------------------------------------------------------ #

    def get_next_send_request_blocking(self) -> dict:
        """Block until the user provides a send request."""
        return self._send_queue.get()

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
            req = self.ui.get_next_send_request_blocking()
            length = int(req.get("length", 1))
            invalid_rwnd = bool(req.get("invalid_rwnd", False))

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
            comment = ""
            if invalid_rwnd:
                rwnd = MAX_RWND + 1
                comment = "INTENTIONAL_INVALID: rwnd > MAX_RWND"
            self.ui.append_log(
                f"Preparing DATA packet: seq={seq}, len={real_length}, ack={ack}, rwnd={rwnd}"
            )
            return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length, comment=comment)

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

    def _respond_to_incoming(self, pkt: Packet):
        # Optional rule: receiver may pretend to have received incorrectly any packet.
        if pkt.type == "DATA" and self.ui.consume_pretend_error_flag():
            self.ui.append_log("Pretending invalid reception → sending ERROR.")
            err = Packet.error(comment="Pretend invalid (user choice)")
            self._send_packet(err)
            return

        return super()._respond_to_incoming(pkt)


# ===================================================================== #
#  PUBLIC ENTRY POINT
# ===================================================================== #

def run_gui_client(role: str, starts_first: bool, conn: Connection | None = None):
    """
    Entry point used by client_A.py and client_B.py.

    Each process opens its own window representing one TCP endpoint.
    """
    root = tk.Tk()

    # ------------------------------------------------------------------ #
    # Connect screen (host/join) – closer to the lecture-style demo UI
    # ------------------------------------------------------------------ #

    @dataclass
    class _ConnectResult:
        conn: Connection
        peer_label: str

    class ConnectScreen(ttk.Frame):
        def __init__(self, master: tk.Misc, on_connected):
            super().__init__(master, padding=20)
            self.on_connected = on_connected

            self.mode_var = tk.StringVar(value="host")
            self.ip_var = tk.StringVar(value=HOST)
            self.port_var = tk.StringVar(value=str(PORT))
            self.status_var = tk.StringVar(value="Not connected")

            title = ttk.Label(self, text="TCP PROJECT GAME", font=("Helvetica", 18, "bold"))
            title.pack(pady=(0, 18))

            box = ttk.Frame(self, padding=16, borderwidth=2, relief="groove")
            box.pack()

            modes = ttk.Frame(box)
            modes.grid(row=0, column=0, columnspan=4, pady=(0, 10))
            ttk.Radiobutton(modes, text="HOST GAME", variable=self.mode_var, value="host").pack(side="left", padx=8)
            ttk.Radiobutton(modes, text="JOIN GAME", variable=self.mode_var, value="join").pack(side="left", padx=8)

            ttk.Label(box, text="IP:").grid(row=1, column=0, sticky="e", padx=(0, 6))
            ttk.Entry(box, textvariable=self.ip_var, width=18).grid(row=1, column=1, padx=(0, 12))
            ttk.Label(box, text="Port:").grid(row=1, column=2, sticky="e", padx=(0, 6))
            ttk.Entry(box, textvariable=self.port_var, width=8).grid(row=1, column=3)

            btn = ttk.Button(box, text="CONNECT", command=self._connect_clicked)
            btn.grid(row=2, column=0, columnspan=4, pady=(12, 0))

            ttk.Label(self, textvariable=self.status_var).pack(pady=(12, 0))

            self.pack(fill="both", expand=True)

        def _connect_clicked(self):
            mode = self.mode_var.get()
            ip = self.ip_var.get().strip()
            try:
                port = int(self.port_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid port", "Port must be a number.")
                return

            self.status_var.set("Connecting...")

            def worker():
                try:
                    if mode == "host":
                        c = Connection.create_as_server(host=ip, port=port)
                        peer_label = "PEER (Other)"
                    else:
                        c = Connection.create_as_client(host=ip, port=port)
                        peer_label = "HOST (Other)"
                    root.after(0, lambda: self.on_connected(_ConnectResult(conn=c, peer_label=peer_label)))
                except Exception as e:
                    root.after(0, lambda: self.status_var.set(f"Connection failed: {e}"))

            threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Professional-ish lecture-style game UI
    # ------------------------------------------------------------------ #

    class ProClientGUI(ttk.Frame):
        def __init__(self, master: tk.Misc, role_name: str):
            super().__init__(master, padding=10)
            self.root = master
            self.role = role_name
            self.peer_name = ROLE_B_NAME if role_name == ROLE_A_NAME else ROLE_A_NAME

            self._send_queue: "queue.Queue[dict]" = queue.Queue()
            self._pretend_error_next = False
            self._pretend_lock = threading.Lock()

            self.connected_var = tk.StringVar(value="DISCONNECTED")
            self.score_var = tk.StringVar(value="SCORE: 0–0")
            self.turn_var = tk.StringVar(value="WAITING (RECV)")

            self.seq_var = tk.StringVar(value="0")
            self.ack_var = tk.StringVar(value="0")
            self.rwnd_var = tk.StringVar(value=str(MAX_RWND))
            self.len_var = tk.StringVar(value="")

            self.invalid_rwnd_var = tk.BooleanVar(value=False)
            self.manual_override_var = tk.BooleanVar(value=False)

            self._diagram_events: list[tuple[str, str]] = []
            self._incoming_decision_queue: "queue.Queue[bool]" = queue.Queue()
            self._incoming_active = False
            self._incoming_packet_label = tk.StringVar(value="")
            self._build_ui()

        def _build_ui(self):
            self.root.title(f"TCP Game – {self.role}")
            self.root.geometry("1100x700")

            top = ttk.Frame(self)
            top.pack(fill="x")
            ttk.Label(top, text="TCP GAME", font=("Helvetica", 14, "bold")).pack(side="left")
            ttk.Label(top, textvariable=self.connected_var).pack(side="left", padx=14)
            ttk.Label(top, textvariable=self.score_var).pack(side="right")

            sep = ttk.Separator(self)
            sep.pack(fill="x", pady=8)

            # My turn panel
            send_frame = ttk.LabelFrame(self, text="MY TURN (SEND)", padding=10)
            send_frame.pack(fill="x", pady=(0, 8))
            self._send_frame = send_frame

            row = ttk.Frame(send_frame)
            row.pack(fill="x")

            ttk.Label(row, text="Seq:").pack(side="left")
            self.seq_entry = ttk.Entry(row, textvariable=self.seq_var, width=10, justify="center")
            self.seq_entry.pack(side="left", padx=(4, 12))
            ttk.Label(row, text="Ack:").pack(side="left")
            self.ack_entry = ttk.Entry(row, textvariable=self.ack_var, width=10, justify="center")
            self.ack_entry.pack(side="left", padx=(4, 12))
            ttk.Label(row, text="Rwnd:").pack(side="left")
            self.rwnd_entry = ttk.Entry(row, textvariable=self.rwnd_var, width=10, justify="center")
            self.rwnd_entry.pack(side="left", padx=(4, 12))
            ttk.Label(row, text="Len:").pack(side="left")
            self.len_entry = ttk.Entry(row, textvariable=self.len_var, width=10, justify="center")
            self.len_entry.pack(side="left", padx=(4, 12))

            self.send_btn = ttk.Button(row, text="SEND PACKET", command=self._on_send)
            self.send_btn.pack(side="right")

            opts = ttk.Frame(send_frame)
            opts.pack(fill="x", pady=(8, 0))
            ttk.Label(opts, textvariable=self.turn_var).pack(side="left")
            ttk.Checkbutton(
                opts,
                text="Manual override seq/ack/rwnd",
                variable=self.manual_override_var,
                command=self._refresh_send_controls,
            ).pack(side="left", padx=12)
            ttk.Checkbutton(
                opts,
                text="Send invalid rwnd once",
                variable=self.invalid_rwnd_var,
            ).pack(side="left", padx=12)
            ttk.Button(
                opts,
                text="Pretend ERROR (next incoming DATA)",
                command=self._on_pretend_error_clicked,
            ).pack(side="left")

            # Middle split: state + diagram
            mid = ttk.Frame(self)
            mid.pack(fill="both", expand=True)

            left = ttk.LabelFrame(mid, text="GAME STATE", padding=8)
            left.pack(side="left", fill="both", expand=True, padx=(0, 8))
            self.state_text = tk.Text(left, height=10, wrap="word")
            self.state_text.pack(fill="both", expand=True)
            self._set_state_text("Waiting for packets…")

            right = ttk.LabelFrame(mid, text="Visual Diagram (Matplotlib)", padding=8)
            right.pack(side="left", fill="both", expand=True)

            fig = Figure(figsize=(6, 4), dpi=100)
            self.ax = fig.add_subplot(111)
            self.ax.set_title("Packet Flow")
            self.ax.set_axis_off()
            self.fig = fig

            self.canvas = FigureCanvasTkAgg(fig, master=right)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self._redraw_diagram()

            # Incoming decision panel
            incoming = ttk.LabelFrame(self, text="INCOMING PACKET (DECIDE)", padding=8)
            incoming.pack(fill="x", pady=(8, 0))
            self._incoming_frame = incoming

            inc_row = ttk.Frame(incoming)
            inc_row.pack(fill="x")
            ttk.Label(inc_row, textvariable=self._incoming_packet_label).pack(side="left")
            self.accept_btn = ttk.Button(inc_row, text="ACCEPT", command=lambda: self._resolve_incoming(True))
            self.accept_btn.pack(side="right", padx=(8, 0))
            self.reject_btn = ttk.Button(inc_row, text="REJECT (ERROR)", command=lambda: self._resolve_incoming(False))
            self.reject_btn.pack(side="right")
            self._set_incoming_active(False, "")

            # Bottom: logs
            bottom = ttk.LabelFrame(self, text="LOG", padding=8)
            bottom.pack(fill="both", expand=False, pady=(8, 0))
            self.log = tk.Text(bottom, height=10, wrap="word")
            self.log.pack(fill="both", expand=True)

            self.pack(fill="both", expand=True)
            self.set_my_turn(False)
            self._refresh_send_controls()

        def _set_state_text(self, text: str):
            self.state_text.configure(state="normal")
            self.state_text.delete("1.0", "end")
            self.state_text.insert("end", text)
            self.state_text.configure(state="disabled")

        def append_log(self, text: str):
            self.log.insert("end", text + "\n")
            self.log.see("end")

        def set_connected(self, ok: bool):
            self.connected_var.set("CONNECTED" if ok else "DISCONNECTED")

        def set_score(self, me: int, opp: int):
            self.score_var.set(f"SCORE: {me}–{opp}")

        def set_my_turn(self, my_turn: bool):
            if my_turn:
                self.turn_var.set("MY TURN (SEND)")
            else:
                self.turn_var.set("WAITING (RECV)")
            self._my_turn = my_turn
            self._refresh_send_controls()

        def _refresh_send_controls(self):
            my_turn = getattr(self, "_my_turn", False)
            if not my_turn:
                for w in (self.seq_entry, self.ack_entry, self.rwnd_entry, self.len_entry, self.send_btn):
                    w.configure(state="disabled")
                return

            # During my turn, length + send are enabled; other fields are opt-in.
            self.len_entry.configure(state="normal")
            self.send_btn.configure(state="normal")
            if self.manual_override_var.get():
                self.seq_entry.configure(state="normal")
                self.ack_entry.configure(state="normal")
                self.rwnd_entry.configure(state="normal")
            else:
                self.seq_entry.configure(state="disabled")
                self.ack_entry.configure(state="disabled")
                self.rwnd_entry.configure(state="disabled")

        def set_send_defaults(self, seq: int, ack: int, rwnd: int):
            self.seq_var.set(str(seq))
            self.ack_var.set(str(ack))
            self.rwnd_var.set(str(rwnd))
            self.len_var.set("")

        def enqueue_diagram_event(self, label: str, direction: str):
            # keep last 12 events
            self._diagram_events.append((label, direction))
            self._diagram_events = self._diagram_events[-12:]
            self._redraw_diagram()

        def _redraw_diagram(self):
            ax = self.ax
            ax.clear()
            ax.set_axis_off()

            # Endpoints
            x_left, x_right = 0.25, 0.75
            ax.plot([x_left, x_left], [0.1, 0.9], color="black")
            ax.plot([x_right, x_right], [0.1, 0.9], color="black")
            ax.text(x_left, 0.95, "HOST (Me)", ha="center", va="bottom", fontsize=9)
            ax.text(x_right, 0.95, "PEER (Other)", ha="center", va="bottom", fontsize=9)

            n = len(self._diagram_events)
            for i, (label, direction) in enumerate(self._diagram_events):
                y = 0.85 - i * (0.75 / max(1, n))
                if direction == "out":
                    ax.annotate(
                        "",
                        xy=(x_right, y),
                        xytext=(x_left, y),
                        arrowprops=dict(arrowstyle="->", color="blue", linewidth=1.5),
                    )
                    ax.text(0.5, y + 0.02, label, ha="center", va="bottom", fontsize=7, color="blue")
                else:
                    ax.annotate(
                        "",
                        xy=(x_left, y),
                        xytext=(x_right, y),
                        arrowprops=dict(arrowstyle="->", color="red", linewidth=1.5),
                    )
                    ax.text(0.5, y + 0.02, label, ha="center", va="bottom", fontsize=7, color="red")

            self.canvas.draw_idle()

        def _on_send(self):
            try:
                length = int(self.len_var.get().strip() or "0")
            except ValueError:
                messagebox.showerror("Invalid Len", "Len must be an integer.")
                return

            req = {
                "length": length,
                "invalid_rwnd": bool(self.invalid_rwnd_var.get()),
                "seq": int(self.seq_var.get() or "0"),
                "ack": int(self.ack_var.get() or "0"),
                "rwnd": int(self.rwnd_var.get() or str(MAX_RWND)),
            }
            self.invalid_rwnd_var.set(False)
            self._send_queue.put(req)
            self.append_log(f"User SEND request: {req}")

        def get_next_send_request_blocking(self, timeout: float | None = None) -> dict:
            return self._send_queue.get(timeout=timeout)

        def _on_pretend_error_clicked(self):
            with self._pretend_lock:
                self._pretend_error_next = True
            self.append_log("User action: pretend ERROR on next incoming DATA.")

        def consume_pretend_error_flag(self) -> bool:
            with self._pretend_lock:
                if self._pretend_error_next:
                    self._pretend_error_next = False
                    return True
            return False

        def update_state(self, text: str):
            self._set_state_text(text)

        def _set_incoming_active(self, active: bool, label: str):
            self._incoming_active = active
            self._incoming_packet_label.set(label)
            state = "normal" if active else "disabled"
            self.accept_btn.configure(state=state)
            self.reject_btn.configure(state=state)

        def _resolve_incoming(self, accepted: bool):
            if not self._incoming_active:
                return
            self._incoming_decision_queue.put(accepted)
            self._set_incoming_active(False, "")

        def prompt_accept_reject(self, pkt: Packet, timeout_seconds: float) -> bool | None:
            """
            Game thread blocks here until user accepts/rejects incoming DATA.
            Returns: True (accept), False (reject), None (timeout)
            """
            label = f"{pkt.type} s={pkt.seq} a={pkt.ack} w={pkt.rwnd} len={pkt.length}"
            self.root.after(0, lambda: self._set_incoming_active(True, label))
            try:
                return self._incoming_decision_queue.get(timeout=timeout_seconds)
            except queue.Empty:
                self.root.after(0, lambda: self._set_incoming_active(False, ""))
                return None

    class ProGameLogic(GameLogic):
        def __init__(self, role_name, conn, starts_first, ui: ProClientGUI):
            super().__init__(role_name, conn, starts_first)
            self.ui = ui

        def _create_next_data_packet(self) -> Packet:
            # Enable UI and wait for user, but enforce quick-response rule via timeout.
            default_seq = self.gbn.state.next_seq
            default_ack = self.validator.peer.expected_seq
            self.ui.root.after(0, lambda: self.ui.set_send_defaults(default_seq, default_ack, self.current_rwnd))
            self.ui.root.after(0, lambda: self.ui.set_my_turn(True))

            try:
                req = self.ui.get_next_send_request_blocking(timeout=RESPONSE_TIMEOUT_SECONDS)
            except queue.Empty:
                # No response in time → lose 1 point and send ACK-only to keep game moving.
                self.scoreboard.my_penalty(1)
                ack = self.validator.peer.expected_seq
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=self.current_rwnd,
                    comment="No user response (timeout) → ACK-only",
                )
                self.ui.root.after(0, lambda: self.ui.set_my_turn(False))
                return pkt

            self.ui.root.after(0, lambda: self.ui.set_my_turn(False))

            length = int(req.get("length", 0))
            invalid_rwnd = bool(req.get("invalid_rwnd", False))
            forced_seq = int(req.get("seq", default_seq))
            forced_ack = int(req.get("ack", default_ack))
            forced_rwnd = int(req.get("rwnd", self.current_rwnd))

            # 0 length -> ACK-only
            if length == 0:
                return Packet.make_ack(
                    seq=forced_seq,
                    ack=forced_ack,
                    rwnd=forced_rwnd,
                    comment="User ACK-only",
                )

            # Default: compute seq from GBN unless user overrides (to allow cheating/invalid packets)
            try:
                seq, real_length = self.gbn.next_data_segment(length=length)
            except RuntimeError:
                # Window full -> ACK-only
                return Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=default_ack,
                    rwnd=self.current_rwnd,
                    comment="Window full, ACK-only",
                )

            seq_to_send = forced_seq if forced_seq != seq else seq
            ack_to_send = forced_ack
            rwnd_to_send = (MAX_RWND + 1) if invalid_rwnd else forced_rwnd
            comment = "DATA"
            if invalid_rwnd:
                comment = "INTENTIONAL_INVALID: rwnd > MAX_RWND"

            return Packet.data(
                seq=seq_to_send,
                ack=ack_to_send,
                rwnd=rwnd_to_send,
                length=real_length,
                comment=comment,
            )

        def _send_packet(self, pkt: Packet):
            self.ui.root.after(0, lambda: self.ui.append_log(
                f"SEND → {pkt.type} | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
            ))
            label = f"{pkt.type} s={pkt.seq} a={pkt.ack} w={pkt.rwnd} len={pkt.length}"
            self.ui.root.after(0, lambda: self.ui.enqueue_diagram_event(label, "out"))
            super()._send_packet(pkt)
            self._update_ui_state()

        def _receive_packet(self) -> Packet:
            pkt = super()._receive_packet()
            self.ui.root.after(0, lambda: self.ui.append_log(
                f"RECV ← {pkt.type} | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
            ))
            label = f"{pkt.type} s={pkt.seq} a={pkt.ack} w={pkt.rwnd} len={pkt.length}"
            self.ui.root.after(0, lambda: self.ui.enqueue_diagram_event(label, "in"))
            self._update_ui_state()
            return pkt

        def _respond_to_incoming(self, pkt: Packet) -> bool:
            # For DATA/ACK: show incoming packet info and let user ACCEPT or REJECT.
            decision = None
            if pkt.type in {"DATA", "ACK"}:
                pretend = (pkt.type == "DATA" and self.ui.consume_pretend_error_flag())
                decision = False if pretend else self.ui.prompt_accept_reject(
                    pkt, timeout_seconds=RESPONSE_TIMEOUT_SECONDS
                )
                if decision is None:
                    # No user response in time -> penalty, then accept to keep game moving.
                    self.scoreboard.my_penalty(1)
                    decision = True

            return super()._respond_to_incoming(pkt, decision=decision)

        def _update_ui_state(self):
            text = (
                f"Seq(next): {self.gbn.state.next_seq} | Base: {self.gbn.state.base}\n"
                f"Peer expected seq: {self.validator.peer.expected_seq}\n"
                f"My rwnd: {self.current_rwnd} | Peer rwnd(last): {self.validator.peer.last_rwnd}\n"
                f"{self.scoreboard.snapshot()}"
            )
            self.ui.root.after(0, lambda: self.ui.update_state(text))
            self.ui.root.after(0, lambda: self.ui.set_score(self.scoreboard.my_score, self.scoreboard.opponent_score))

    # Start flow: show connect screen then game screen
    def start_game(connect_result: _ConnectResult):
        for child in list(root.winfo_children()):
            child.destroy()

        ui = ProClientGUI(root, role_name=role)
        ui.set_connected(True)
        game = ProGameLogic(role, connect_result.conn, starts_first, ui)

        t = threading.Thread(target=game.run, daemon=True)
        t.start()

    # If conn is provided (legacy path), start directly.
    if conn is not None:
        start_game(_ConnectResult(conn=conn, peer_label="PEER (Other)"))
    else:
        ConnectScreen(root, on_connected=start_game)

    root.mainloop()
