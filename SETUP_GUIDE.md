# 🚀 Gesture Transfer System — Complete Setup Guide (v3)

Version 3 adds a fully **gesture-driven select & drop** experience:
- Laptop: grab a file with a **pinch**, drag it and **drop it on the ➕** in the camera to transfer.
- Mobile: opens its own **camera with a ➕ accept zone** — hold an **open palm** over the ➕ to accept the incoming file.
- Both sides work like Huawei's "AI Teleportation": grab → drag → drop, no taps needed.

---

## 📋 Overview

```
        (Sender) LAPTOP camera                      (Receiver) MOBILE camera
   ┌─────────────────────────────┐            ┌─────────────────────────────┐
   │  👊 Fist (hold) = add files │            │   Camera shows ➕ zone       │
   │  🤏 Pinch = grab a file card│   HTTP     │   File arrives → appears    │
   │  👉 Drag to ➕ = drop/send   │ ─────────► │   🖐 OPEN PALM on ➕ = accept│
   │  🖐 Palm(hold) = clear tray │  sockets   │   ⬇ auto-downloads to phone │
   └─────────────────────────────┘            └─────────────────────────────┘
```

| Direction | How it works |
|-----------|--------------|
| Laptop → Mobile | Drag file to ➕ → offer sent → phone accepts via palm gesture (or tap) → auto-download |
| Laptop → Laptop | Drag file to ➕ → direct socket transfer → saved in `received_files/` |
| Mobile → Laptop | File tab → choose file → upload (tap-based) → saved in `received_files/` |

---

## 🖥️ System Requirements

### Laptop (Sender/Gesture host)
- **OS**: Windows 10/11, macOS, or Linux
- **Python**: 3.8+
- **Webcam**: Built-in or external
- **Network**: WiFi (all devices on the **same network**)

### Mobile (Android/iOS)
- **Browser**: Chrome / Firefox / Safari / Edge (Chrome recommended)
- **Camera permission** enabled (needed for gesture accept)
- **Network**: Same WiFi as laptop

---

## 📦 Installation

### 1. Install Python Dependencies
```bash
pip install opencv-python mediapipe numpy flask flask-cors qrcode[pil]
```

### 2. Verify Installation
```bash
python -c "import cv2, mediapipe, flask, qrcode; print('OK - all packages installed')"
```

### 3. Project Files
Your project folder must contain:

| File / Folder | Purpose |
|---------------|---------|
| `gesture_server.py` | Main server (web + camera gestures + transfers) |
| `mobile_receiver.html` | Mobile interface (camera gesture accept + files + send) |
| `static/hands/` | Local MediaPipe hand-tracking model (works offline, ~11 MB) |
| `received_files/` | Files received FROM other devices |
| `shared_files/` | Files staged for mobile download / accepted offers |
| `pending_offers/` | Offers waiting for the receiver's gesture accept |
| `test_connection.py` | IP detection utility |

> **Offline note:** the mobile hand-tracking model is served by your laptop from
> `static/hands/`, so the phone does **not** need internet after connecting.

---

## 🔧 Configuration

### Windows Firewall (Run as Administrator)
```powershell
netsh advfirewall firewall add rule name="GestureTransfer" dir=in action=allow protocol=TCP localport=5000-5010
```

### Find Your Laptop IP
```bash
python test_connection.py
```
Or manually: `ipconfig` → look for **IPv4 Address** (e.g., `192.168.1.100`).

---

## 🚀 Running the System

### On Laptop (Sender / Receiver)
```bash
cd path/to/GestureTransfer
python gesture_server.py
```

**What happens:**
1. 📡 Socket server starts on port **5002** (laptop-to-laptop)
2. 🌐 Web server starts on port **5000**
3. 📷 Camera window opens with the gesture tray
4. 📄 Dashboard opens in browser: `http://localhost:5000`

> **Useful flags (env vars):**
> - `GT_NO_CAMERA=1` → skip the camera (web/transfer features only)
> - `GT_NO_BROWSER=1` → don't auto-open the dashboard browser

### On Mobile (Receiver)
1. Open the mobile page from the **QR code** on the laptop dashboard
   — or manually, in Chrome: `http://[LAPTOP_IP]:5000/mobile`
   (e.g. `http://192.168.1.100:5000/mobile`)
2. Tap **Connect** — the status badge turns **ONLINE**
3. Tap **Start Camera** (your browser will ask for camera permission → allow)

### On Second Laptop (Optional)
Run the same command on the other laptop. Both laptops auto-register with each other.

---

## 🎮 Laptop Gesture Controls (Camera)

The camera window shows:
- **Top row** — connected device chips (`PH` = phone, `PC` = laptop)
- **Left column** — your selected file cards (the "tray")
- **Right side** — the big **➕ DROP ZONE**

