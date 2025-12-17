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

        self.current_ball = None
        self.current_ball_text = None
        self.anim_queue: "queue.Queue[dict]" = queue.Queue()
        self.anim_running = False

        self._build_static()
        self.after(40, self._animation_step)

    def _build_static(self):
        x_local = 80
        x_peer = 420
        y_center = 80
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
        self.line_id = self.canvas.create_line(
            x_local + 40, y_center, x_peer - 40, y_center,
            dash=(4, 2), fill="#999999"
        )

    def enqueue_packet(self, direction, kind, seq, ack, length):
        self.anim_queue.put({"direction": direction, "kind": kind, "seq": seq, "ack": ack, "length": length})

    def _start_next_ball(self, item):
        if self.current_ball: self.canvas.delete(self.current_ball)
        if self.current_ball_text: self.canvas.delete(self.current_ball_text)
        x1, y1, x2, y2 = self.canvas.coords(self.line_id)
        direction = item["direction"]
        x = x1 if direction == "outgoing" else x2
        dx = +6 if direction == "outgoing" else -6
        y = y1
        r = 7
        color = {"DATA": "#3498db", "ACK": "#2ecc71", "ERROR": "#e74c3c", "RETX": "#f39c12"}.get(item["kind"], "#34495e")
        self.current_ball = self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="")
        label = item["kind"]
        if item["seq"] is not None: label += f" s={item['seq']}"
        self.current_ball_text = self.canvas.create_text(x, y-15, text=label, font=("Helvetica", 8))
        self.anim_running = True
        self.canvas.itemconfig(self.current_ball, tags=("ball", str(dx)))
        self.canvas.itemconfig(self.current_ball_text, tags=("ball_text", str(dx)))

    def _animation_step(self):
        if self.anim_running and self.current_ball:
            x1, _, x2, _ = self.canvas.coords(self.current_ball)
            lx1, _, lx2, _ = self.canvas.coords(self.line_id)
            dx = int(self.canvas.gettags(self.current_ball)[1])
            if (dx > 0 and x2 < lx2) or (dx < 0 and x1 > lx1):
                self.canvas.move(self.current_ball, dx, 0)
                self.canvas.move(self.current_ball_text, dx, 0)
            else:
                self.canvas.delete(self.current_ball); self.canvas.delete(self.current_ball_text)
                self.current_ball = None; self.current_ball_text = None; self.anim_running = False
        if not self.anim_running and not self.anim_queue.empty():
            self._start_next_ball(self.anim_queue.get_nowait())
        self.after(40, self._animation_step)


# ===================================================================== #
#  SINGLE CLIENT WINDOW (MANUAL INPUT ONLY)
# ===================================================================== #

