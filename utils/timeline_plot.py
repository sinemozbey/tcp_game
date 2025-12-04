# utils/timeline_plot.py

import matplotlib.pyplot as plt
from dataclasses import dataclass
from typing import List


@dataclass
class TimelineEvent:
    timestamp: float
    sender: str
    receiver: str
    seq: int | None
    ack: int | None
    rwnd: int | None
    length: int | None
    kind: str  # "DATA", "ACK", "ERROR", "RETX"


def plot_timeline(events: List[TimelineEvent], output_file: str = "timeline.png"):
    """
    Simple TCP-like time diagram:
    X-axis: endpoints, Y-axis: time
    """

    if not events:
        print("No events to plot.")
        return

    # Map endpoints to x positions
    endpoints = sorted(
        {e.sender for e in events} | {e.receiver for e in events}
    )
    x_positions = {name: i for i, name in enumerate(endpoints)}

    times = [e.timestamp for e in events]
    t0 = min(times)

    plt.figure(figsize=(6, 8))

    # Vertical lines for endpoints
    for name, x in x_positions.items():
        plt.plot([x, x], [0, max(times) - t0 + 1], linestyle="--")
        plt.text(x, max(times) - t0 + 1.2, name, ha="center")

    for e in events:
        y = e.timestamp - t0 + 0.5
        x_start = x_positions[e.sender]
        x_end = x_positions[e.receiver]

        plt.annotate(
            "",
            xy=(x_end, y),
            xytext=(x_start, y),
            arrowprops=dict(arrowstyle="->"),
        )

        label = f"{e.kind}"
        if e.seq is not None:
            label += f" s={e.seq}"
        if e.ack is not None:
            label += f" a={e.ack}"
        if e.rwnd is not None:
            label += f" w={e.rwnd}"
        if e.length is not None:
            label += f" len={e.length}"

        plt.text((x_start + x_end) / 2, y + 0.1, label, fontsize=7, ha="center")

    plt.xlabel("Endpoints")
    plt.ylabel("Time")
    plt.title("TCP Game Packet Timeline")
    plt.tight_layout()
    plt.savefig(output_file, dpi=150)
    plt.close()
