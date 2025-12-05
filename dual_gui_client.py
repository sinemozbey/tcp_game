# dual_gui_client.py

import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from core.connection import Connection
from core.game_logic import GameLogic
from core.packet import Packet
from utils.config import (
    MIN_SEGMENT_SIZE,
    MAX_SEGMENT_SIZE,
    ROLE_A_NAME,
    ROLE_B_NAME,
)

# ======================================================================
#  ORTA KISIM: PAKET ANİMASYON CANVAS'I
# ======================================================================

class AnimationCanvas:
    """
    Ortada iki uç (ClientA - ClientB) ve aralarında hareket eden paket topları.
    GameLogic thread'leri sadece enqueue_packet(direction, pkt) çağırıyor,
    animasyonu GUI thread'i yapıyor.
    """

    def __init__(self, parent: tk.Widget):
        self.canvas = tk.Canvas(parent, height=140, bg="#f5f5f5", highlightthickness=0)
        self.canvas.pack(fill="x", padx=10, pady=(0, 10))

        self.event_queue: "queue.Queue[tuple[str, Packet]]" = queue.Queue()
        self.animation_running = False

        # Sabit koordinatlar
        self.width = 1000
        self.canvas.config(width=self.width)

        self.y_mid = 70
        self.a_x = 120
        self.b_x = self.width - 120

        self._draw_endpoints()

    def _draw_endpoints(self):
        # ClientA "bilgisayar"ı
        self.canvas.create_rectangle(
            self.a_x - 30,
            self.y_mid - 20,
            self.a_x + 30,
            self.y_mid + 20,
            fill="#d9eaff",
            outline="#5b8def",
            width=2,
        )
        self.canvas.create_text(
            self.a_x,
            self.y_mid + 32,
            text="ClientA",
            font=("Helvetica", 10, "bold"),
        )

        # ClientB "bilgisayar"ı
        self.canvas.create_rectangle(
            self.b_x - 30,
            self.y_mid - 20,
            self.b_x + 30,
            self.y_mid + 20,
            fill="#ffd9d9",
            outline="#f26d6d",
            width=2,
        )
        self.canvas.create_text(
            self.b_x,
            self.y_mid + 32,
            text="ClientB",
            font=("Helvetica", 10, "bold"),
        )

        # Aradaki hat
        self.canvas.create_line(
            self.a_x + 35,
            self.y_mid,
            self.b_x - 35,
            self.y_mid,
            dash=(4, 2),
        )

    # --------------------------------------------------------------- #
    # Dışarıdan kullanılan API
    # --------------------------------------------------------------- #

    def enqueue_packet(self, direction: str, pkt: Packet):
        """
        direction: "A->B" veya "B->A"
        """
        self.event_queue.put((direction, pkt))

    def start(self, root: tk.Tk):
        """GUI thread'i içinde periyodik olarak kuyruğu kontrol eder."""
        self._process_queue()
        # 30 ms'de bir kontrol
        root.after(30, lambda: self.start(root))

    # --------------------------------------------------------------- #
    # İç animasyon mantığı
    # --------------------------------------------------------------- #

    def _process_queue(self):
        if self.animation_running:
            return

        try:
            direction, pkt = self.event_queue.get_nowait()
        except queue.Empty:
            return

        self.animation_running = True
        self._animate_packet(direction, pkt)

    def _animate_packet(self, direction: str, pkt: Packet):
        # Başlangıç / bitiş x koordinatları
        if direction == "A->B":
            x_start, x_end = self.a_x + 40, self.b_x - 40
        else:  # "B->A"
            x_start, x_end = self.b_x - 40, self.a_x + 40

        y = self.y_mid

        # Paket topu (daire) ve label
        radius = 14
        circle = self.canvas.create_oval(
            x_start - radius,
            y - radius,
            x_start + radius,
            y + radius,
            fill="#ffffff",
            outline="#333333",
        )

        label_text = f"{pkt.type}\nlen={pkt.length or 0}"
        label = self.canvas.create_text(
            x_start,
            y,
            text=label_text,
            font=("Helvetica", 8, "bold"),
        )

        steps = 40
        total_dx = (x_end - x_start)
        dx = total_dx / steps
        delay_ms = 25  # toplam ~1s civarı

        def step(i=0):
            if i >= steps:
                # animasyon bitti
                self.canvas.delete(circle)
                self.canvas.delete(label)
                self.animation_running = False
                return

            self.canvas.move(circle, dx, 0)
            self.canvas.move(label, dx, 0)
            self.canvas.after(delay_ms, lambda: step(i + 1))

        step()


