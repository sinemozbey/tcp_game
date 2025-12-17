# gui_client.py
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from core.game_logic import GameLogic
from core.packet import Packet
from utils.config import MAX_RWND, MIN_SEGMENT_SIZE, MAX_SEGMENT_SIZE


# ============================================================
#  GUI: Manual TCP Game Client (seq/ack/rwnd/len + ERROR karar)
# ============================================================

class ManualClientGUI(ttk.Frame):
    """
    Manual GUI:
      - Your turn: enter seq/ack/rwnd/len and click SEND
      - On DATA receive: dialog -> SEND ERROR or ACCEPT
      - Log panel for events
    """

    def __init__(self, root: tk.Tk, role: str):
        super().__init__(root, padding=12)
        self.root = root
        self.role = role

        # Thread-safe request channel (Game thread -> UI thread)
        self._ui_requests: "queue.Queue[dict]" = queue.Queue()

        # Blocking wait objects (Game thread waits, UI resolves)
        self._need_packet_event = threading.Event()
        self._packet_result: dict | None = None

        self._need_decision_event = threading.Event()
        self._decision_result: bool | None = None  # True => send ERROR, False => accept

        self._build_ui()
        self.pack(fill="both", expand=True)

        # UI poll loop (handles requests from game thread safely)
        self.root.after(50, self._poll_requests)

    # ---------------- UI BUILD ----------------

    def _build_ui(self):
        self.root.title(f"TCP Game (Manual) - {self.role}")
        self.root.geometry("920x620")

        title = ttk.Label(self, text=f"TCP Game – {self.role} (MANUAL)", font=("Helvetica", 16, "bold"))
        title.pack(anchor="center", pady=(0, 8))

        self.status_var = tk.StringVar(value="Status: Waiting...")
        status = ttk.Label(self, textvariable=self.status_var)
        status.pack(anchor="center", pady=(0, 10))

        # Packet entry panel
        form = ttk.LabelFrame(self, text="Send Packet (Manual)", padding=10)
        form.pack(fill="x", pady=(0, 10))

        grid = ttk.Frame(form)
        grid.pack(fill="x")

        self.seq_var = tk.StringVar()
        self.ack_var = tk.StringVar()
        self.rwnd_var = tk.StringVar()
        self.len_var = tk.StringVar()

        def add_row(r, label, var, hint):
            ttk.Label(grid, text=label, width=10).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=4)
            e = ttk.Entry(grid, textvariable=var, width=18)
            e.grid(row=r, column=1, sticky="w", pady=4)
            ttk.Label(grid, text=hint).grid(row=r, column=2, sticky="w", padx=(10, 0), pady=4)
            return e

        self.seq_entry = add_row(0, "SEQ", self.seq_var, ">= 0")
        self.ack_entry = add_row(1, "ACK", self.ack_var, ">= 0")
        self.rwnd_entry = add_row(2, "RWND", self.rwnd_var, f"0..{MAX_RWND}")
        self.len_entry = add_row(3, "LEN", self.len_var, f"0(ACK-only) or {MIN_SEGMENT_SIZE}..{MAX_SEGMENT_SIZE}")

        btn_row = ttk.Frame(form)
        btn_row.pack(fill="x", pady=(8, 0))

        self.send_btn = ttk.Button(btn_row, text="SEND", command=self._on_send_clicked)
        self.send_btn.pack(side="left")

        ttk.Button(btn_row, text="Clear", command=self._clear_fields).pack(side="left", padx=(8, 0))

        self._set_send_enabled(False)

        # Log panel
        logs = ttk.LabelFrame(self, text="Log", padding=10)
        logs.pack(fill="both", expand=True)

        self.log = tk.Text(logs, wrap="word", height=18)
        self.log.pack(fill="both", expand=True)
        self._append_log("GUI ready.\n")

    # ---------------- Helpers ----------------

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_fields(self):
        self.seq_var.set("")
        self.ack_var.set("")
        self.rwnd_var.set("")
        self.len_var.set("")

    def _set_send_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for w in (self.seq_entry, self.ack_entry, self.rwnd_entry, self.len_entry, self.send_btn):
            w.configure(state=state)

    def _parse_int(self, name: str, value: str) -> int:
        try:
            return int(value.strip())
        except Exception:
            raise ValueError(f"{name} must be an integer.")

    # ---------------- UI events ----------------

    def _on_send_clicked(self):
        # Only valid when game requested packet
        try:
            seq = self._parse_int("SEQ", self.seq_var.get())
            ack = self._parse_int("ACK", self.ack_var.get())
            rwnd = self._parse_int("RWND", self.rwnd_var.get())
            length = self._parse_int("LEN", self.len_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return

        # Soft bounds (manuel oyunda hataya izin var ama aşırı değerleri sınırlıyoruz)
        if seq < 0: seq = 0
        if ack < 0: ack = 0
        if rwnd < 0: rwnd = 0
        if rwnd > MAX_RWND: rwnd = MAX_RWND

        if length < 0:
            length = 0
        if length != 0:
            if length < MIN_SEGMENT_SIZE: length = MIN_SEGMENT_SIZE
            if length > MAX_SEGMENT_SIZE: length = MAX_SEGMENT_SIZE

        self._packet_result = {"seq": seq, "ack": ack, "rwnd": rwnd, "length": length}
        self._append_log(f"[INPUT] seq={seq}, ack={ack}, rwnd={rwnd}, len={length}")
        self.status_var.set("Status: Packet submitted. Waiting...")
        self._set_send_enabled(False)

        # Unblock game thread
        self._need_packet_event.set()

    # ---------------- Requests from game thread ----------------

    def request_packet_input(self):
        """Called from game thread: ask UI to enable form and wait."""
        self._need_packet_event.clear()
        self._packet_result = None
        self._ui_requests.put({"type": "need_packet"})
        self._need_packet_event.wait()
        assert self._packet_result is not None
        return self._packet_result

    def request_error_decision(self, pkt: Packet, is_valid: bool, reason: str) -> bool:
        """Called from game thread: ask UI for ERROR vs ACCEPT decision and wait."""
        self._need_decision_event.clear()
        self._decision_result = None
        self._ui_requests.put(
            {"type": "need_decision", "pkt": pkt, "is_valid": is_valid, "reason": reason}
        )
        self._need_decision_event.wait()
        assert self._decision_result is not None
        return self._decision_result

    def notify_send(self, pkt: Packet):
        """Called from game thread: log send."""
        self._ui_requests.put({"type": "log", "text": f"SEND → {pkt.type} | s={pkt.seq} a={pkt.ack} w={pkt.rwnd} len={pkt.length}"})

    def notify_recv(self, pkt: Packet):
        """Called from game thread: log recv."""
        self._ui_requests.put({"type": "log", "text": f"RECV ← {pkt.type} | s={pkt.seq} a={pkt.ack} w={pkt.rwnd} len={pkt.length}"})

    # ---------------- UI polling loop ----------------

    def _poll_requests(self):
        try:
            while True:
                item = self._ui_requests.get_nowait()
                t = item.get("type")

                if t == "need_packet":
                    self.status_var.set("Status: Your turn! Enter seq/ack/rwnd/len and press SEND.")
                    self._set_send_enabled(True)
                    self.seq_entry.focus_set()

                elif t == "need_decision":
                    pkt: Packet = item["pkt"]
                    is_valid: bool = item["is_valid"]
                    reason: str = item["reason"]
                    self._show_decision_dialog(pkt, is_valid, reason)

                elif t == "log":
                    self._append_log(item["text"])

        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._poll_requests)

    def _show_decision_dialog(self, pkt: Packet, is_valid: bool, reason: str):
        """
        Modal dialog: SEND ERROR vs ACCEPT
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Incoming DATA – Decision")
        dlg.geometry("520x260")
        dlg.transient(self.root)
        dlg.grab_set()

        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Incoming DATA received", font=("Helvetica", 12, "bold")).pack(anchor="w")
        ttk.Separator(frm).pack(fill="x", pady=8)

        details = (
            f"seq={pkt.seq} | ack={pkt.ack} | rwnd={pkt.rwnd} | len={pkt.length}\n\n"
            f"Validator hint: {'VALID' if is_valid else 'INVALID'}\n"
            f"Reason: {reason}"
        )
        ttk.Label(frm, text=details, justify="left").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=10)

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def choose(send_error: bool):
            self._decision_result = send_error
            self._append_log(f"[DECISION] {'SEND ERROR' if send_error else 'ACCEPT'} for incoming DATA")
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()
            self._need_decision_event.set()

        ttk.Button(btns, text="ACCEPT (continue)", command=lambda: choose(False)).pack(side="left")
        ttk.Button(btns, text="SEND ERROR", command=lambda: choose(True)).pack(side="left", padx=(10, 0))

        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(False))


# ============================================================
# GameLogic wrapper: connect UI providers + log hooks
# ============================================================

class GameLogicManualGUI(GameLogic):
    def __init__(self, role_name, conn, starts_first, ui: ManualClientGUI):
        super().__init__(
            role_name=role_name,
            conn=conn,
            starts_first=starts_first,
            manual_packet_provider=ui.request_packet_input,
            error_decision_provider=ui.request_error_decision,
        )
        self.ui = ui

    def _send_packet(self, pkt: Packet):
        self.ui.notify_send(pkt)
        super()._send_packet(pkt)

    def _receive_packet(self) -> Packet:
        pkt = super()._receive_packet()
        self.ui.notify_recv(pkt)
        return pkt


# ============================================================
# Public entry point
# ============================================================

def run_gui_client(role: str, conn, starts_first: bool):
    root = tk.Tk()
    ui = ManualClientGUI(root, role=role)
    game = GameLogicManualGUI(role, conn, starts_first, ui)

    t = threading.Thread(target=game.run, daemon=True)
    t.start()

    try:
        root.mainloop()
    finally:
        try:
            conn.close()
        except Exception:
            pass
