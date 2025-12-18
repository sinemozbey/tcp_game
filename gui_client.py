# gui_client.py

import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, font

from core.game_logic import GameLogic
from core.packet import Packet
from core.connection import TimeoutError
from utils.config import (
    ROLE_A_NAME,
    ROLE_B_NAME,
    MAX_RWND,
    BUFFER_DRAIN_INTERVAL_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
)

# ===================================================================== #
#  MODERN STİL AYARLARI
# ===================================================================== #
COLOR_BG_MAIN = "#F0F2F5"       # Ana arka plan (Açık Gri)
COLOR_CARD_BG = "#FFFFFF"       # Kart arka planı (Beyaz)
COLOR_PRIMARY = "#2980B9"       # Ana renk (Mavi)
COLOR_SUCCESS = "#27AE60"       # Başarı (Yeşil)
COLOR_DANGER  = "#C0392B"       # Hata (Kırmızı)
COLOR_TEXT_MAIN = "#2C3E50"     # Ana metin rengi
FONT_MAIN = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEADER = ("Segoe UI", 14, "bold")
FONT_MONO = ("Consolas", 9)

# ===================================================================== #
#  TIMELINE PANEL (ANIMASYONLU OKLAR)
# ===================================================================== #

class TimelineCanvas(tk.Frame):
    def __init__(self, master, local_name: str, peer_name: str, **kwargs):
        # Frame arka planını beyaz yapalım
        super().__init__(master, bg=COLOR_CARD_BG, **kwargs)

        self.local_name = local_name
        self.peer_name = peer_name

        # --- Scrollbar ve Canvas ---
        self.scrollbar = ttk.Scrollbar(self, orient="vertical")
        self.scrollbar.pack(side="right", fill="y")

        self.canvas = tk.Canvas(
            self, 
            height=300, 
            bg=COLOR_CARD_BG, 
            bd=0, 
            highlightthickness=0, # Çerçeve çizgisini kaldır
            yscrollcommand=self.scrollbar.set
        )
        self.canvas.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        self.scrollbar.config(command=self.canvas.yview)

        # Çizim Parametreleri
        self.current_y = 50      
        self.y_step = 45         
        self.margin_x = 80       
        self.arrow_slant = 20    

        self.canvas.bind("<Configure>", self._on_resize)
        self.width = 1
        
        # Başlıkları ilk kez çiz
        self._draw_headers()

    def _on_resize(self, event):
        self.width = event.width
        self._draw_headers()

    def _draw_headers(self):
        """Başlıkları ve dikey çizgileri çizer."""
        self.canvas.delete("header")
        
        x_left = self.margin_x
        w = self.width if self.width > 100 else 400
        x_right = w - self.margin_x

        # İsimler (Daha modern kutular içinde)
        self._draw_badge(x_left, 20, f"{self.local_name} (Me)", "#ECF0F1", COLOR_TEXT_MAIN)
        self._draw_badge(x_right, 20, self.peer_name, "#ECF0F1", COLOR_TEXT_MAIN)

        # Dikey referans çizgileri (Timeline)
        line_height = max(self.current_y + 100, self.canvas.winfo_height())
        self.canvas.create_line(x_left, 45, x_left, line_height, fill="#BDC3C7", dash=(2, 2), width=1, tags="header")
        self.canvas.create_line(x_right, 45, x_right, line_height, fill="#BDC3C7", dash=(2, 2), width=1, tags="header")

    def _draw_badge(self, x, y, text, bg_color, text_color):
        """İsimleri şık bir kutu içinde yazar."""
        font_badge = ("Segoe UI", 9, "bold")
        # Metin genişliğini ölçmek için geçici bir text oluşturup silebiliriz veya tahmini genişlik verebiliriz.
        # Basitlik için text'i oluşturup bbox alalım.
        t_id = self.canvas.create_text(x, y, text=text, font=font_badge, fill=text_color, tags="header")
        bbox = self.canvas.bbox(t_id)
        # Arka plan kutusu (Text'in altına çizmek için 'lower' yapmamız lazım ama silip baştan çizmek daha kolay)
        self.canvas.delete(t_id)
        
        pad = 8
        rect_id = self.canvas.create_rectangle(
            bbox[0]-pad, bbox[1]-pad, bbox[2]+pad, bbox[3]+pad, 
            fill=bg_color, outline="", tags="header"
        )
        # Köşeleri yuvarlatılmış hissi vermek için (Tkinter rectangle tam desteklemez ama bu hali temiz durur)
        self.canvas.create_text(x, y, text=text, font=font_badge, fill=text_color, tags="header")

    def add_packet_arrow(self, direction, kind, seq, ack, rwnd, length):
        """Animasyonlu ok ekler."""
        x_left = self.margin_x
        w = self.canvas.winfo_width()
        x_right = (w if w > 100 else 400) - self.margin_x
        
        y_start = self.current_y
        y_end = self.current_y + self.arrow_slant

        # --- Renk ve Stil ---
        sender_name = self.local_name if direction == "outgoing" else self.peer_name
        
        if sender_name == ROLE_A_NAME:
            arrow_color = "#3498DB" # Mavi (Client A)
        else:
            arrow_color = "#E67E22" # Turuncu (Client B)
        
        text_color = "#34495E"
        line_width = 2
        
        if kind == "ACK": 
            text_color = "#27AE60"
        elif kind == "ERROR": 
            text_color = "#C0392B"
            line_width = 3
            arrow_color = "#C0392B"
        elif kind == "RETX":
            text_color = "#D35400"

        # Etiket
        label = f"{kind}"
        details = []
        if seq is not None: details.append(f"s={seq}")
        if ack is not None: details.append(f"a={ack}")
        if rwnd is not None: details.append(f"w={rwnd}")
        if length is not None: details.append(f"len={length}")
        label_full = f"{label} {' '.join(details)}"

        # Koordinatlar
        if direction == "outgoing":
            x1, y1 = x_left, y_start
            x2, y2 = x_right, y_end
        else:
            x1, y1 = x_right, y_start
            x2, y2 = x_left, y_end

        # --- ANIMASYON BAŞLAT ---
        # Önce boş bir çizgi ve metin oluştur (gizli)
        arrow_id = self.canvas.create_line(x1, y1, x1, y1, fill=arrow_color, width=line_width, arrow=tk.LAST, tags="arrow")
        text_id = self.canvas.create_text((x1+x2)/2, y1-10, text=label_full, fill=text_color, font=("Segoe UI", 8, "bold"), tags="arrow", state="hidden")
        
        # Animasyon adımları
        steps = 20
        duration_ms = 400 # Toplam süre
        step_delay = duration_ms // steps
        
        self.animate_arrow_step(arrow_id, text_id, x1, y1, x2, y2, 0, steps, step_delay)

        # Sonraki pozisyonu ayarla
        self.current_y += self.y_step
        self._draw_headers() # Çizgileri uzat
        
        # Scroll alanını genişlet ama hemen aşağı atlama, animasyon bitince odaklanabiliriz
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # Yine de kullanıcı yeni paketi görsün diye kaydırıyoruz
        if self.current_y > self.canvas.winfo_height():
            self.canvas.yview_moveto(1.0)

    def animate_arrow_step(self, arrow_id, text_id, x1, y1, x2, y2, step, max_steps, delay):
        if step > max_steps:
            # Bittiğinde metni göster
            self.canvas.itemconfig(text_id, state="normal")
            return
        
        # Interpolasyon (Lerp)
        t = step / max_steps
        cur_x = x1 + (x2 - x1) * t
        cur_y = y1 + (y2 - y1) * t
        
        self.canvas.coords(arrow_id, x1, y1, cur_x, cur_y)
        self.after(delay, lambda: self.animate_arrow_step(arrow_id, text_id, x1, y1, x2, y2, step+1, max_steps, delay))


