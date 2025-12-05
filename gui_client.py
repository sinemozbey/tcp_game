# gui_client.py

import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from core.game_logic import GameLogic
from core.packet import Packet
from utils.config import MIN_SEGMENT_SIZE, MAX_SEGMENT_SIZE


class GameGUI:
    """
    Kullanıcıdan paket uzunluğunu alan GUI.
    GameLogicGUI bu sınıftan length isteyip queue üzerinden alacak.
    Aynı pencerede rol, son girilen length ve basit bir log alanı gösterilir.
    """

    def __init__(
        self,
        root: tk.Tk,
        role: str,
        min_size: int = MIN_SEGMENT_SIZE,
        max_size: int = MAX_SEGMENT_SIZE,
    ) -> None:
        self.root = root
        self.role = role
        self.min_size = min_size
        self.max_size = max_size

        self.root.title(f"TCP Game - {role}")
        self._length_queue: "queue.Queue[int]" = queue.Queue()

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI bileşenleri
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # Pencere boyutu ve temel stil
        self.root.geometry("520x320")
        self.root.minsize(480, 300)

        main = ttk.Frame(self.root, padding=15)
        main.pack(fill="both", expand=True)

        # Başlık
        title = ttk.Label(
            main,
            text=f"TCP Game – {self.role}",
            font=("Helvetica", 16, "bold"),
        )
        title.pack(pady=(0, 8))

        # Açıklama
        info = ttk.Label(
            main,
            text=(
                f"DATA length giriniz: {self.min_size}-{self.max_size}\n"
                f"0 = sadece ACK göndermek için"
            ),
            justify="center",
        )
        info.pack(pady=(0, 10))

        # Giriş alanı + buton
        input_frame = ttk.LabelFrame(main, text="Packet Control")
        input_frame.pack(fill="x", pady=(0, 10))

        inner = ttk.Frame(input_frame)
        inner.pack(padx=10, pady=8, fill="x")

        self.length_var = tk.StringVar()
        entry = ttk.Entry(
            inner,
            textvariable=self.length_var,
            width=10,
            justify="center",
        )
        entry.grid(row=0, column=0, padx=(0, 8))
        entry.focus_set()

        send_btn = ttk.Button(inner, text="Send", command=self._on_send_clicked)
        send_btn.grid(row=0, column=1, padx=(0, 8))

        # Status label
        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(inner, textvariable=self.status_var, foreground="gray")
        status.grid(row=0, column=2, sticky="w")

        # Basit log alanı
        log_frame = ttk.LabelFrame(main, text="Local Info")
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            height=8,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        self._append_log("GUI initialized. Waiting for game loop...")

    # ------------------------------------------------------------------ #
    # Yardımcı: log yazdırma
    # ------------------------------------------------------------------ #

    def _append_log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # Eventler
    # ------------------------------------------------------------------ #

    def _on_send_clicked(self):
        text = self.length_var.get().strip()

        if text == "":
            # boşsa default 1 byte
            length = 1
        else:
            try:
                length = int(text)
            except ValueError:
                messagebox.showerror(
                    "Invalid value",
                    "Lütfen sayısal bir değer gir (örn. 0, 1, 2, 3...).",
                )
                return

        if length < 0:
            messagebox.showerror(
                "Invalid value",
                "Negatif uzunluk olamaz.",
            )
            return

        # 0 = sadece ACK → kabul
        if length != 0 and (length < self.min_size or length > self.max_size):
            messagebox.showerror(
                "Invalid value",
                f"Uzunluk {self.min_size}-{self.max_size} aralığında "
                f"veya 0 (sadece ACK) olmalı.",
            )
            return

        # Buraya geldiysek geçerli
        self._length_queue.put(length)
        self.status_var.set(f"Son girilen length = {length}")
        self._append_log(f"User input: length={length}")
        self.length_var.set("")

    # ------------------------------------------------------------------ #
    # GameLogic'in kullanacağı arayüz
    # ------------------------------------------------------------------ #

    def get_next_length_blocking(self) -> int:
        """
        GameLogic thread'i tarafından çağrılır.
        Kullanıcı GUI'den bir değer girene kadar bloklar.
        """
        length = self._length_queue.get()
        return length


class GameLogicGUI(GameLogic):
    """
    GameLogic + GUI entegrasyonu.
    Sadece _create_next_data_packet GUI'den length alacak şekilde override edildi.
    """

    def __init__(self, role_name, conn, starts_first, ui: GameGUI):
        super().__init__(role_name, conn, starts_first)
        self.ui = ui

    def _create_next_data_packet(self) -> Packet:
        """
        CLI'deki input() yerine GUI'den paket uzunluğu alır.
        """

        while True:
            length = self.ui.get_next_length_blocking()

            # 0 → sadece ACK gönder (DATA yok)
            if length == 0:
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                self.ui._append_log(
                    f"Preparing ACK-only packet: ack={ack}, rwnd={rwnd}"
                )
                return Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="GUI ACK-only",
                )

            # normal DATA
            try:
                seq, real_length = self.gbn.next_data_segment(length=length)
            except RuntimeError:
                # pencere dolu → sadece ACK gönder
                ack = self.validator.peer.last_seq + self.validator.peer.last_len
                rwnd = self.current_rwnd
                self.ui._append_log(
                    "Window full → sending ACK-only instead of DATA."
                )
                return Packet.make_ack(
                    seq=self.gbn.state.next_seq,
                    ack=ack,
                    rwnd=rwnd,
                    comment="Window full, ACK-only (GUI)",
                )

            ack = self.validator.peer.last_seq + self.validator.peer.last_len
            rwnd = self.current_rwnd
            self.ui._append_log(
                f"Preparing DATA packet: seq={seq}, len={real_length}, "
                f"ack={ack}, rwnd={rwnd}"
            )
            return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length)


# ---------------------------------------------------------------------- #
# Dışarıdan çağrılacak fonksiyon
# ---------------------------------------------------------------------- #

def run_gui_client(role: str, conn, starts_first: bool):
    """
    client_A / client_B dosyalarından çağrılan entry point.
    Tk penceresini açar, GameLogicGUI'yi ayrı bir thread'de çalıştırır.
    """
    root = tk.Tk()
    ui = GameGUI(root, role, MIN_SEGMENT_SIZE, MAX_SEGMENT_SIZE)
    game = GameLogicGUI(role, conn, starts_first, ui)

    # Game loop ayrı thread’de
    t = threading.Thread(target=game.run, daemon=True)
    t.start()

    # Tk event loop
    try:
        root.mainloop()
    finally:
        # pencere kapanınca bağlantıyı kapat
        try:
            conn.close()
        except Exception:
            pass
