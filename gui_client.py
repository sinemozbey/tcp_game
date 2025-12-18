# gui_client.py

import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox

from core.game_logic import GameLogic
from core.packet import Packet
from core.connection import TimeoutError
from utils.config import (
    MIN_SEGMENT_SIZE,
    MAX_SEGMENT_SIZE,
    ROLE_A_NAME,
    ROLE_B_NAME,
    MAX_RWND,
    BUFFER_DRAIN_INTERVAL_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
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
        self.root.geometry("900x850")

        header = ttk.Label(self, text=f"TCP Game — {self.role}", font=("Helvetica", 16, "bold"))
        header.pack(pady=(0, 4))
        
        # --- SCOREBOARD ---
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
        
        # --- RWND DURUM PANELİ ---
        status_frame = ttk.Frame(self, relief="groove", borderwidth=2, padding=5)
        status_frame.pack(fill="x", pady=(0, 8))
        
        self.rwnd_label = ttk.Label(
            status_frame, 
            text="My Receiver Window (rwnd): 50", 
            font=("Courier", 12, "bold"), 
            foreground="#8E44AD"
        )
        self.rwnd_label.pack()
        # -------------------------
        
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
    
    # Timeout destekli kuyruk okuma
    def get_input_with_timeout(self, timeout) -> dict:
        return self._input_queue.get(timeout=timeout)

    def set_mode(self, mode: str, message: str = ""):
        self.current_mode = mode
        
        if mode == "SENDING":
            self.mode_label.config(text="🚀 YOUR TURN: Send a packet", foreground="#27AE60") 
            self.info_label.config(text="Fill all 4 fields and click SEND")
            self.error_btn.config(state="disabled")
            self.send_btn.config(state="normal")
            
        elif mode == "RESPONDING":
            msg = message if message else "Packet received!"
            self.mode_label.config(text=f"🚀 YOUR TURN: {msg}", foreground="#27AE60") 
            self.info_label.config(text="Click SEND to respond or ERROR to reject")
            self.error_btn.config(state="normal")
            self.send_btn.config(state="normal")

        elif mode == "WAITING":
            self.mode_label.config(text="⏳ WAITING for peer...", foreground="#E67E22")
            self.info_label.config(text="Please wait for the opponent's move")
            self.error_btn.config(state="disabled")
            self.send_btn.config(state="disabled")

    def show_incoming_packet(self, pkt: Packet):
        def _update_ui():
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
        
        self.root.after(0, _update_ui)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
    
    def update_scores(self, my_score: int, opponent_score: int):
        def _update():
            self.my_score_label.config(text=str(my_score))
            self.opponent_score_label.config(text=str(opponent_score))
            
            if my_score > opponent_score:
                self.my_score_label.config(foreground="#27AE60")
                self.opponent_score_label.config(foreground="#E74C3C")
            elif opponent_score > my_score:
                self.my_score_label.config(foreground="#E74C3C")
                self.opponent_score_label.config(foreground="#27AE60")
            else:
                self.my_score_label.config(foreground="#3498DB")
                self.opponent_score_label.config(foreground="#3498DB")
        
        self.root.after(0, _update)
    
    def animate_send(self, pkt): 
        self.traffic.enqueue_packet("outgoing", pkt.type, pkt.seq, pkt.ack, pkt.length)
    
    def animate_recv(self, pkt): 
        self.traffic.enqueue_packet("incoming", pkt.type, pkt.seq, pkt.ack, pkt.length)
    
    # --- RWND CANLI TAKİP METODU ---
    def start_rwnd_monitor(self, game_logic):
        current_val = game_logic.current_rwnd
        
        self.rwnd_label.config(text=f"My Receiver Window (rwnd): {current_val}")
        
        if current_val <= 0:
            self.rwnd_label.config(foreground="red", text=f"⚠️ RWND CLOSED ({current_val})")
        else:
            self.rwnd_label.config(foreground="#8E44AD")

        self.root.after(100, self.start_rwnd_monitor, game_logic)


# ===================================================================== #
#  GAME LOGIC + GUI INTEGRATION
# ===================================================================== #

class GameLogicGUI(GameLogic):
    def __init__(self, role_name, conn, starts_first, ui: SingleClientGUI):
        super().__init__(role_name, conn, starts_first)
        self.ui = ui
        self.ui.update_scores(0, 0)
        
        if starts_first:
            self.ui.set_mode("SENDING")
        else:
            self.ui.set_mode("WAITING", "Waiting for peer...")

    def _check_and_update_buffer(self):
        now = time.time()
        if now - self.last_window_update >= BUFFER_DRAIN_INTERVAL_SECONDS:
            if self.recv_buffer_used > 0:
                drain_amount = 20
                if self.recv_buffer_used <= drain_amount: 
                    self.recv_buffer_used = 0
                else: 
                    self.recv_buffer_used -= drain_amount
                
                self.current_rwnd = MAX_RWND - self.recv_buffer_used
                self.logger.info(f"Buffer Drain Executed: used={self.recv_buffer_used}, rwnd={self.current_rwnd}")
            
            self.last_window_update = now

    def _poll_input_queue(self):
        while True:
            self._check_and_update_buffer()
            try:
                return self.ui.get_input_with_timeout(timeout=0.1)
            except queue.Empty:
                continue
    
    def _get_user_decision_for_incoming(self) -> dict:
        return self._poll_input_queue()

    def _create_next_data_packet(self) -> Packet:
        self.ui.set_mode("SENDING")
        user_input = self._poll_input_queue()
        
        if user_input["action"] == "ERROR":
            self.logger.warning("User pressed ERROR during send phase")
            auto_ack = self.validator.peer.last_seq + self.validator.peer.last_len
            return Packet.make_ack(
                seq=self.gbn.state.next_seq,
                ack=auto_ack,
                rwnd=self.current_rwnd,
                comment="ERROR pressed during send",
            )
        
        return self._create_response_packet_from_input(user_input)

    def _send_packet(self, pkt: Packet):
        self.ui.append_log(f"[SEND] {pkt.type} | s={pkt.seq}, a={pkt.ack}, w={pkt.rwnd}, len={pkt.length}")
        self.ui.animate_send(pkt)
        super()._send_packet(pkt)
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        self.ui.set_mode("WAITING")

    def _receive_packet(self) -> Packet:
        start_wait = time.time()
        raw = None
        
        while True:
            self._check_and_update_buffer()
            
            elapsed = time.time() - start_wait
            remaining = RESPONSE_TIMEOUT_SECONDS - elapsed
            if remaining <= 0:
                raise TimeoutError("No response within timeout")
            
            step_timeout = min(0.5, remaining)
            try:
                raw = self.conn.recv_json(timeout=step_timeout)
                break 
            except TimeoutError:
                continue
            except Exception as e:
                raise e
        
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
        
        self.ui.append_log(f"[RECV] {pkt.type} | s={pkt.seq}, a={pkt.ack}, w={pkt.rwnd}, len={pkt.length}")
        self.ui.animate_recv(pkt)
        self.ui.show_incoming_packet(pkt)
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return pkt
    
    def _create_response_packet_from_input(self, user_input: dict):
        result = super()._create_response_packet_from_input(user_input)
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return result
    
    # --- [ÖNEMLİ] FAST RETRANSMIT (MANUAL MODE) ---
    def _respond_to_incoming(self, pkt: Packet) -> bool:
        if pkt.type == "DATA" and self.current_rwnd == 0:
            self.logger.warning("Peer sent DATA while rwnd=0 → AUTOMATIC ERROR")
            self.scoreboard.detected_error()
            err = Packet.error(comment="CORRECT: DATA sent while advertised rwnd=0")
            self._send_packet(err)
            return False

        if self.start_time is None: elapsed = 0.0
        else: elapsed = time.time() - self.start_time

        is_valid, reason = self.validator.validate(
            pkt, 
            last_sent_seq=self.last_sent_seq,
            elapsed_time=elapsed
        )
        self.logger.info(f"Validation Check: valid={is_valid}, reason={reason}")

        # 1. GEÇİCİ BUFFER GÜNCELLEMESİ (Tentative)
        tentative_data_len = 0
        if pkt.type == "DATA" and is_valid:
            expected_now = self.validator.peer.expected_seq
            if (pkt.seq or 0) + (pkt.length or 0) == expected_now:
                tentative_data_len = (pkt.length or 0)
                self.recv_buffer_used += tentative_data_len
                if self.recv_buffer_used > MAX_RWND: self.recv_buffer_used = MAX_RWND
                self.current_rwnd = MAX_RWND - self.recv_buffer_used
                self.logger.info(f"Tentative Buffer Update: rwnd -> {self.current_rwnd}")

        # 2. FAST RETRANSMIT TESPİTİ (Ama oto doldurma yok!)
        processed_ack_early = False
        saved_gbn_base = self.gbn.state.base
        saved_gbn_dup = self.gbn.state.duplicate_ack_count
        saved_gbn_next = self.gbn.state.next_seq
        
        if is_valid and pkt.type == "ACK" and pkt.ack is not None:
            win_adv, retx_req = self.gbn.on_ack(pkt.ack)
            processed_ack_early = True
            
            if retx_req:
                self._pending_retransmit = True
                # UYARI: Kırmızı yazı çıkar ama kutuları ellemez.
                self.ui.mode_label.config(text="⚠️ FAST RETRANSMIT TRIGGERED!", foreground="red")
                self.ui.info_label.config(text=f"Packet Loss Detected! Enter Seq={self.gbn.state.base} manually to resend.")
                # self.ui.seq_var.set(...) # <- BU SATIR SİLİNDİ (OTO DOLDURMA YOK)

        # 3. KULLANICI GİRİŞİNİ BEKLE
        user_decision = self._poll_input_queue()
        outgoing_comment_flag = ""

        if user_decision["action"] == "ERROR":
             # Kullanıcı ERROR dedi. Yaptığımız geçici değişiklikleri geri almalıyız.
             if tentative_data_len > 0:
                 self.recv_buffer_used -= tentative_data_len
                 if self.recv_buffer_used < 0: self.recv_buffer_used = 0
                 self.current_rwnd = MAX_RWND - self.recv_buffer_used
                 self.logger.info(f"User rejected valid packet -> Reverted rwnd to {self.current_rwnd}")
             
             # GBN Revert
             if processed_ack_early:
                 self.gbn.state.base = saved_gbn_base
                 self.gbn.state.duplicate_ack_count = saved_gbn_dup
                 self.gbn.state.next_seq = saved_gbn_next
                 self._pending_retransmit = False 

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
            if not is_valid:
                self.scoreboard.opponent_score += 1 
                outgoing_comment_flag = "MISSED_ERROR"
            
            # Eğer yukarıda on_ack çalıştırmadıysak şimdi çalıştır
            if not processed_ack_early:
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
            self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
            return False

def run_gui_client(role: str, conn, starts_first: bool):
    root = tk.Tk()
    ui = SingleClientGUI(root, role=role)
    game = GameLogicGUI(role, conn, starts_first, ui)
    
    ui.start_rwnd_monitor(game)
    
    t = threading.Thread(target=game.run, daemon=True)
    t.start()
    try:
        root.mainloop()
    finally:
        try: 
            conn.close()
        except: 
            pass