class SingleClientGUI(ttk.Frame):
    def __init__(self, root: tk.Tk, role: str):
        super().__init__(root, padding=10)
        self.root = root
        self.role = role
        self.peer_name = ROLE_B_NAME if role == ROLE_A_NAME else ROLE_A_NAME
        
        self._input_queue: "queue.Queue[dict]" = queue.Queue()
        self.incoming_packet = None
        self.current_mode = "SENDING"
        
        self._build_ui()

    def _build_ui(self):
        self.root.title(f"TCP Game — {self.role} (MANUAL MODE)")
        self.root.geometry("900x800")

        header = ttk.Label(self, text=f"TCP Game — {self.role}", font=("Helvetica", 16, "bold"))
        header.pack(pady=(0, 4))
        
        # SCOREBOARD
        score_frame = ttk.Frame(self, padding=5)
        score_frame.pack(fill="x", pady=(0, 8))
        
        # Sol taraf - Kendi skorumuz
        left_score = ttk.Frame(score_frame, relief="solid", borderwidth=2, padding=10)
        left_score.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        ttk.Label(left_score, text=f"{self.role}", font=("Helvetica", 11, "bold"), foreground="#2E86C1").pack()
        self.my_score_label = ttk.Label(left_score, text="0", font=("Helvetica", 28, "bold"), foreground="#27AE60")
        self.my_score_label.pack()
        ttk.Label(left_score, text="points", font=("Helvetica", 9), foreground="gray").pack()
        
        # Orta - VS
        ttk.Label(score_frame, text="VS", font=("Helvetica", 12, "bold"), foreground="#E74C3C").pack(side="left", padx=10)
        
        # Sağ taraf - Rakip skoru
        right_score = ttk.Frame(score_frame, relief="solid", borderwidth=2, padding=10)
        right_score.pack(side="left", fill="both", expand=True, padx=(5, 0))
        
        ttk.Label(right_score, text=f"{self.peer_name}", font=("Helvetica", 11, "bold"), foreground="#E67E22").pack()
        self.opponent_score_label = ttk.Label(right_score, text="0", font=("Helvetica", 28, "bold"), foreground="#E74C3C")
        self.opponent_score_label.pack()
        ttk.Label(right_score, text="points", font=("Helvetica", 9), foreground="gray").pack()
        
        self.traffic = TrafficCanvas(self, local_name=self.role, peer_name=self.peer_name)
        self.traffic.pack(fill="x", pady=(0, 8))

        # GELEN PAKET
        incoming_frame = ttk.LabelFrame(self, text="📥 Incoming Packet", padding=10)
        incoming_frame.pack(fill="x", pady=(4, 8))
        
        self.incoming_label = ttk.Label(
            incoming_frame, 
            text="Waiting for packet...", 
            font=("Courier", 11), 
            foreground="blue"
        )
        self.incoming_label.pack()

        # MANUEL GİRİŞ
        control_frame = ttk.LabelFrame(self, text="📤 Your Response", padding=10)
        control_frame.pack(fill="x", pady=(4, 8))
        
        self.mode_label = ttk.Label(
            control_frame,
            text="",
            font=("Helvetica", 10, "bold"),
            foreground="green"
        )
        self.mode_label.grid(row=0, column=0, columnspan=3, pady=(0, 10))

        ttk.Label(control_frame, text="Sequence Number (seq):").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.seq_var = tk.StringVar()
        ttk.Entry(control_frame, textvariable=self.seq_var, width=12).grid(row=1, column=1, sticky="w")

        ttk.Label(control_frame, text="Acknowledgment (ack):").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.ack_var = tk.StringVar()
        ttk.Entry(control_frame, textvariable=self.ack_var, width=12).grid(row=2, column=1, sticky="w")

        ttk.Label(control_frame, text="Window Size (rwnd):").grid(row=3, column=0, sticky="e", padx=5, pady=5)
        self.rwnd_var = tk.StringVar()
        ttk.Entry(control_frame, textvariable=self.rwnd_var, width=12).grid(row=3, column=1, sticky="w")

        ttk.Label(control_frame, text="Data Length (0-5):").grid(row=4, column=0, sticky="e", padx=5, pady=5)
        self.length_var = tk.StringVar()
        ttk.Entry(control_frame, textvariable=self.length_var, width=12).grid(row=4, column=1, sticky="w")

        btn_frame = ttk.Frame(control_frame)
        btn_frame.grid(row=1, column=2, rowspan=4, padx=30)
        
        self.send_btn = ttk.Button(btn_frame, text="✅ SEND", command=self._on_send_clicked, width=15)
        self.send_btn.pack(pady=5)
        
        self.error_btn = ttk.Button(btn_frame, text="❌ ERROR", command=self._on_error_clicked, width=15)
        self.error_btn.pack(pady=5)

        self.info_label = ttk.Label(
            control_frame, 
            text="",
            font=("Helvetica", 9, "italic"),
            foreground="gray"
        )
        self.info_label.grid(row=5, column=0, columnspan=3, pady=(10, 0))

        # LOG
        bottom = ttk.LabelFrame(self, text="📋 Event Logs", padding=8)
        bottom.pack(fill="both", expand=True)
        
        self.log = tk.Text(bottom, height=12, font=("Courier", 9))
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        
        self.pack(fill="both", expand=True)

    def _on_send_clicked(self):
        try:
            seq = int(self.seq_var.get().strip())
            ack = int(self.ack_var.get().strip())
            rwnd = int(self.rwnd_var.get().strip())
            length = int(self.length_var.get().strip())
        except ValueError:
            messagebox.showerror("Input Error", "All fields must be integers!")
            return

        if seq < 0 or ack < 0 or rwnd < 0 or length < 0:
            messagebox.showerror("Input Error", "Values cannot be negative!")
            return

        input_data = {
            "action": "SEND",
            "seq": seq,
            "ack": ack,
            "rwnd": rwnd,
            "length": length
        }
        
        self._input_queue.put(input_data)
        self.append_log(f"[USER] SEND → seq={seq}, ack={ack}, rwnd={rwnd}, len={length}")

    def _on_error_clicked(self):
        input_data = {
            "action": "ERROR",
            "seq": 0,
            "ack": 0,
            "rwnd": 0,
            "length": 0
        }
        self._input_queue.put(input_data)
        self.append_log("[USER] ERROR button pressed")

    def get_input_blocking(self) -> dict:
        return self._input_queue.get()

    def set_mode(self, mode: str, message: str = ""):
        self.current_mode = mode
        
        if mode == "SENDING":
            self.mode_label.config(text="🚀 YOUR TURN: Send a packet", foreground="green")
            self.info_label.config(text="Fill all 4 fields and click SEND")
            self.error_btn.config(state="disabled")
            self.send_btn.config(state="normal")
            
        elif mode == "RESPONDING":
            msg = message if message else "Received packet from peer"
            self.mode_label.config(text=f"⚠️  RESPOND: {msg}", foreground="orange")
            self.info_label.config(text="Click SEND to respond or ERROR to reject")
            self.error_btn.config(state="normal")
            self.send_btn.config(state="normal")

    def show_incoming_packet(self, pkt: Packet):
        if pkt.type == "ERROR":
            text = "❌ ERROR received from peer"
            color = "red"
        else:
            text = f"📥 Type={pkt.type} | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
            color = "darkblue"
        
        self.incoming_label.config(text=text, foreground=color)
        self.incoming_packet = pkt
        self.append_log(f"\n{'='*60}\n{text}\n{'='*60}")
        self.set_mode("RESPONDING", "Packet received!")

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
    
    def update_scores(self, my_score: int, opponent_score: int):
        """Skorları güncelle - thread-safe"""
        def _update():
            self.my_score_label.config(text=str(my_score))
            self.opponent_score_label.config(text=str(opponent_score))
            
            # Renk güncellemesi - kazanan yeşil
            if my_score > opponent_score:
                self.my_score_label.config(foreground="#27AE60")  # Yeşil
                self.opponent_score_label.config(foreground="#E74C3C")  # Kırmızı
            elif opponent_score > my_score:
                self.my_score_label.config(foreground="#E74C3C")  # Kırmızı
                self.opponent_score_label.config(foreground="#27AE60")  # Yeşil
            else:
                # Berabere - her ikisi de mavi
                self.my_score_label.config(foreground="#3498DB")  # Mavi
                self.opponent_score_label.config(foreground="#3498DB")  # Mavi
        
        # GUI update'ini main thread'de schedule et
        self.root.after(0, _update)
    
    def animate_send(self, pkt): 
        self.traffic.enqueue_packet("outgoing", pkt.type, pkt.seq, pkt.ack, pkt.length)
    
    def animate_recv(self, pkt): 
        self.traffic.enqueue_packet("incoming", pkt.type, pkt.seq, pkt.ack, pkt.length)