# ======================================================================
#  SOL / SAĞ PANEL – KULLANICI GİRİŞİ ve LOG
# ======================================================================

class SidePanel:
    """
    Tek bir client (ClientA veya ClientB) için GUI paneli.
    """

    def __init__(self, parent: tk.Widget, role: str) -> None:
        self.role = role
        self.length_queue: "queue.Queue[int]" = queue.Queue()

        self.frame = ttk.Frame(parent, padding=10)
        self.frame.pack(fill="both", expand=True)

        self._build_ui()

    def _build_ui(self):
        title = ttk.Label(
            self.frame,
            text=f"{self.role}",
            font=("Helvetica", 13, "bold"),
        )
        title.pack(pady=(0, 5))

        subtitle = ttk.Label(
            self.frame,
            text=(
                f"DATA length: {MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE}\n"
                f"0 = sadece ACK göndermek için"
            ),
            justify="center",
        )
        subtitle.pack(pady=(0, 10))

        packet_group = ttk.LabelFrame(self.frame, text="Packet Control", padding=10)
        packet_group.pack(fill="x", padx=5, pady=(0, 10))

        entry_frame = ttk.Frame(packet_group)
        entry_frame.pack(fill="x")

        self.length_var = tk.StringVar()
        entry = ttk.Entry(
            entry_frame,
            textvariable=self.length_var,
            width=8,
            justify="center",
        )
        entry.grid(row=0, column=0, padx=(0, 8))
        entry.focus_set()

        self.send_btn = ttk.Button(
            entry_frame,
            text="Send",
            command=self._on_send_clicked,
        )
        self.send_btn.grid(row=0, column=1, padx=(0, 8))

        self.last_len_var = tk.StringVar(value="Son girilen length = -")
        last_len_lbl = ttk.Label(entry_frame, textvariable=self.last_len_var)
        last_len_lbl.grid(row=0, column=2, sticky="w")

        info_group = ttk.LabelFrame(self.frame, text="Local Info", padding=10)
        info_group.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self.log_text = tk.Text(
            info_group,
            height=12,
            wrap="word",
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

        self._append_log("GUI initialized. Waiting for game loop...")

    # --------------------- küçük yardımcılar --------------------------- #

    def _append_log(self, msg: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

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
                    f"{self.role}: Lütfen sayısal bir değer gir (örn. 0, 1, 2, 3...).",
                )
                return

        if length < 0:
            messagebox.showerror(
                "Invalid value",
                f"{self.role}: Negatif uzunluk olamaz.",
            )
            return

        if length != 0 and not (MIN_SEGMENT_SIZE <= length <= MAX_SEGMENT_SIZE):
            messagebox.showerror(
                "Invalid value",
                (
                    f"{self.role}: Uzunluk {MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE} "
                    f"aralığında veya 0 (sadece ACK) olmalı."
                ),
            )
            return

        self.length_queue.put(length)
        self.last_len_var.set(f"Son girilen length = {length}")
        self._append_log(f"User input: length={length}")
        self.length_var.set("")

    def get_next_length_blocking(self) -> int:
        return self.length_queue.get()

    # GameLogic'ten gelen loglar
    def log_packet_preparation(self, seq: int, length: int, ack: int, rwnd: int):
        self._append_log(
            f"Preparing DATA packet: seq={seq}, len={length}, ack={ack}, rwnd={rwnd}"
        )

    def log_window_full_ack_only(self):
        self._append_log("Window full → sending ACK-only instead of DATA.")

    def log_send_packet(self, pkt: Packet):
        info = (
            f"SEND → {pkt.type}"
            f" | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
        )
        self._append_log(info)

    def log_recv_packet(self, pkt: Packet):
        info = (
            f"RECV ← {pkt.type}"
            f" | seq={pkt.seq}, ack={pkt.ack}, rwnd={pkt.rwnd}, len={pkt.length}"
        )
        self._append_log(info)


# ======================================================================
#  GAME LOGIC + GUI KÖPRÜSÜ
# ======================================================================

class DualGameLogicGUI(GameLogic):
    """
    GameLogic + SidePanel + AnimationCanvas entegrasyonu.
    Her taraf için ayrı instance, aynı AnimationCanvas paylaşılıyor.
    """

    def __init__(
        self,
        role_name: str,
        conn: Connection,
        starts_first: bool,
        ui_panel: SidePanel,
        anim_canvas: AnimationCanvas,
    ):
        super().__init__(role_name, conn, starts_first)
        self.ui = ui_panel
        self.anim = anim_canvas

        # Animasyon için yön
        if role_name == ROLE_A_NAME:
            self.direction = "A->B"
        else:
            self.direction = "B->A"

    # ------------- DATA paketini GUI'den alarak oluşturma --------------- #

    def _create_next_data_packet(self) -> Packet:
        while True:
            length = self.ui.get_next_length_blocking()

            # 0 → sadece ACK
            if length == 0:
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="GUI ACK-only",
                )
                return pkt

            try:
                seq, real_length = self.gbn.next_data_segment(length=length)
            except RuntimeError:
                # pencere dolu -> sadece ACK
                self.ui.log_window_full_ack_only()
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                pkt = Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="Window full, ACK-only (GUI)",
                )
                return pkt

            ack = self.validator.peer.last_seq + self.validator.peer.last_len
            rwnd = self.current_rwnd
            self.ui.log_packet_preparation(seq, real_length, ack, rwnd)
            pkt = Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length)
            return pkt

    # ------------- Gönderme / Alma override (log + animasyon) ---------- #

    def _send_packet(self, pkt: Packet):
        # önce panel logu
        self.ui.log_send_packet(pkt)
        # animasyon kuyruğuna at (sadece DATA/ACK/ERROR için istersen)
        if pkt.type in ("DATA", "ACK", "ERROR"):
            self.anim.enqueue_packet(self.direction, pkt)
        # sonra normal send
        super()._send_packet(pkt)

    def _receive_packet(self) -> Packet:
        pkt = super()._receive_packet()
        self.ui.log_recv_packet(pkt)
        return pkt