| Gesture | Action |
|---------|--------|
| 👊 **Fist (hold ~1s)** | Opens file picker → adds selected file(s) to the tray |
| 🤏 **Pinch on a card** | Grabs / selects that file (your fingertip is the cursor) |
| 👉 **Drag the grabbed card to ➕** and release | Drops it → gesture transfer starts |
| ☝ **Point at a device chip** | Chooses the receiver device (✓ shows selection) |
| 🖐 **Open palm (hold)** | Clears the whole tray |
| `q` key | Close camera window (server keeps running) |
| `d` key | Open file picker (keyboard shortcut) |
| `c` key | Clear tray (keyboard shortcut) |

> Tip: release the pinch **inside the green ➕** to send. When a phone is targeted,
> the offer waits for the phone's palm gesture to accept it.

---

## 📱 Mobile Camera Gesture Controls (Receiving)

The **Camera Gesture** tab shows your camera with a **➕ ACCEPT ZONE** overlay.

1. Laptop drops a file to you → a yellow **“Incoming”** banner appears and the ➕ turns green
2. Hold an **OPEN PALM over the ➕** for ~1 second
3. The file downloads automatically — progress bar shown, file saved to the phone
4. Or use the **Accept / Decline** buttons as a fallback

Other tabs:
- **Files** — download any file the laptop has shared (accepted offers appear here too)
- **Send** — tap to pick files on the phone and upload them to the laptop

---

## 🧠 How It Works (Offers & Gesture Accept)

1. Laptop drops a file on ➕ → server stages it in `pending_offers/` and creates an **offer**
   targeted at that device (`POST /offers`, pushed live via **SSE `/events`**).
2. The phone sees the offer and shows it above its ➕ zone.
3. Phone's hand tracking detects an **open palm over the ➕** → `POST /accept_offer/<id>`.
4. Server copies the file to `shared_files/`, the phone downloads it automatically, and the
   transfer is logged in the **history** (see dashboard).

**All new API endpoints:**
```
GET  /offers             list offers for a device ( ?id=<deviceId> )
POST /accept_offer/<id>  accept an incoming offer
POST /decline_offer/<id> decline an incoming offer
GET  /events             SSE live stream (offers, devices, file events)
GET  /status             full dashboard JSON (devices, offers, history)
GET  /history            transfer history page
GET  /qr_mobile          QR-code PNG pointing to the mobile page
GET  /static/<path>      serves local MediaPipe model (static/hands/)
```

---

## 🌐 Network Requirements

- **Same WiFi network** (or Ethernet on the same subnet)
- **No AP/Client isolation** (router setting)
- **Ports 5000, 5001, 5002** open
- **IPv4** connectivity

---

## 🔍 Troubleshooting

| Problem | Solution |
|---------|----------|
| Camera window shows no tray | Make a **fist** (or press `d`) to add files |
| Can't grab a card | Ensure your index fingertip touches the card, then pinch thumb+index together |
| Drop doesn't send | Release the pinch **inside** the green ➕; check a device is ONLINE |
| Phone gets offer but no download | Phone camera must see an **open palm** over ➕, or tap **Accept** |
| Phone camera black / denied | Allow camera permission, or use the Accept button fallback |
| "Failed to fetch" | Wrong IP / firewall / not on same WiFi |
| Mobile shows OFFLINE | Restart server, verify IP, check firewall |
| Hand tracking not loading | Check `http://[IP]:5000/static/hands/hands.js` opens (offline model) |
| QR code missing | Install: `pip install qrcode[pil]` |

**MediaPipe import error:**
```bash
pip uninstall mediapipe -y
pip install mediapipe==0.10.8
```

**Port already in use:**
```bash
netstat -ano | findstr :5000
taskkill /PID <PID> /F
```

**IP changed after restart:** run `python test_connection.py` and update the phone
(scan the new QR code, or open `http://NEW_IP:5000/mobile`).

---

## 📊 Testing Checklist

- [ ] `python gesture_server.py` starts, dashboard opens
- [ ] Camera window shows tray + ➕ drop zone
- [ ] Mobile connects → ON LINE, camera gesture tab opens
- [ ] Fist adds a file card to the tray
- [ ] Pinch grabs the card; dragging to ➕ starts a transfer
- [ ] Phone shows "Incoming" and palm-over-➕ auto-downloads it
- [ ] Laptop-to-laptop transfer saved in `received_files/`
- [ ] Mobile Send tab uploads a file to the laptop
- [ ] Dashboard shows devices, offers, and history

---

## 🎯 Roadmap

- [ ] Mobile-to-mobile transfers via gesture
- [ ] Transfer encryption
- [ ] Native Android/iOS app
- [ ] Multi-receiver simultaneous drop
- [ ] WiFi-Direct / offline hotspot mode

---

**Version**: 3.0 — Gesture select + drop on ➕, camera gesture accept on mobile  
**Last Updated**: September 2026  
**Compatible**: Python 3.8+, Windows/Mac/Linux, Android/iOS browsers