# ===================================================================== #
#  GAME LOGIC + GUI INTEGRATION
# ===================================================================== #

class GameLogicGUI(GameLogic):
    def __init__(self, role_name, conn, starts_first, ui: SingleClientGUI):
        super().__init__(role_name, conn, starts_first)
        self.ui = ui
        
        # Başlangıç skorlarını göster
        self.ui.update_scores(0, 0)
        
        if starts_first:
            self.ui.set_mode("SENDING")
        else:
            # ClientB başlangıçta beklemede, GUI'de butonları devre dışı bırak
            self.ui.set_mode("RESPONDING", "Waiting for peer...")
            self.ui.send_btn.config(state="disabled")
            self.ui.error_btn.config(state="disabled")
    
    def _get_user_decision_for_incoming(self) -> dict:
        return self.ui.get_input_blocking()

    def _create_next_data_packet(self) -> Packet:
        self.ui.set_mode("SENDING")
        user_input = self.ui.get_input_blocking()
        
        if user_input["action"] == "ERROR":
            self.logger.warning("User pressed ERROR during send phase")
            auto_ack = self.validator.peer.last_seq + self.validator.peer.last_len
            return Packet.make_ack(
                seq=self.gbn.state.next_seq,
                ack=auto_ack,
                rwnd=self.current_rwnd,
                comment="ERROR pressed during send",
            )
        
        # Normal paket oluştur (_create_response_packet_from_input ile aynı mantık)
        return self._create_response_packet_from_input(user_input)

    def _send_packet(self, pkt: Packet):
        self.ui.append_log(f"[SEND] {pkt.type} | s={pkt.seq}, a={pkt.ack}, w={pkt.rwnd}, len={pkt.length}")
        self.ui.animate_send(pkt)
        super()._send_packet(pkt)
        # Skorları güncelle
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)

    def _receive_packet(self) -> Packet:
        pkt = super()._receive_packet()
        self.ui.append_log(f"[RECV] {pkt.type} | s={pkt.seq}, a={pkt.ack}, w={pkt.rwnd}, len={pkt.length}")
        self.ui.animate_recv(pkt)
        self.ui.show_incoming_packet(pkt)
        # Skorları güncelle
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return pkt
    
    def _create_response_packet_from_input(self, user_input: dict):
        """Skor değişikliklerini yakalamak için override"""
        result = super()._create_response_packet_from_input(user_input)
        # Paket oluşturulduktan sonra skorları güncelle
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return result
    
    def _respond_to_incoming(self, pkt):
        """Skor değişikliklerini yakalamak için override"""
        result = super()._respond_to_incoming(pkt)
        # Yanıt verildikten sonra skorları güncelle
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return result


def run_gui_client(role: str, conn, starts_first: bool):
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
        except: 
            pass