# ===================================================================== #
#  SINGLE CLIENT GUI (MODERNIZED)
# ===================================================================== #

class SingleClientGUI(tk.Frame):
    def __init__(self, root: tk.Tk, role: str):
        super().__init__(root)
        self.root = root
        self.role = role
        self.peer_name = ROLE_B_NAME if role == ROLE_A_NAME else ROLE_A_NAME
        
        self._input_queue: "queue.Queue[dict]" = queue.Queue()
        self.incoming_packet = None
        self.current_mode = "SENDING"
        
        # Stil Ayarları
        self._configure_styles()
        self.configure(bg=COLOR_BG_MAIN)
        
        self._build_ui()

    def _configure_styles(self):
        style = ttk.Style()
        style.theme_use('clam') # 'clam' teması daha esnek renk değişimi sağlar
        
        # Genel Frame
        style.configure("TFrame", background=COLOR_BG_MAIN)
        style.configure("Card.TFrame", background=COLOR_CARD_BG, relief="flat")
        
        # LabelFrame
        style.configure("Card.TLabelframe", background=COLOR_CARD_BG, relief="flat")
        style.configure("Card.TLabelframe.Label", background=COLOR_CARD_BG, foreground=COLOR_PRIMARY, font=FONT_BOLD)

        # Label
        style.configure("TLabel", background=COLOR_BG_MAIN, foreground=COLOR_TEXT_MAIN, font=FONT_MAIN)
        style.configure("Card.TLabel", background=COLOR_CARD_BG, foreground=COLOR_TEXT_MAIN, font=FONT_MAIN)
        style.configure("Header.TLabel", font=FONT_HEADER, background=COLOR_BG_MAIN, foreground=COLOR_TEXT_MAIN)
        
        # Entry
        style.configure("TEntry", fieldbackground="white", padding=5)

        # Buttons
        style.configure("Send.TButton", background=COLOR_SUCCESS, foreground="white", font=FONT_BOLD, borderwidth=0)
        style.map("Send.TButton", background=[("active", "#219150")])
        
        style.configure("Error.TButton", background=COLOR_DANGER, foreground="white", font=FONT_BOLD, borderwidth=0)
        style.map("Error.TButton", background=[("active", "#A93226")])

    def _build_ui(self):
        self.root.title(f"TCP Game — {self.role}")
        self.root.geometry("1000x950")
        self.root.configure(bg=COLOR_BG_MAIN)

        # --- ANA KONTEYNER (Padding ile) ---
        main_container = ttk.Frame(self, padding=20)
        main_container.pack(fill="both", expand=True)

        # --- HEADER ---
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill="x", pady=(0, 15))
        ttk.Label(header_frame, text=f"TCP Network Game Simulator", font=("Segoe UI", 18, "bold"), foreground=COLOR_PRIMARY).pack(side="left")
        ttk.Label(header_frame, text=f"Role: {self.role}", font=("Segoe UI", 12), foreground="gray").pack(side="right", anchor="s")

        # --- SCOREBOARD CARD ---
        score_card = self._create_card(main_container)
        score_card.pack(fill="x", pady=(0, 15))

        # Grid Layout for Scores
        score_card.columnconfigure(0, weight=1)
        score_card.columnconfigure(1, weight=0) # VS
        score_card.columnconfigure(2, weight=1)

        # Sol Skor (Biz)
        f_left = ttk.Frame(score_card, style="Card.TFrame")
        f_left.grid(row=0, column=0, pady=10)
        ttk.Label(f_left, text="ME", style="Card.TLabel", font=("Segoe UI", 10, "bold"), foreground=COLOR_PRIMARY).pack()
        self.my_score_label = ttk.Label(f_left, text="0", style="Card.TLabel", font=("Segoe UI", 32, "bold"), foreground=COLOR_SUCCESS)
        self.my_score_label.pack()

        # VS
        ttk.Label(score_card, text="VS", style="Card.TLabel", font=("Segoe UI", 14, "bold"), foreground="#95A5A6").grid(row=0, column=1, padx=20)

        # Sağ Skor (Rakip)
        f_right = ttk.Frame(score_card, style="Card.TFrame")
        f_right.grid(row=0, column=2, pady=10)
        ttk.Label(f_right, text="OPPONENT", style="Card.TLabel", font=("Segoe UI", 10, "bold"), foreground=COLOR_DANGER).pack()
        self.opponent_score_label = ttk.Label(f_right, text="0", style="Card.TLabel", font=("Segoe UI", 32, "bold"), foreground=COLOR_DANGER)
        self.opponent_score_label.pack()

        # RWND Bilgisi (Scoreboard içinde alt kısım)
        self.rwnd_label = ttk.Label(score_card, text="My Rwnd: 50", style="Card.TLabel", font=("Consolas", 12, "bold"), foreground="#8E44AD")
        self.rwnd_label.grid(row=1, column=0, columnspan=3, pady=(0, 10))

        # --- ORTA BÖLÜM: TIMELINE & KONTROLLER ---
        middle_pane = ttk.PanedWindow(main_container, orient="horizontal")
        middle_pane.pack(fill="both", expand=True, pady=(0, 15))

        # SOL: Timeline
        timeline_frame = self._create_card_frame(middle_pane, "Live Packet Timeline")
        middle_pane.add(timeline_frame, weight=3)
        
        self.timeline = TimelineCanvas(timeline_frame, local_name=self.role, peer_name=self.peer_name)
        self.timeline.pack(fill="both", expand=True, padx=5, pady=5)

        # SAĞ: Kontroller ve Loglar
        right_panel = ttk.Frame(middle_pane) # Konteyner
        middle_pane.add(right_panel, weight=2)
        
        # -- Gelen Paket Bilgisi --
        incoming_card = self._create_card_frame(right_panel, "Incoming Packet")
        incoming_card.pack(fill="x", pady=(0, 10), padx=(10, 0))
        
        self.incoming_label = ttk.Label(incoming_card, text="Waiting...", style="Card.TLabel", foreground="#7F8C8D", padding=10)
        self.incoming_label.pack(fill="x")

        # -- Kontrol Paneli --
        control_card = self._create_card_frame(right_panel, "Action Panel")
        control_card.pack(fill="x", pady=(0, 10), padx=(10, 0))
        
        self.mode_label = ttk.Label(control_card, text="", style="Card.TLabel", font=("Segoe UI", 10, "bold"), foreground=COLOR_SUCCESS)
        self.mode_label.pack(pady=(0, 10))

        # Form Grid
        form_frame = ttk.Frame(control_card, style="Card.TFrame")
        form_frame.pack(fill="x", padx=10)
        
        self._add_form_row(form_frame, 0, "Seq:", self._create_var("seq"))
        self._add_form_row(form_frame, 1, "Ack:", self._create_var("ack"))
        self._add_form_row(form_frame, 2, "Rwnd:", self._create_var("rwnd"))
        self._add_form_row(form_frame, 3, "Len:", self._create_var("length"))

        # Butonlar
        btn_frame = ttk.Frame(control_card, style="Card.TFrame")
        btn_frame.pack(fill="x", pady=15, padx=10)
        
        self.send_btn = ttk.Button(btn_frame, text="SEND PACKET", style="Send.TButton", command=self._on_send_clicked)
        self.send_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.error_btn = ttk.Button(btn_frame, text="REPORT ERROR", style="Error.TButton", command=self._on_error_clicked)
        self.error_btn.pack(side="right", fill="x", expand=True, padx=(5, 0))
        
        self.info_label = ttk.Label(control_card, text="", style="Card.TLabel", font=("Segoe UI", 8, "italic"), foreground="gray")
        self.info_label.pack(pady=(0, 10))

        # -- Loglar --
        log_card = self._create_card_frame(right_panel, "System Logs")
        log_card.pack(fill="both", expand=True, padx=(10, 0))
        
        self.log = scrolledtext.ScrolledText(log_card, height=10, font=FONT_MONO, bg="#FAFAFA", relief="flat")
        self.log.pack(fill="both", expand=True, padx=5, pady=5)
        self.log.configure(state="disabled")

        self.pack(fill="both", expand=True)

    # --- YARDIMCI METODLAR ---
    def _create_card(self, parent):
        """Beyaz arka planlı, gölge görünümlü (basit border) çerçeve."""
        f = ttk.Frame(parent, style="Card.TFrame", padding=10)
        # Tkinter'da gerçek gölge zordur, border ile simüle ediyoruz
        f.configure(borderwidth=1, relief="solid") 
        return f

    def _create_card_frame(self, parent, title):
        """Başlıklı beyaz çerçeve."""
        f = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=10)
        return f

    def _create_var(self, name):
        if not hasattr(self, f"{name}_var"):
            setattr(self, f"{name}_var", tk.StringVar())
        return getattr(self, f"{name}_var")

    def _add_form_row(self, parent, row, label_text, var):
        ttk.Label(parent, text=label_text, style="Card.TLabel", width=6, anchor="e").grid(row=row, column=0, padx=5, pady=5)
        ttk.Entry(parent, textvariable=var, font=("Consolas", 10)).grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        parent.columnconfigure(1, weight=1)

    # --- Orijinal Fonksiyonlar (Aynen Korundu) ---
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
        self.append_log(f"📤 SENDING: seq={seq}, ack={ack}, rwnd={rwnd}, len={length}")

    def _on_error_clicked(self):
        input_data = {"action": "ERROR", "seq": 0, "ack": 0, "rwnd": 0, "length": 0}
        self._input_queue.put(input_data)
        self.append_log("⚠️ USER TRIGGERED ERROR REPORT")

    def get_input_blocking(self) -> dict:
        return self._input_queue.get()
    
    def get_input_with_timeout(self, timeout) -> dict:
        return self._input_queue.get(timeout=timeout)

    def set_mode(self, mode: str, message: str = ""):
        self.current_mode = mode
        if mode == "SENDING":
            self.mode_label.config(text="🚀 YOUR TURN", foreground=COLOR_SUCCESS) 
            self.info_label.config(text="Prepare packet and click SEND")
            self.error_btn.state(["disabled"])
            self.send_btn.state(["!disabled"])
        elif mode == "RESPONDING":
            self.mode_label.config(text=f"⚡ ACTION REQUIRED: {message}", foreground=COLOR_PRIMARY) 
            self.info_label.config(text="Respond (SEND) or Reject (ERROR)")
            self.error_btn.state(["!disabled"])
            self.send_btn.state(["!disabled"])
        elif mode == "WAITING":
            self.mode_label.config(text="⏳ WAITING PEER...", foreground="#F39C12")
            self.info_label.config(text="Waiting for opponent's move")
            self.error_btn.state(["disabled"])
            self.send_btn.state(["disabled"])

    def show_incoming_packet(self, pkt: Packet):
        def _update_ui():
            if pkt.type == "ERROR":
                text = "❌ ERROR RECEIVED"
                fg = COLOR_DANGER
            else:
                text = f"📥 {pkt.type} | s={pkt.seq} a={pkt.ack} w={pkt.rwnd} l={pkt.length}"
                fg = COLOR_PRIMARY
            
            self.incoming_label.config(text=text, foreground=fg)
            self.incoming_packet = pkt
            self.append_log(f"{text}")
            self.set_mode("RESPONDING", "Packet Received")
        self.root.after(0, _update_ui)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"> {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")
    
    def update_scores(self, my_score: int, opponent_score: int):
        def _update():
            self.my_score_label.config(text=str(my_score))
            self.opponent_score_label.config(text=str(opponent_score))
        self.root.after(0, _update)
    
    def animate_send(self, pkt): 
        self.root.after(0, lambda: self.timeline.add_packet_arrow(
            "outgoing", pkt.type, pkt.seq, pkt.ack, pkt.rwnd, pkt.length
        ))
    
    def animate_recv(self, pkt): 
        self.root.after(0, lambda: self.timeline.add_packet_arrow(
            "incoming", pkt.type, pkt.seq, pkt.ack, pkt.rwnd, pkt.length
        ))
    
    def start_rwnd_monitor(self, game_logic):
        val = game_logic.current_rwnd
        self.rwnd_label.config(text=f"My Rwnd: {val}")
        if val <= 0:
            self.rwnd_label.config(foreground=COLOR_DANGER)
        else:
            self.rwnd_label.config(foreground="#8E44AD")
        self.root.after(100, self.start_rwnd_monitor, game_logic)

