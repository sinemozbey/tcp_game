# core/packet.py

from dataclasses import dataclass, asdict
from typing import Optional
import json


@dataclass
class Packet:
    """
    Generic TCP-like packet for the game.
    type:
        "DATA"  - carries seq, ack, length, rwnd
        "ACK"   - acknowledgment
        "ERROR" - logical error notification (no seq/ack/rwnd/len required)
    """

    type: str
    seq: Optional[int] = None
    ack: Optional[int] = None
    rwnd: Optional[int] = None
    length: Optional[int] = None
    comment: str | None = None  # for debugging / explanation

    # ---- JSON helpers ---------------------------------------------------

    def to_json(self) -> str:
        """Serialize packet to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "Packet":
        """Deserialize packet from JSON string."""
        data = json.loads(raw)
        return cls(**data)

    # ---- Constructors ---------------------------------------------------

    @classmethod
    def data(
        cls,
        seq: int,
        ack: int,
        rwnd: int,
        length: int,
        comment: str = "",
    ) -> "Packet":
        return cls(
            type="DATA",
            seq=seq,
            ack=ack,
            rwnd=rwnd,
            length=length,
            comment=comment,
        )

    @classmethod
    def make_ack(
        cls,
        seq: int,
        ack: int,
        rwnd: int,
        comment: str = "",
    ) -> "Packet":
        # İsmi `ack` olursa dataclass alanını override ediyor, o yüzden make_ack
        return cls(
            type="ACK",
            seq=seq,
            ack=ack,
            rwnd=rwnd,
            length=0,
            comment=comment,
        )

    @classmethod
    def error(cls, comment: str = "") -> "Packet":
        # Spec: ERROR notification without seq, ack, rwnd, length
        return cls(type="ERROR", comment=comment)