# ======================================================================
#  TEK PENCERELİ UYGULAMA – DUAL VIEW
# ======================================================================

def run_dual_gui():
    root = tk.Tk()
    root.title("TCP Game – Dual View")
    root.geometry("1160x640")

    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)

    header = ttk.Label(
        main,
        text="TCP Game – ClientA & ClientB",
        font=("Helvetica", 16, "bold"),
    )
    header.pack(pady=(0, 5))

    subtitle = ttk.Label(
        main,
        text="Solda ClientA, sağda ClientB. Ortada gerçek TCP paket akışı animasyonlu olarak gösterilir.",
        justify="center",
    )
    subtitle.pack(pady=(0, 10))

    # Ortadaki animasyon alanı
    anim = AnimationCanvas(main)

    # Alt tarafta iki panel yan yana
    panels_frame = ttk.Frame(main)
    panels_frame.pack(fill="both", expand=True)

    # İki kolon: A ve B
    panels_frame.columnconfigure(0, weight=1)
    panels_frame.columnconfigure(1, weight=1)
    panels_frame.rowconfigure(0, weight=1)

    left_frame = ttk.LabelFrame(panels_frame, text=ROLE_A_NAME, padding=5)
    right_frame = ttk.LabelFrame(panels_frame, text=ROLE_B_NAME, padding=5)

    left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))


    ui_a = SidePanel(left_frame, ROLE_A_NAME)
    ui_b = SidePanel(right_frame, ROLE_B_NAME)

    # GameLogic + Connection thread’leri
    def start_games():
        def run_a():
            conn = Connection.create_as_server()
            game = DualGameLogicGUI(ROLE_A_NAME, conn, True, ui_a, anim)
            game.run()

        def run_b():
            conn = Connection.create_as_client()
            game = DualGameLogicGUI(ROLE_B_NAME, conn, False, ui_b, anim)
            game.run()

        threading.Thread(target=run_a, daemon=True).start()
        threading.Thread(target=run_b, daemon=True).start()

    start_games()

    # Animasyon kuyruğunu başlat
    anim.start(root)

    root.mainloop()


if __name__ == "__main__":
    run_dual_gui()