# ===================================================================== #
#  GAME LOGIC + GUI INTEGRATION (NO CHANGE REQUIRED HERE)
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
        self.ui.append_log(f"Sent: {pkt.type} s={pkt.seq} a={pkt.ack}")
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
        
        self.ui.animate_recv(pkt)
        self.ui.show_incoming_packet(pkt)
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return pkt
    
    def _create_response_packet_from_input(self, user_input: dict):
        result = super()._create_response_packet_from_input(user_input)
        self.ui.update_scores(self.scoreboard.my_score, self.scoreboard.opponent_score)
        return result
    
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

        tentative_data_len = 0
        if pkt.type == "DATA" and is_valid:
            expected_now = self.validator.peer.expected_seq
            if (pkt.seq or 0) + (pkt.length or 0) == expected_now:
                tentative_data_len = (pkt.length or 0)
                self.recv_buffer_used += tentative_data_len
                if self.recv_buffer_used > MAX_RWND: self.recv_buffer_used = MAX_RWND
                self.current_rwnd = MAX_RWND - self.recv_buffer_used
                self.logger.info(f"Tentative Buffer Update: rwnd -> {self.current_rwnd}")

        processed_ack_early = False
        saved_gbn_base = self.gbn.state.base
        saved_gbn_dup = self.gbn.state.duplicate_ack_count
        saved_gbn_next = self.gbn.state.next_seq
        
        if is_valid and pkt.type == "ACK" and pkt.ack is not None:
            win_adv, retx_req = self.gbn.on_ack(pkt.ack)
            processed_ack_early = True
            
            if retx_req:
                self._pending_retransmit = True
                self.ui.mode_label.config(text="⚠️ FAST RETRANSMIT TRIGGERED!", foreground=COLOR_DANGER)
                self.ui.info_label.config(text=f"Packet Loss! Resend from Base={self.gbn.state.base}")

        user_decision = self._poll_input_queue()
        outgoing_comment_flag = ""

        if user_decision["action"] == "ERROR":
             if tentative_data_len > 0:
                 self.recv_buffer_used -= tentative_data_len
                 if self.recv_buffer_used < 0: self.recv_buffer_used = 0
                 self.current_rwnd = MAX_RWND - self.recv_buffer_used
             
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