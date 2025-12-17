# core/game_logic.py

import time
from collections import OrderedDict
from typing import List

from utils.logger import get_logger
from utils.config import (
    GAME_DURATION_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    BUFFER_DRAIN_INTERVAL_SECONDS,
    WINDOW_STALL_TIMEOUT_SECONDS,
    MAX_RWND,
    RWND_INCREASE_STEP,
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
from typing import List, Callable, Optional



class GameLogic:
    """
    High-level orchestration of the TCP Game.
    One instance runs per client.
    """

class GameLogic:
    """
    High-level orchestration of the TCP Game.
    One instance runs per client.
    """

    def __init__(
        self,
        role_name: str,
        conn: Connection,
        starts_first: bool,
        length_provider: Optional[Callable[[], int]] = None,
    ):
        """
        length_provider:
          - None ise: eski davranış, terminalden input() ile uzunluk sorar.
          - GUI kullanırken: dışarıdan verilen bir fonksiyon ile uzunluk alır.
            Bu fonksiyon bloklayıcı olabilir (ör. queue.get()).
        """
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

        self._pending_retransmit = False
        # Go-Back-N için: henüz ACK almamış segmentleri (seq -> length) tut
        self._unacked_segments: "OrderedDict[int, int]" = OrderedDict()
        # Fast retransmit sonrası: base'ten itibaren yeniden gönderilecek segmentler
        self._retransmit_queue: list[tuple[int, int]] = []
        self._is_retransmitting = False
        # Skor ve "oyuncu hilesi" değerlendirmesi için son gönderilen paket bilgisi
        self._last_outgoing_packet: Packet | None = None
        self._last_outgoing_intentionally_invalid: bool = False
        self._last_outgoing_was_invalid: bool | None = None
        self._last_outgoing_invalid_reason: str | None = None

        # GUI veya CLI’den uzunluk sağlayan fonksiyon
        self.length_provider = length_provider

    
    def _get_segment_length_from_user(self) -> int:
        """
        Kullanıcıdan paket uzunluğu isteyen soyut katman.
        Eğer dışarıdan provider verilmemişse, klasik input() kullanır.
        """
        if self.segment_length_provider is not None:
            return self.segment_length_provider(self.role)

        # Default: CLI (terminal) input
        while True:
            try:
                size_str = input(
                    f"{self.role}: Göndereceğin DATA uzunluğu "
                    f"({MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE}, 0 = sadece ACK): "
                ).strip()
                if size_str == "":
                    return 1
                return int(size_str)
            except ValueError:
                print(f"{self.role}: Lütfen sayısal bir değer gir.")

    # --- Utility methods -------------------------------------------------

    def _record_event(self, sender: str, receiver: str, pkt: Packet, kind: str):
        now = time.time()
        self.timeline.append(
            TimelineEvent(
                timestamp=now,
                sender=sender,
                receiver=receiver,
                seq=pkt.seq,
                ack=pkt.ack,
                rwnd=pkt.rwnd,
                length=pkt.length,
                kind=kind,
            )
        )

    # --- Packet sending helpers -----------------------------------------

    def _send_packet(self, pkt: Packet):
        # Track whether our last outgoing packet is structurally invalid (for scoring ERROR / undetected invalid).
        self._last_outgoing_was_invalid, self._last_outgoing_invalid_reason = self._classify_outgoing_validity(pkt)
        self.logger.info(f"Sending: {pkt}")
        self.conn.send_json({"packet": pkt.to_json()})
        self.last_activity_time = time.time()
        self._last_outgoing_packet = pkt
        self._last_outgoing_intentionally_invalid = bool(
            pkt.comment and pkt.comment.startswith("INTENTIONAL_INVALID")
        )

        # Timeline için tür belirleme
        if pkt.type == "DATA":
            # Retransmission modundaysak RETX olarak işaretle
            if getattr(self, "_is_retransmitting", False):
                kind = "RETX"
            else:
                kind = "DATA"
        elif pkt.type == "ACK":
            kind = "ACK"
        elif pkt.type == "ERROR":
            kind = "ERROR"
        else:
            kind = pkt.type  # ekstra tip olursa diye

        # DATA / ACK / ERROR / RETX ne ise o
        self._record_event(self.role, "Peer", pkt, kind)

        # rwnd=0 advertise ettiysek, zero_window timer'ı tut
        if pkt.rwnd == 0:
            if self.zero_window_since is None:
                self.zero_window_since = time.time()
        else:
            self.zero_window_since = None

        # Son gönderilen seq'i takip et (doğrulama için)
        if pkt.seq is not None:
            end_seq = pkt.seq + (pkt.length or 0)
            if end_seq > self.last_sent_seq:
                self.last_sent_seq = end_seq

        # Gönderdiğimiz DATA segmentini unacked buffer'a ekle
        if pkt.type == "DATA" and pkt.seq is not None and (pkt.length or 0) > 0:
            # Retransmission olsa bile aynı anahtar varsa overwrite etmeyelim
            self._unacked_segments.setdefault(pkt.seq, pkt.length or 0)

    def _classify_outgoing_validity(self, pkt: Packet) -> tuple[bool | None, str | None]:
        """
        Outgoing paketin 'yapısal' olarak geçerli olup olmadığını belirler.
        Bu kontrol, peer'in tüm mantıksal validasyonunu birebir kopyalamaz; ama
        kesin invalid durumları (rwnd aralığı, length>rwnd, negatif değerler vb.) yakalar.
        """
        if pkt.type == "ERROR":
            return None, None

        if pkt.type not in {"DATA", "ACK"}:
            return True, "Unknown type"

        if pkt.seq is None or pkt.seq < 0:
            return True, "Invalid seq"

        if pkt.ack is None or pkt.ack < 0:
            return True, "Invalid ack"

        if pkt.rwnd is None or pkt.rwnd < 0 or pkt.rwnd > MAX_RWND:
            return True, "Invalid rwnd"

        if pkt.length is None or pkt.length < 0:
            return True, "Invalid length"

        if pkt.length > pkt.rwnd:
            return True, "length > rwnd"

        if pkt.type == "ACK" and pkt.length != 0:
            return True, "ACK length must be 0"

        return False, None

    def _receive_packet(self) -> Packet:
        raw = self.conn.recv_json(timeout=RESPONSE_TIMEOUT_SECONDS)
        pkt = Packet.from_json(raw["packet"])
        self.logger.info(f"Received: {pkt}")
        self._record_event("Peer", self.role, pkt, pkt.type)
        self.last_activity_time = time.time()
        return pkt

    # --- Game turn logic -------------------------------------------------

    # -------------------------------------------------------------- #
    # İnsan girdisi (CLI) için yardımcı fonksiyon
    # -------------------------------------------------------------- #

    def _ask_segment_length_cli(self) -> int:
        """
        Terminalden kullanıcıya sorarak segment uzunluğu alır.
        (GUI kullanılmadığı durumlarda devreye girer.)
        """
        while True:
            try:
                size_str = input(
                    f"{self.role}: Göndereceğin DATA uzunluğu "
                    f"({MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE}, 0 = sadece ACK): "
                ).strip()

                if size_str == "":
                    # Boş enter: default 1 byte
                    length = 1
                else:
                    length = int(size_str)

            except ValueError:
                print(f"{self.role}: Lütfen sayısal bir değer gir (örn. 1, 2, 3...).")
                continue

            if length < 0:
                print(f"{self.role}: Negatif uzunluk olamaz.")
                continue

            if length != 0 and (length < MIN_SEGMENT_SIZE or length > MAX_SEGMENT_SIZE):
                print(
                    f"{self.role}: Uzunluk {MIN_SEGMENT_SIZE}-{MAX_SEGMENT_SIZE} "
                    f"arasında olmalı."
                )
                continue

            return length



    # --- Packet creation (user controlled) ---------------------------

    def _create_next_data_packet(self) -> Packet:
        """
        Bir SONRAKİ DATA segmentini insan kontrollü olarak oluşturur.
        - Eğer length_provider atanmışsa (GUI): ondan bir int bekler.
        - Aksi halde: terminalden input() ile uzunluk sorar.
        """

        # 1) Uzunluğu al (GUI veya CLI)
        if self.length_provider is not None:
            # GUI tarafı bir int döndürmeli (0 = sadece ACK)
            length = self.length_provider()
            self.logger.info(f"GUI selected length={length}")
        else:
            # Eski terminal davranışı
            length = self._ask_segment_length_cli()

        # 2) 0 → sadece ACK gönder (DATA yok)
        if length == 0:
            ack = self.validator.peer.last_seq + self.validator.peer.last_len
            rwnd = self.current_rwnd
            pkt = Packet.make_ack(
                seq=self.gbn.state.next_seq,
                ack=ack,
                rwnd=rwnd,
                comment="User-chosen ACK-only",
            )
            return pkt

        # 3) Güvenlik amaçlı sınırları burada da kontrol edelim (GUI için)
        if length < 0:
            self.logger.warning(
                f"{self.role}: Negatif uzunluk alındı ({length}), 1 byte olarak düzeltiliyor."
            )
            length = 1

        if length < MIN_SEGMENT_SIZE:
            self.logger.warning(
                f"{self.role}: Uzunluk {length} < MIN_SEGMENT_SIZE, {MIN_SEGMENT_SIZE} olarak düzeltiliyor."
            )
            length = MIN_SEGMENT_SIZE
        elif length > MAX_SEGMENT_SIZE:
            self.logger.warning(
                f"{self.role}: Uzunluk {length} > MAX_SEGMENT_SIZE, {MAX_SEGMENT_SIZE} olarak düzeltiliyor."
            )
            length = MAX_SEGMENT_SIZE

        # 4) Go-Back-N penceresinden bir sonraki DATA segmentini al
        seq, real_length = self.gbn.next_data_segment(length=length)

        # Basitlik için: ack = peer'den son in-order byte
        ack = self.validator.peer.last_seq + self.validator.peer.last_len
        rwnd = self.current_rwnd
        return Packet.data(seq=seq, ack=ack, rwnd=rwnd, length=real_length)


    # ------------------------------------------------------------------ #
    # Incoming packet handling
    # ------------------------------------------------------------------ #

    def _respond_to_incoming(self, pkt: Packet, decision: bool | None = None) -> bool:
        """
        Peer'den bir paket geldiğinde nasıl davranacağımız:
        - ERROR      -> sadece logla ve küçük bir ACK gönder (oyun akışı için)
        - ACK        -> SADECE Go-Back-N penceresini güncelle, CEVAP GÖNDERME (False döner)
        - DATA       -> validate et, geçerliyse buffer'a yaz + ACK gönder;
                        geçersizse ERROR gönder + puan.
        """
        # Eğer önceki paketimiz kesin invalid idi ve peer ERROR göndermediyse,
        # peer hatayı kaçırdı → biz +1 alırız.
        if self._last_outgoing_was_invalid is True and pkt.type != "ERROR":
            self.scoreboard.my_reward(1)
            self._last_outgoing_was_invalid = None
            self._last_outgoing_invalid_reason = None

        # 1) Peer bize ERROR gönderdiyse
        if pkt.type == "ERROR":
            self.logger.warning("Peer reported ERROR for our packet.")
            # Scoring:
            # - Son gönderdiğimiz paket kesin invalid ise: peer doğru yakaladı → peer +1 (bizde opponent_score++)
            # - Değilse: peer haksız ERROR gönderdi → biz +1
            if self._last_outgoing_was_invalid is True:
                self.scoreboard.opponent_reward(1)
            else:
                self.scoreboard.my_reward(1)
            self._last_outgoing_was_invalid = None
            self._last_outgoing_invalid_reason = None
            # Küçük bir ACK gönderip oyunu devam ettiriyoruz
            ack_pkt = Packet.make_ack(
                seq=self.validator.peer.expected_seq,
                ack=self.gbn.state.base,
                rwnd=self.current_rwnd,
                comment="After ERROR, continue",
            )
            self._send_packet(ack_pkt)
            # Burada GBN state'ini güncellemiyoruz; bu ACK'i biz gönderiyoruz.
            return True

        # 2) Peer bize ACK gönderdiyse -> SADECE pencereyi güncelle, cevap gönderme!
        if pkt.type == "ACK":
            # Kullanıcı REJECT seçerse ERROR yollayabilir (pretend).
            if decision is False:
                ok, reason, _ = self.validator.peek_validate(pkt, last_sent_seq=self.last_sent_seq)
                if ok:
                    self.scoreboard.opponent_reward(1)
                else:
                    self.scoreboard.my_reward(1)
                err = Packet.error(comment=f"User rejected ACK: {reason}")
                self._send_packet(err)
                return True

            self.logger.info("Received pure ACK from peer.")
            ok, reason, _ = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
            if not ok:
                # ACK invalid ama kabul ettik -> peer +1 (missed detection)
                if decision is True:
                    self.scoreboard.opponent_reward(1)
                return False

            if pkt.ack is not None:
                window_advanced, retransmit_required = self.gbn.on_ack(pkt.ack)

                # ACK geldikçe unacked buffer'ı temizle
                if window_advanced:
                    self._drop_acked_segments(pkt.ack)

                if window_advanced:
                    self.logger.info(
                        f"Go-Back-N window advanced: base={self.gbn.state.base}, "
                        f"next_seq={self.gbn.state.next_seq}"
                    )

                if retransmit_required:
                    self.logger.warning(
                        f"Go-Back-N triggered (duplicate ACK). "
                        f"Next DATA will be retransmitted from seq={self.gbn.state.base}"
                    )
                    self._prepare_retransmission()
            return False

        # Eğer biz rwnd=0 advertise etmiş durumdaysak ve karşı taraf DATA gönderiyorsa → o kaybeder
        if self.current_rwnd == 0 and pkt.type == "DATA":
            self.logger.warning(
                "Peer, rwnd=0 durumundayken DATA gönderdi → kural ihlali, peer puan kaybeder."
            )
            # Doküman: bu durumda gönderen taraf puan kaybeder (biz +1 almayız)
            self.scoreboard.opponent_penalty(1)
            err = Packet.error(comment="DATA sent while advertised rwnd=0")
            self._send_packet(err)
            return True

        # 3) Geriye sadece DATA kalıyor, onu validate edeceğiz
        expected_before = self.validator.peer.expected_seq
        # Kullanıcı REJECT seçerse ERROR yolla; doğru/yanlış yakalamaya göre skorla.
        if decision is False:
            ok, reason, _ = self.validator.peek_validate(pkt, last_sent_seq=self.last_sent_seq)
            if ok:
                self.scoreboard.opponent_reward(1)
            else:
                self.scoreboard.my_reward(1)
            err = Packet.error(comment=f"User rejected DATA: {reason}")
            self._send_packet(err)
            return True

        is_valid, reason, classification = self.validator.validate(pkt, last_sent_seq=self.last_sent_seq)
        self.logger.info(f"Validation result: valid={is_valid}, reason={reason}, class={classification}")

        if not is_valid:
            # Eğer kullanıcı kabul ettiyse (decision True / None) ve yine de invalid ise:
            # peer hatasını yakalayamadık → peer +1, ama oyun devam etsin diye dup ACK gönder.
            self.scoreboard.opponent_reward(1)
            ack_pkt = Packet.make_ack(
                seq=self.validator.peer.expected_seq,
                ack=expected_before,
                rwnd=self.current_rwnd,
                comment=f"Accepted invalid DATA -> dup ACK ({reason})",
            )
            self._send_packet(ack_pkt)
            return True

        # ⬇️ Buradan sonrası SADECE GEÇERLİ DATA için ⬇️
        # Go-Back-N receiver davranışı:
        #   - IN_ORDER    -> buffer'a al, expected_seq ilerlet, ACK=expected_seq
        #   - OLD/OUT_OOO -> discard, ACK=expected_before (dup ACK)
        if classification == "IN_ORDER":
            data_len = pkt.length or 0
            self.recv_buffer_used += data_len
            if self.recv_buffer_used > MAX_RWND:
                self.recv_buffer_used = MAX_RWND  # taşmayı engelle

            self.current_rwnd = MAX_RWND - self.recv_buffer_used

            # window full mü?
            if self.current_rwnd == 0:
                if self.window_full_since is None:
                    self.window_full_since = time.time()
            else:
                self.window_full_since = None  # boşaldıysa resetle

            ack_num = self.validator.peer.expected_seq
            comment = "Normal ACK (in-order)"
        else:
            # OLD veya OUT_OF_ORDER segment -> buffer'a alma, dup ACK gönder
            ack_num = expected_before
            comment = f"Dup ACK ({classification})"

        ack_pkt = Packet.make_ack(
            seq=self.validator.peer.expected_seq,
            ack=ack_num,
            rwnd=self.current_rwnd,
            comment=comment,
        )
        self._send_packet(ack_pkt)
        # DİKKAT: Burada GBN state'imizi güncellemiyoruz;
        # bizim GBN sadece ALDIĞIMIZ ACK'lerle güncellenmeli.
        return True

    # --- Go-Back-N helpers ----------------------------------------------

    def _drop_acked_segments(self, ack_num: int) -> None:
        """
        ACK numarasına göre artık onaylanmış segmentleri buffer'dan çıkar.
        ack_num: peer'in en son in-order aldığı byte index (exclusive).
        """
        while self._unacked_segments:
            first_seq, first_len = next(iter(self._unacked_segments.items()))
            if first_seq + first_len <= ack_num:
                self._unacked_segments.popitem(last=False)
            else:
                break

        # Retransmit kuyruğu varsa, artık ACK'lenmişleri çıkar
        if self._retransmit_queue:
            base = self.gbn.state.base
            self._retransmit_queue = [
                (seq, ln) for (seq, ln) in self._retransmit_queue if seq + ln > base
            ]
            if not self._retransmit_queue:
                self._is_retransmitting = False

    def _prepare_retransmission(self) -> None:
        """
        Fast retransmit tetiklendiğinde base'ten itibaren unacked segmentleri sıraya koy.
        """
        base = self.gbn.state.base
        items = [(seq, ln) for (seq, ln) in self._unacked_segments.items() if seq >= base]
        items.sort(key=lambda x: x[0])
        self._retransmit_queue = items
        self._is_retransmitting = bool(items)
        # GBN'in next_seq'ini base'e çek (retransmit için)
        self.gbn.state.next_seq = base

    def _create_next_retransmit_packet(self) -> Packet:
        """
        Retransmission sırasında sıradaki segmenti (DATA) üret.
        Kullanıcı girdisi almaz; daha önce gönderilmiş segmentlerin aynı uzunluklarıyla tekrar gönderir.
        """
        # Kuyruk boşsa fallback
        if not self._retransmit_queue:
            self._is_retransmitting = False
            return self._create_next_data_packet()

        # ACK ile base ilerlediyse artık geçersiz olanları düşür
        base = self.gbn.state.base
        while self._retransmit_queue and (self._retransmit_queue[0][0] + self._retransmit_queue[0][1] <= base):
            self._retransmit_queue.pop(0)

        if not self._retransmit_queue:
            self._is_retransmitting = False
            return self._create_next_data_packet()

        expected_seq, length = self._retransmit_queue.pop(0)
        seq, real_length = self.gbn.next_data_segment(length=length)
        if seq != expected_seq:
            # GBN state drift ettiyse hizala
            self.logger.warning(
                f"Retransmit seq mismatch (expected {expected_seq}, got {seq}); aligning to expected."
            )
            seq = expected_seq

        ack = self.validator.peer.last_seq + self.validator.peer.last_len
        rwnd = self.current_rwnd

        if not self._retransmit_queue:
            self._is_retransmitting = False

        return Packet.data(
            seq=seq,
            ack=ack,
            rwnd=rwnd,
            length=real_length,
            comment="Fast retransmit (GBN)",
        )

    # --- Public game loop -----------------------------------------------

    def run(self):
        self.logger.info("Game starting...")
        start_time = time.time()
        my_turn_to_send = self.starts_first

        try:
            while True:
                now = time.time()

                # Her BUFFER_DRAIN_INTERVAL_SECONDS saniyede bir uygulama tarafı buffer'dan veri işlesin
                if now - self.last_window_update >= BUFFER_DRAIN_INTERVAL_SECONDS:
                    if self.recv_buffer_used > 0:
                        processed = min(RWND_INCREASE_STEP, self.recv_buffer_used)
                        self.recv_buffer_used -= processed
                        if self.recv_buffer_used < 0:
                            self.recv_buffer_used = 0

                        self.current_rwnd = MAX_RWND - self.recv_buffer_used
                        self.logger.info(
                            f"Application processed {processed} bytes from receive buffer. "
                            f"recv_buffer_used={self.recv_buffer_used}, rwnd={self.current_rwnd}"
                        )

                        # pencere boşaldıysa full state sıfırlansın
                        if self.current_rwnd > 0:
                            self.window_full_since = None

                    self.last_window_update = now

                elapsed = now - start_time
                remaining = GAME_DURATION_SECONDS - elapsed

                # SÜRE KONTROLÜ
                if remaining <= 0:
                    self.logger.info(
                        f"Remaining time: 0s | {self.scoreboard.snapshot()}"
                    )
                    break

                # Receive window WINDOW_STALL_TIMEOUT_SECONDS boyunca full kaldıysa
                if self.window_full_since is not None:
                    if now - self.window_full_since >= WINDOW_STALL_TIMEOUT_SECONDS:
                        self.logger.warning(
                            f"Receive window {WINDOW_STALL_TIMEOUT_SECONDS} saniye boyunca full kaldı → biz suçluyuz, rakibe puan."
                        )
                        # Bizim hatamız → biz puan kaybederiz (oyun bitmesin)
                        self.scoreboard.my_penalty(1)
                        # Yeniden saymaya başla (oyun devam)
                        self.window_full_since = now

                # Buradan sonrası sadece süre > 0 iken çalışır
                self.logger.info(
                    f"Remaining time: {int(remaining)}s | {self.scoreboard.snapshot()}"
                )

                if my_turn_to_send:
                    try:
                        # Retransmission varsa kullanıcı girişi yerine onu gönder
                        if getattr(self, "_retransmit_queue", None):
                            pkt = self._create_next_retransmit_packet()
                        else:
                            pkt = self._create_next_data_packet()
                    except RuntimeError:
                        # Pencere dolu -> sadece ACK gönder
                        ack = self.validator.peer.last_seq + self.validator.peer.last_len
                        pkt = Packet.make_ack(
                            seq=self.gbn.state.next_seq,
                            ack=ack,
                            rwnd=self.current_rwnd,
                            comment="Window full, ACK-only",
                        )
                    self._send_packet(pkt)
                    my_turn_to_send = False

                else:
                    try:
                        pkt = self._receive_packet()
                    except TimeoutError:
                        self.logger.warning("Peer timeout → skor güncellenecek.")
                        # Özel kural: rwnd=0 advertise eden taraf, 45 sn hiç paket yoksa puan kaybeder.
                        if (
                            self.zero_window_since is not None
                            and time.time() - self.zero_window_since >= WINDOW_STALL_TIMEOUT_SECONDS
                        ):
                            self.scoreboard.my_penalty(1)
                            self.zero_window_since = time.time()
                        else:
                            self.scoreboard.opponent_penalty(1)
                        my_turn_to_send = True
                        continue
                    except ConnectionError as e:
                        self.logger.warning(
                            f"Connection closed by peer: {e}. Ending game loop."
                        )
                        break

                    response_sent = self._respond_to_incoming(pkt)
                    # Turn-based:
                    # - ACK alındı ve cevap göndermediysek -> sıra bizde
                    # - Herhangi bir cevap gönderdiysek -> sıra peer'de
                    my_turn_to_send = (pkt.type == "ACK") and (not response_sent)
                    time.sleep(0.2)  # 200ms bekle, terminali rahatlatır

                # Eğer biz rwnd=0 durumunda kaldıysak ve WINDOW_STALL_TIMEOUT_SECONDS hiçbir aktivite yoksa
                if self.zero_window_since is not None:
                    if (
                        now - self.zero_window_since >= WINDOW_STALL_TIMEOUT_SECONDS
                        and now - self.last_activity_time >= WINDOW_STALL_TIMEOUT_SECONDS
                    ):
                        self.logger.warning(
                            f"{WINDOW_STALL_TIMEOUT_SECONDS} saniye boyunca rwnd=0 kaldık ve hiç paket alışverişi olmadı → biz puan kaybediyoruz."
                        )
                        # rwnd=0 advertise eden taraf biziz → biz puan kaybederiz (oyun bitmesin)
                        self.scoreboard.my_penalty(1)
                        # Yeniden saymaya başla
                        self.zero_window_since = now

        except (TimeoutError, ConnectionError) as e:
            self.logger.warning(f"Game ended due to connection problem: {e}")
        finally:
            self.logger.info("Game duration finished.")
            self._plot_timeline()
            self.conn.close()
            self.logger.info(f"Final score: {self.scoreboard.snapshot()}")

    def _plot_timeline(self):
        """
        Toplanan timeline eventlerini kullanarak grafik oluşturur.
        utils.timeline_plot.plot_timeline fonksiyonunu çağırır.
        """
        if not self.timeline:
            self.logger.info("Timeline boş, çizilecek event yok.")
            return

        try:
            plot_timeline(self.timeline, TIMELINE_PLOT_FILE)
            self.logger.info(f"Timeline grafiği kaydedildi: {TIMELINE_PLOT_FILE}")
        except Exception as e:
            self.logger.warning(f"Timeline çizimi sırasında hata oluştu: {e}")
