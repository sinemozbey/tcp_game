🚀 TCP GAME – Interactive TCP Transmission Simulator
<p align="center"> <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square" /> <img src="https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square" /> <img src="https://img.shields.io/badge/GUI-Tkinter-green?style=flat-square" /> <img src="https://img.shields.io/badge/Networking-TCP%20Sockets-orange?style=flat-square" /> <img src="https://img.shields.io/badge/Status-Active-success?style=flat-square" /> </p>
📌 Overview

TCP Game is an interactive, high-fidelity TCP transmission simulator built on real Python TCP sockets, designed for education, debugging, and transport-layer visualization.

The platform features:

Two independent TCP endpoints (ClientA & ClientB)

Real socket communication

Manual packet-size control

Go-Back-N ARQ logic

Flow control & receive window modeling

Live packet animation between clients

Dual-panel GUI combining logs, manual controls, and animated packet flow

This tool helps both students and professionals understand TCP behavior at a practical and visual level.

🎯 Use Cases

This system is ideal for:

Networking & Operating Systems courses

TCP/UDP protocol visualization

Developer training & onboarding

Simulation of flow control mechanisms

Go-Back-N ARQ demonstration

Real-time debugging of packet behavior

Research or demo environments

🌟 Key Features
✔ Real TCP Communication

The system does not simulate network behavior — it uses real Python TCP sockets (socket module) to establish a live connection between ClientA and ClientB.

✔ Go-Back-N ARQ Implementation

Includes:

Sliding window control

Duplicate ACK detection

Automatic retransmissions

Dynamic update of base & next sequence number

Window violation detection

✔ Dynamic Flow Control (rwnd)

Models:

Receiver buffer size

Window shrink/expand

Zero-window handling (30-second rule)

Illegal DATA transmission when rwnd=0

✔ Professional Dual-View Interface

Both endpoints appear in one unified GUI, visually representing:

Local logs

Packet input controls

Outgoing & incoming messages

Animated packet flow (DATA → ACK → RETX → ERROR)

Example layout:

ClientA  <------ animated packet flow ------>  ClientB


(You can insert screenshots below.)

✔ Packet Animation Engine

Every packet (DATA, ACK, ERROR, RETX) is animated across the screen:

Starts at sender node

Moves smoothly along the center line

Arrives at receiver

Returns with response if needed

This allows users to see TCP behavior, not just read logs.

✔ Event Timeline Generation

A high-quality PNG timeline is automatically generated:

Each transmission is logged chronologically

Color-coded arrows: DATA, ACK, ERROR, RETX

Timestamped for debugging and reporting



🔧 Installation
1️⃣ Clone the repository
git clone https://github.com/sinemozbey/tcp_game.git
cd tcp_game

2️⃣ Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

3️⃣ Install dependencies
pip install -r requirements.txt

▶️ Running the Simulation
Terminal 1 — Start Client A (server):
python client_A.py

Terminal 2 — Start Client B (client):
python client_B.py


Both will appear in a single GUI window with animation.


🧪 Example Transmission Scenario

ClientA sends DATA(len=3)

Packet animates across the line

ClientB validates, updates rwnd, sends ACK

ACK animates back

If duplicate ACKs occur → retransmission triggered

If rwnd=0 and sender sends DATA → penalty applied

30 seconds without window recovery → timeout logic runs

🚀 Planned Enhancements

Selective Repeat ARQ

Artificial packet loss simulation

Adjustable latency, jitter, and error rate

Web-based interface (React / WebSockets)

PCAP export for Wireshark analysis

📜 License

This project is licensed under the MIT License, providing full freedom for modification, use, and distribution.
