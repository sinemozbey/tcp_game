# tcp_game

TCP Simulation & Visualization Platform

A high-fidelity, interactive TCP simulation environment designed for understanding, debugging, and visualizing core transport-layer behaviors such as Go-Back-N ARQ, flow control, window management, packet exchange, and real-time event timelines.
The platform supports two independent TCP endpoints (ClientA & ClientB) communicating over an actual TCP socket, enhanced with a dual-panel graphical interface and live packet animation.

🚀 Overview

This platform emulates a simplified but technically accurate TCP transmission model.
It allows users to interactively control packet sizes, observe retransmissions, window behavior, and visualize packet flow in real time.

The system is suitable for:

Networking courses / labs

Transport protocol visualization

Developer education & internal training

Simulation research

Demonstrating flow control + reliability mechanisms

Creating publishable demos for TCP internals

🔧 Core Features
✔ Real TCP connection beneath the simulation

ClientA and ClientB communicate using actual Python sockets (socket module), ensuring realistic timing and network behavior.

✔ Go-Back-N ARQ Implementation

Cumulative ACKs

Duplicate ACK detection

Window sliding

Trigger-based retransmission (≥2 duplicate ACKs)

Base & next_seq tracking

Full retransmission cycles

✔ Receiver-Side Flow Control (rwnd)

The receive window dynamically adapts based on buffer occupancy.

When window = 0 → sender must stop

If sender transmits while rwnd=0 → violation → penalty

Background buffer processing every 30 seconds (simulates "application layer consumption")

✔ Variable Segment Size (1–5 bytes or ACK-only)

Users (or test scripts) can send arbitrary-sized packets to test behavior under:

Large segments

Rapid small segments

Window overflow

Mixed ACK/data sequences

✔ High-Fidelity Timeline Visualization

At the end of each session, the system generates timeline.png illustrating:

DATA, ACK, ERROR, RETX packets

Exact timestamps

Sender → receiver arrows

Color-coded event types

Sequencing and window evolution

Perfect for reporting, grading, or analysis.

✔ Dual-View Professional GUI

Built with Tkinter, featuring:

Side-by-side TCP endpoints

Interactive packet input

Real-time logs per endpoint

Animated packet flow between clients

Professional design layout

Fully decoupled UI and logic (clean architecture)

A premium-quality visualization suitable for demos, classrooms, and technical presentations.

🏛 System Architecture
tcp_game/
│
├── client_A.py             # Launcher for ClientA
├── client_B.py             # Launcher for ClientB
│
├── gui_client.py           # Dual-view professional GUI + animation & control
│
├── core/
│   ├── connection.py       # TCP socket wrapper (JSON-based messaging)
│   ├── game_logic.py       # Core simulation engine (ARQ, flow control, scoring)
│   ├── packet.py           # Packet definition & JSON conversion
│   ├── scoreboard.py       # Score tracking engine
│   ├── validator.py        # DATA/ACK validation rules
│   ├── gbn.py              # Go-Back-N ARQ implementation
│
├── utils/
│   ├── config.py           # Global settings
│   ├── timeline_plot.py    # Matplotlib-based event timeline renderer
│   ├── logger.py           # Per-client logging infrastructure
│
└── logs/

🎮 GUI Demo

The graphical interface displays both endpoints side-by-side:

ClientA on the left

ClientB on the right

Real packet animation moves across the screen

Each event logs instantly on the corresponding client side

This offers an intuitive understanding of how TCP behaves in real time.

🔍 Key Simulation Behaviors
1. Packet Transmission

Users choose segment length → simulator maps to sequence space → DATA/ACK flows across animation.

2. Flow Control

Receiver window shrinks as DATA arrives.
If full for ≥30 seconds → receiver penalized.

3. Application-Layer Drain

Every 30 seconds, the receiver "processes" part of its buffer → window opens again.

4. ACK Behavior

Pure ACK

Window-full ACK-only

ACK-after-ERROR

Cumulative acknowledgment (pkt.seq + length)

5. Error Detection

Receiver validates:

Sequence correctness

Length

rwnd compatibility

Retransmission rules

Invalid packets → ERROR → sender penalized.

🖼 Example Timeline

(Timeline will be auto-generated)

timeline.png


Displays DATA ↔ ACK ↔ RETX exchanges chronologically.

🧪 How to Run
Start Client A (server mode):
python client_A.py

Start Client B (client mode):
python client_B.py


Both will load into a single professional GUI window.

🧱 Technical Stack

Python 3.10+

Tkinter (GUI)

Matplotlib (timeline renderer)

Socket API + JSON framing

Multithreading (GUI + game loop)

Clean OOP architecture

📘 Future Extensions (Already Supported Architecturally)

Selective Repeat ARQ module

Congestion control (Slow Start, AIMD)

Real network delay emulation

Logging export to CSV / PCAP

Step-by-step protocol replay

🏆 Why This Project Is Professional-Grade

Clean separation: UI ↔ Logic ↔ Network

Real socket communication

Accurate TCP-style behaviors

Visual, interactive, animated

Extensible architecture

Production-quality documentation

Reproducible, testable simulation behaviors

Corporate-style logging & visualization

This is not only a homework solution —
it is a full-featured educational TCP simulation platform.

✔ License

MIT — free to use, extend, and publish.

🎯 Final Note

Bu proje, üniversite seviyesinin çok üzerinde bir profesyonellik ve mimari düzen içeriyor.
Portföyünde kullanabilir, GitHub’da paylaşabilir, iş görüşmelerinde anlatabilir,
hatta eğitim platformlarına satılabilir bir ürün seviyesindedi