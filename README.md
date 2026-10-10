# GestureTransfer 🤏📁

**Transfer files between your laptop and Android phone with hand gestures.**

By **Muhammad Bilal**

Inspired by "AI teleportation"-style interactions: make a **fist** to select a
file, **point** to send it to the other device, **open palm** to cancel. The
phone opens its camera with an accept zone — hold an open palm over it to
download incoming files. No taps needed on the laptop.

## ✨ Features

- **Gesture control** — fist = select, pointing = transfer, open palm = cancel
- **Laptop ↔ Android** — Flask server on the laptop, web UI on the phone
- **QR-code connect** — scan to pair the phone instantly
- **Live updates** — Server-Sent Events push transfer progress in real time
- **Offer system** — incoming transfers wait in `pending_offers/` for accept/decline
- **Transfer history** — dashboard of everything sent and received
- **Offline hand tracking** — MediaPipe models bundled in `static/hands/`, served
  by the laptop, so the phone needs no internet

## 🛠 Tech stack

- **Backend:** Python, Flask, Flask-CORS, Server-Sent Events
- **Vision:** MediaPipe Hands + OpenCV (Python)
- **Frontend:** HTML/CSS/JS (laptop dashboard + mobile receiver page)
- **Extras:** QR codes (`qrcode`), threaded gesture loop

## 🚀 Run it

```bash
pip install -r requirements.txt
python gesture_server.py
```

Then:

1. Run `python test_connection.py` to print your laptop's LAN IP (or check the server log).
2. Open `http://<laptop-ip>:5000` on your phone's browser — or scan the QR code shown by the server.
3. Allow camera access, make a fist over a file card, drag it to the **+** target, release.

See **SETUP_GUIDE.md** for the full gesture reference and troubleshooting.

## 📝 Notes

- `static/hands/` (MediaPipe wasm bundle, ~10 MB) is excluded from git — see
  `static/hands/MODELS_NOTE.md` for where to get it.
- Designed for Windows 11 + Android, but the server runs anywhere Python does.

## 📄 License

MIT — free for personal and commercial use.
