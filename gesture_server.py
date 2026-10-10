import cv2
import mediapipe as mp
import numpy as np
import os
import sys
import json
import time
import math
import queue
import shutil
import io
import threading
import socket
import uuid
from flask import Flask, request, jsonify, send_file, Response, stream_with_context, render_template_string, send_from_directory
from flask_cors import CORS
import webbrowser
from datetime import datetime
from pathlib import Path

# Windows console may use cp1252 - widen stdout so emojis do not crash printing
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ============ CONFIGURATION ============
PORT = 5000
BUFFER_SIZE = 4096
HANDSHAKE_PORT = 5001
TRANSFER_PORT = 5002
UPLOAD_FOLDER = "received_files"
SHARED_FOLDER = "shared_files"
PENDING_FOLDER = "pending_offers"
STATIC_FOLDER = "static"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SHARED_FOLDER, exist_ok=True)
os.makedirs(PENDING_FOLDER, exist_ok=True)

# ============ FLASK WEB SERVER ============
app = Flask(__name__)
CORS(app)

# Store connected devices
connected_devices = {}
device_id_counter = 0

# Offer system
offers = {}
offer_counter = 0
transfer_history = []

# SSE clients
sse_clients = []

# Shared lock for cross-thread globals
state_lock = threading.Lock()

# Gesture constants
GRAB_DIST = 0.075
RELEASE_DIST = 0.14
DROP_ZONE_RADIUS = 78
POINT_STABLE_FRAMES = 8
DIALOG_HOLD_FRAMES = 18
PALM_CLEAR_FRAMES = 12

# ============ MEDIAPIPE ============
try:
    mp_hands = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
except AttributeError:
    from mediapipe.python.solutions import hands as mp_hands
    from mediapipe.python.solutions import drawing_utils as mp_drawing

# ============ GESTURE / HAND GEOMETRY HELPERS ============
def lm_dist(lm, a, b):
    p, q = lm[a], lm[b]
    return math.hypot(p.x - q.x, p.y - q.y)

def detect_gesture(landmarks):
    """Returns dict with rich hand state: cursor, pinch, pose, fingers"""
    if not landmarks:
        return {"pose": "none", "cursor": None, "pinch": False, "pinch_dist": 1.0,
                "index_up": False, "middle_up": False, "ring_up": False, "pinky_up": False,
                "thumb_ext": False, "ext_count": 0}

    tip_x, tip_y = landmarks[8].x, landmarks[8].y

    index_up = landmarks[8].y < landmarks[6].y
    middle_up = landmarks[12].y < landmarks[10].y
    ring_up = landmarks[16].y < landmarks[14].y
    pinky_up = landmarks[20].y < landmarks[18].y
    thumb_ext = abs(landmarks[4].x - landmarks[3].x) > 0.05

    ext_count = int(index_up) + int(middle_up) + int(ring_up) + int(pinky_up) + int(thumb_ext)

    pinch_dist = lm_dist(landmarks, 4, 8)
    pinch = pinch_dist < GRAB_DIST

    # Pose categories
    if ext_count >= 5:
        pose = "open_palm"
    elif index_up and not middle_up and not ring_up and not pinky_up and not pinch:
        pose = "pointing"
    elif ext_count <= 1 and not pinch:
        pose = "closed_fist"
    elif pinch:
        pose = "pinch"
    else:
        pose = "relaxed"

    return {
        "pose": pose,
        "cursor": (tip_x, tip_y),
        "pinch": pinch,
        "pinch_dist": pinch_dist,
        "index_up": index_up,
        "middle_up": middle_up,
        "ring_up": ring_up,
        "pinky_up": pinky_up,
        "thumb_ext": thumb_ext,
        "ext_count": ext_count,
    }

def file_color(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'):
        return (76, 175, 80)
    if ext in ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.mp3', '.wav'):
        return (156, 39, 176)
    if ext in ('.pdf', '.doc', '.docx', '.txt', '.md', '.xls', '.xlsx', '.ppt', '.pptx'):
        return (66, 126, 250)
    if ext in ('.zip', '.rar', '.7z', '.tar', '.gz'):
        return (255, 152, 0)
    return (96, 125, 139)

def draw_rounded_rect(img, x1, y1, x2, y2, color, thickness=2, radius=10):
    cv2.rectangle(img, (x1 + radius, y1 + 1), (x2 - radius, y2 - 1), color, thickness)
    cv2.rectangle(img, (x1 + 1, y1 + radius), (x2 - 1, y2 - radius), color, thickness)
    cv2.line(img, (x1 + radius, y1), (x2 - radius, y1), color, thickness)
    cv2.line(img, (x1 + radius, y2), (x2 - radius, y2), color, thickness)
    cv2.line(img, (x1, y1 + radius), (x1, y2 - radius), color, thickness)
    cv2.line(img, (x2, y1 + radius), (x2, y2 - radius), color, thickness)

def truncate_text(text, max_len):
    return text if len(text) <= max_len else text[:max_len - 1] + "…"

# ============ SSE BROADCAST ============
def sse_broadcast(event, data):
    payload = (event, data)
    for q in list(sse_clients):
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass

@app.route('/events')
def events():
    def gen():
        yield "retry: 3000\n\n"
        yield ": connected\n\n"
        q = queue.Queue(maxsize=200)
        sse_clients.append(q)
        try:
            while True:
                try:
                    event, data = q.get(timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            if q in sse_clients:
                sse_clients.remove(q)
    return Response(stream_with_context(gen()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

def log_history(entry):
    entry['ts'] = datetime.now().strftime('%H:%M:%S')
    with state_lock:
        transfer_history.insert(0, entry)
        del transfer_history[100:]

# ============ OFFER SYSTEM ============
def dedupe_name(folder, filename):
    candidate = filename
    name, ext = os.path.splitext(filename)
    i = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{name}_{i}{ext}"
        i += 1
    return candidate

def create_offer(device_info, file_path):
    global offer_counter
    offer_counter += 1
    oid = f"o{int(time.time())}_{offer_counter}"
    filename = os.path.basename(file_path)
    saved_name = dedupe_name(PENDING_FOLDER, filename)
    dest = os.path.join(PENDING_FOLDER, saved_name)
    try:
        shutil.copy2(file_path, dest)
    except Exception as e:
        print(f"[ERROR] Could not stage file for offer: {e}")
        return None
    offers[oid] = {
        "id": oid,
        "filename": filename,
        "size": os.path.getsize(file_path),
        "from_name": "Laptop",
        "target_id": device_info.get('id'),
        "status": "pending",
        "path": dest,
        "created_at": time.time(),
    }
    print(f"\n[OFFER] 📤 {filename} → {device_info.get('name', device_info.get('id', 'device'))} (waiting for gesture-accept)")
    sse_broadcast('offer', {"id": oid, "filename": filename, "target_id": device_info.get('id')})
    log_history({"type": "offer", "file": filename, "size": offers[oid]["size"],
                 "to": device_info.get('name', 'device'), "to_type": device_info.get('type', '?'),
                 "status": "pending"})
    return oid

@app.route('/offers')
def list_offers():
    target_id = request.args.get('id')
    result = []
    for oid, o in list(offers.items()):
        if o['target_id'] == target_id:
            result.append({
                "id": o['id'], "filename": o['filename'], "size": o['size'],
                "from": o['from_name'], "status": o['status'],
                "url": o.get('url', ''),
            })
    return jsonify({"offers": result})

@app.route('/accept_offer/<oid>', methods=['POST'])
def accept_offer(oid):
    o = offers.get(oid)
    if not o:
        return jsonify({"error": "Offer not found"}), 404
    if o['status'] != 'pending':
        return jsonify({"status": o['status'], "url": o.get('url', '')})
    o['status'] = 'accepted'
    o['accepted_at'] = time.time()
    shared_name = dedupe_name(SHARED_FOLDER, o['filename'])
    shared_path = os.path.join(SHARED_FOLDER, shared_name)
    try:
        shutil.copy2(o['path'], shared_path)
    except Exception as e:
        print(f"[ERROR] Accept copy failed: {e}")
    o['shared'] = shared_path
    o['url'] = f"/shared/{shared_name}"
    print(f"\n[OFFER] ✅ {o['filename']} ACCEPTED by {o['target_id']} → ready to download")
    sse_broadcast('offer_status', {"id": oid, "status": "accepted", "url": o['url']})
    for h in transfer_history:
        if h.get('type') == 'offer' and h.get('file') == o['filename'] and h.get('status') == 'pending':
            h['status'] = 'accepted'
    return jsonify({"status": "accepted", "url": o["url"], "filename": o["filename"]})

@app.route('/decline_offer/<oid>', methods=['POST'])
def decline_offer(oid):
    o = offers.get(oid)
    if not o:
        return jsonify({"error": "Offer not found"}), 404
    o['status'] = 'declined'
    try:
        if os.path.exists(o.get('path', '')):
            os.remove(o['path'])
    except Exception as e:
        print(f"[WARN] Cleanup failed: {e}")
    print(f"\n[OFFER] ❌ {o['filename']} declined by {o['target_id']}")
    sse_broadcast('offer_status', {"id": oid, "status": "declined"})
    for h in transfer_history:
        if h.get('type') == 'offer' and h.get('status') == 'pending' and h.get('file') == o['filename']:
            h['status'] = 'declined'
    return jsonify({"status": "declined"})

# ============ GESTURE LISTENER (Sender camera with pinch-grab + "+" drop) ============
def hand_gesture_listener():
    if os.environ.get('GT_NO_CAMERA'):
        print("[CAMERA] Camera skipped (GT_NO_CAMERA set)")
        return
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        print("❌ Camera not found! Please check your webcam.")
        return

    tray = []            # list of {path, name, size, color}
    grabbed = None       # index into tray of grabbed card
    target_id = None     # selected device chip
    fist_frames = 0
    palm_frames = 0
    point_frames = 0
    pointed_chip = None
    cooldown = 0
    last_drop = ""

    print("\n[CAMERA] Camera started! Gesture controls:")
    print("  👊 Fist (hold)      = Open file picker (add files to tray)")
    print("  🤏 Pinch on a card  = Grab / select the file")
    print("  👉 Drag card to ➕   = Drop → gesture transfer starts")
    print("  🖐 Open palm (hold)  = Clear tray")
    print("  ☝ Point at device chip = Choose receiver device")
    print("  Press 'q' to close camera\n")

    with mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.5
    ) as hands:

        while True:
            success, frame = cap.read()
            if not success:
                print("⚠️ Failed to capture frame")
                continue

            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = hands.process(rgb)

            W, H = frame.shape[1], frame.shape[0]
            gesture = {"pose": "none", "cursor": None, "pinch": False}
            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)
                    gesture = detect_gesture(hand_landmarks.landmark)

            cursor = gesture.get("cursor")
            # Convert normalized -> pixel (frame is mirrored already)
            cur_pt = (int(cursor[0] * W), int(cursor[1] * H)) if cursor else None
            pinch = gesture.get("pinch", False)
            pose = gesture.get("pose", "none")

            # ---- Drop zone (+ sign) on the right ----
            dz = (W - 90, H // 2)
            in_zone = cur_pt and math.hypot(cur_pt[0] - dz[0], cur_pt[1] - dz[1]) <= DROP_ZONE_RADIUS

            # ---- Device chips on top ----
            chips = []
            x = 8
            for did, dev in list(connected_devices.items())[:6]:
                chips.append({"id": did, "dev": dev, "rect": (x, 8, 148, 32)})
                x += 154
            chip_rects = [c["rect"] for c in chips]
            in_chip = None
            if cur_pt:
                for c in chips:
                    rx, ry, rw, rh = c["rect"]
                    if rx <= cur_pt[0] <= rx + rw and ry <= cur_pt[1] <= ry + rh:
                        in_chip = c
                        break

            # ---- Card slots (left column) ----
            card_w = 176
            card_h = 46
            slot_x = 12
            slot_y0 = 64
            slot_gap = 54

            # ---- Fist: open file picker (add to tray) ----
            if pose == "closed_fist":
                fist_frames += 1
                palm_frames = 0
                point_frames = 0
                if fist_frames >= DIALOG_HOLD_FRAMES and cooldown == 0:
                    paths = select_files_dialog()
                    if paths:
                        for p in paths:
                            tray.append({
                                "path": p,
                                "name": os.path.basename(p),
                                "size": os.path.getsize(p),
                                "color": file_color(p),
                            })
                        print(f"\n[ADD] 📁 {len(paths)} file(s) added to tray (total {len(tray)})")
                        sse_broadcast('laptop_state', {"event": "tray_updated", "count": len(tray)})
                    fist_frames = 0
                    cooldown = 40
            else:
                fist_frames = 0

            # ---- Open palm: clear tray & target ----
            if pose == "open_palm":
                palm_frames += 1
                if palm_frames >= PALM_CLEAR_FRAMES and cooldown == 0:
                    tray = []
                    grabbed = None
                    target_id = None
                    print("\n[CLEAR] 🖐 Tray cleared")
                    sse_broadcast('laptop_state', {"event": "tray_cleared"})
                    palm_frames = 0
                    cooldown = 25
            else:
                palm_frames = 0

            # ---- Pointing at device chip: select target ----
            if pose == "pointing" and not pinch:
                if in_chip and in_chip["id"] == pointed_chip:
                    point_frames += 1
                else:
                    pointed_chip = in_chip["id"] if in_chip else None
                    point_frames = 0 if in_chip else 0
                if point_frames >= POINT_STABLE_FRAMES and in_chip:
                    target_id = in_chip["id"]
                    print(f"\n[TARGET] ☝ Receiver set → {in_chip['dev']['name']}")
                    sse_broadcast('laptop_state', {"event": "target_set", "dev": in_chip["dev"]["name"]})
                    point_frames = 0
                    cooldown = 20
            else:
                point_frames = 0

            # ---- Pinch grab / drag / drop ----
            if grabbed is None and pinch and cur_pt:
                for i in range(len(tray)):
                    ry = slot_y0 + i * slot_gap
                    if (slot_x <= cur_pt[0] <= slot_x + card_w) and (ry <= cur_pt[1] <= ry + card_h):
                        grabbed = i
                        print(f"[GRAB] 🤏 Grabbed: {tray[i]['name']}")
                        break
            elif grabbed is not None and not pinch:
                card = tray[grabbed]
                in_zone_abs = in_zone
                if in_zone_abs and cur_pt:
                    # --- DROP ON THE "+" ---
                    ok = perform_drop(card, target_id)
                    last_drop = "✅ Sent: " + card["name"] if ok else "⚠️ No receiver online"
                    print(f"\n[DROP] ➕ {card['name']} → {'transfer started' if ok else 'FAILED (no device)'}")
                    sse_broadcast('laptop_state', {"event": "dropped", "file": card["name"], "ok": bool(ok)})
                    tray.pop(grabbed)
                    grabbed = None
                    cooldown = 40
                else:
                    grabbed = None  # dropped outside zone → return to slot

            # ---------------- DRAWING ----------------
            # Drop zone
            dz_color = (0, 255, 120) if (grabbed is not None and in_zone) else (64, 224, 208)
            cv2.circle(frame, dz, DROP_ZONE_RADIUS, dz_color, 3)
            cv2.circle(frame, dz, DROP_ZONE_RADIUS - 6, dz_color, 1)
            cv2.line(frame, (dz[0] - 34, dz[1]), (dz[0] + 34, dz[1]), dz_color, 4)
            cv2.line(frame, (dz[0], dz[1] - 34), (dz[0], dz[1] + 34), dz_color, 4)
            drop_label = "DROP HERE " + ("✚" if (grabbed is not None and in_zone) else "+")
            cv2.putText(frame, drop_label, (dz[0] - 60, dz[1] - 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, dz_color, 2)

            # Device chips
            for c in chips:
                rx, ry, rw, rh = c["rect"]
                selected = (c["id"] == target_id)
                color = (33, 150, 243) if selected else (90, 90, 110)
                draw_rounded_rect(frame, rx, ry, rx + rw, ry + rh, color, 2)
                devtag = "PH" if c["dev"].get("type") == "mobile" else "PC"
                label = truncate_text(c["dev"]['name'], 11)
                cv2.putText(frame, f"{devtag}: {label}", (rx + 8, ry + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
                if selected:
                    cv2.putText(frame, "✓", (rx + rw - 18, ry + 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 2)

            # File cards
            for i, card in enumerate(tray):
                ry = slot_y0 + i * slot_gap
                if grabbed == i and cur_pt:
                    draw_x = cur_pt[0] - 44
                    draw_y = cur_pt[1] - card_h // 2 - 24
                else:
                    draw_x = slot_x
                    draw_y = ry
                draw_rounded_rect(frame, int(draw_x), int(draw_y), int(draw_x + card_w), int(draw_y + card_h),
                                  card["color"] if grabbed != i else (0, 255, 120), 2)
                name = truncate_text(card["name"], 20)
                cv2.putText(frame, name, (int(draw_x) + 8, int(draw_y) + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3)
                cv2.putText(frame, name, (int(draw_x) + 8, int(draw_y) + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
                size_kb = card["size"] / 1024
                size_txt = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                cv2.putText(frame, size_txt, (int(draw_x) + 8, int(draw_y) + 38),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
                if grabbed == i:
                    cv2.putText(frame, "→ drop on +", (int(draw_x) + 78, int(draw_y) + 38),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 120), 2)

            # Guide line when grabbing
            if grabbed is not None and cur_pt:
                cv2.line(frame, cur_pt, dz, (0, 255, 120), 2, cv2.LINE_AA)

            # Status text
            mode = "Select a receiver: point at a chip" if target_id is None else "Drag tray card to ➕"
            cv2.putText(frame, f"Mode: {mode}", (10, H - 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            if last_drop:
                cv2.putText(frame, last_drop, (10, H - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 2)
            if grabbed is not None:
                cv2.putText(frame, "🤏 GRABBING — move to + then release", (10, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            if cooldown > 0:
                cooldown -= 1

            cv2.imshow("Gesture Transfer — Pinch to grab, drag & drop on ➕", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('d'):
                paths = select_files_dialog()
                if paths:
                    for p in paths:
                        tray.append({"path": p, "name": os.path.basename(p),
                                     "size": os.path.getsize(p), "color": file_color(p)})
            elif key == ord('c'):
                tray = []
                grabbed = None
                target_id = None
                print("\n[CLEAR] Tray cleared via key")

    cap.release()
    cv2.destroyAllWindows()

def perform_drop(card, target_id=None):
    """Send a dropped card to the selected (or first) connected device."""
    dev = None
    if connected_devices:
        if target_id and target_id in connected_devices:
            dev = connected_devices[target_id]
        else:
            dev = list(connected_devices.values())[0]
    if not dev:
        print("[WARNING] No devices connected!")
        return False

    filename = card['name']
    if dev['type'] == 'mobile':
        oid = create_offer(dev, card['path'])
        return oid is not None
    else:
        transfer_file_socket(card['path'], dev)
        return True

def select_files_dialog():
    """Multi file selection via tkinter (fist gesture)"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        files = filedialog.askopenfilenames(
            title="Select files to gesture-transfer",
            filetypes=[("All files", "*.*")]
        )
        root.destroy()
        return list(files) if files else []
    except Exception as e:
        print(f"[WARNING] GUI dialog failed: {e}, using fallback")
        test_file = os.path.join(SHARED_FOLDER, "test.txt")
        if not os.path.exists(test_file):
            with open(test_file, "w") as f:
                f.write("Test file for gesture transfer")
        return [test_file]

def show_device_selection(file_path):
    print("\n[DEVICES] Connected devices:")
    devices = list(connected_devices.values())
    for i, dev in enumerate(devices):
        print(f"  {i+1}. {dev['name']} ({dev['ip']}) - {dev.get('type', 'unknown')}")
    try:
        choice = int(input("Select device number (or Enter for first): ").strip() or "1") - 1
        if 0 <= choice < len(devices):
            dev = devices[choice]
            if dev.get('type') == 'mobile':
                create_offer(dev, file_path)
            else:
                transfer_file_socket(file_path, dev)
    except:
        if devices:
            dev = devices[0]
            if dev.get('type') == 'mobile':
                create_offer(dev, file_path)
            else:
                transfer_file_socket(file_path, dev)

def transfer_file(file_path, device_info):
    device_type = device_info.get('type', 'unknown')
    device_name = device_info.get('name', 'Unknown')
    print(f"\n[TRANSFER] Transferring: {os.path.basename(file_path)} to {device_name} ({device_type})")
    if device_type == 'mobile':
        create_offer(device_info, file_path)
    else:
        transfer_file_socket(file_path, device_info)

def transfer_file_socket(file_path, device_info):
    """Send file to laptop/desktop via raw socket"""
    try:
        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        print(f"[SENDING] Sending: {filename} ({filesize} bytes) → {device_info.get('name', 'Unknown')}")

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(15)
        client.connect((device_info.get('ip'), TRANSFER_PORT))

        metadata = json.dumps({"filename": filename, "filesize": filesize})
        client.send(metadata.encode('utf-8') + b'|||')

        with open(file_path, 'rb') as f:
            sent = 0
            while True:
                data = f.read(BUFFER_SIZE)
                if not data:
                    break
                client.send(data)
                sent += len(data)
                if filesize > 0:
                    print(f"\r[PROGRESS] {filename}: {sent/filesize*100:.1f}%", end="", flush=True)
        client.close()
        print(f"\n[COMPLETE] ✅ Sent: {filename}")
        log_history({"type": "socket", "file": filename, "size": filesize,
                     "to": device_info.get('name', '?'), "to_type": 'laptop', "status": "sent"})
    except Exception as e:
        print(f"[ERROR] Transfer failed: {e}")

# ============ FILE RECEIVER (socket, laptop-to-laptop) ============
def file_receiver():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', TRANSFER_PORT))
    server.listen(5)
    print(f"[LISTENING] Listening for socket transfers on port {TRANSFER_PORT}")

    while True:
        try:
            client, addr = server.accept()
            print(f"[INCOMING] Incoming socket transfer from {addr[0]}")

            data = b''
            while b'|||' not in data:
                chunk = client.recv(BUFFER_SIZE)
                if not chunk:
                    break
                data += chunk

            if b'|||' in data:
                metadata_str = data.split(b'|||')[0].decode('utf-8')
                metadata = json.loads(metadata_str)
                filename = metadata['filename']
                filesize = metadata['filesize']

                filepath = os.path.join(UPLOAD_FOLDER, dedupe_name(UPLOAD_FOLDER, filename))
                filename = os.path.basename(filepath)

                with open(filepath, 'wb') as f:
                    received = 0
                    while received < filesize:
                        chunk = client.recv(BUFFER_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)

                print(f"[RECEIVED] ✅ Received: {filename} ({received} bytes)")
                print(f"[SAVED] 💾 Saved to: {filepath}")
                log_history({"type": "receive", "file": filename, "size": received,
                             "from": addr[0], "status": "received"})
                sse_broadcast('file_received', {"filename": filename, "path": filepath})

            client.close()
        except Exception as e:
            print(f"[ERROR] Receive error: {e}")

# ============ HELPER FUNCTIONS ============
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def get_status_data():
    files = [f for f in os.listdir(SHARED_FOLDER) if os.path.isfile(os.path.join(SHARED_FOLDER, f))]
    with state_lock:
        devs = {k: {kk: vv for kk, vv in v.items() if kk != 'server_ip'} for k, v in connected_devices.items()}
        hist = [dict(h) for h in transfer_history]
        off = [{k: v for k, v in o.items() if k != 'path'} for o in offers.values()]
    return {
        'devices': len(connected_devices),
        'devices_list': devs,
        'files': len(files),
        'server_ip': get_local_ip(),
        'offers': off,
        'history': hist,
    }

# ============ WEB INTERFACE ROUTES ============
@app.route('/')
def index():
    local_ip = get_local_ip()
    qr_available = False
    try:
        import qrcode
        qr_available = True
    except Exception:
        pass
    return render_template_string(INDEX_HTML, local_ip=local_ip, qr_available=qr_available)

@app.route('/qr_mobile')
def qr_mobile():
    try:
        import qrcode
    except Exception:
        return jsonify({"error": "QR library not installed. Run: pip install qrcode[pil]"}), 501
    url = f"http://{get_local_ip()}:{PORT}/mobile"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/mobile')
def mobile_interface():
    with open('mobile_receiver.html', 'r', encoding='utf-8') as f:
        return f.read()

@app.route('/status')
def status():
    return jsonify(get_status_data())

@app.route('/devices')
def devices_page():
    devices_html = '<h1>📱 Connected Devices</h1><ul>'
    for dev_id, dev in connected_devices.items():
        devices_html += f'<li><strong>{dev["name"]}</strong> ({dev["ip"]}) - Type: {dev.get("type", "unknown")} - ID: {dev_id}</li>'
    devices_html += '</ul><a href="/">Back</a>'
    return devices_html

@app.route('/files')
def list_files():
    files = os.listdir(UPLOAD_FOLDER)
    html = '<h1>📁 Received Files</h1><ul>'
    for f in files:
        filepath = os.path.join(UPLOAD_FOLDER, f)
        size = os.path.getsize(filepath)
        html += f'<li><a href="/download/{f}">{f}</a> ({size} bytes)</li>'
    html += '</ul><a href="/">Back</a>'
    return html

@app.route('/history')
def history_page():
    html = '<h1>📜 Transfer History</h1><ul>'
    for h in transfer_history:
        html += f"<li>{h.get('ts','')} - {h.get('file','')} → {h.get('to', h.get('from',''))} [{h.get('status','')}]</li>"
    html += '</ul><a href="/">Back</a>'
    return html

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

@app.route('/shared/<filename>')
def download_shared(filename):
    return send_from_directory(SHARED_FOLDER, filename, as_attachment=True)

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(STATIC_FOLDER, filename)

@app.route('/register', methods=['POST'])
def register_device():
    global device_id_counter
    data = request.json or {}
    device_id = data.get('id') or f"device_{device_id_counter}"
    device_id_counter += 1
    device_name = data.get('name', 'Unknown Device')
    device_type = data.get('type', 'unknown')
    ip = request.remote_addr
    with state_lock:
        connected_devices[device_id] = {
            'id': device_id,
            'name': device_name,
            'ip': ip,
            'type': device_type,
            'server_ip': get_local_ip(),
            'last_seen': time.time()
        }
    print(f"[DEVICE] 📶 Device connected: {device_name} ({ip}) - Type: {device_type} - ID: {device_id}")
    sse_broadcast('devices_update', get_status_data())
    return jsonify({'status': 'registered', 'device_id': device_id, 'server_ip': get_local_ip()})

@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    data = request.json or {}
    device_id = data.get('id')
    if device_id in connected_devices:
        connected_devices[device_id]['last_seen'] = time.time()
    return jsonify({'status': 'ok'})

@app.route('/list_shared')
def list_shared():
    files = []
    for f in os.listdir(SHARED_FOLDER):
        filepath = os.path.join(SHARED_FOLDER, f)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            files.append({
                'name': f,
                'size': stat.st_size,
                'modified': stat.st_mtime,
                'url': f'/shared/{f}'
            })
    return jsonify({'files': files})

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    filename = dedupe_name(UPLOAD_FOLDER, file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    size = os.path.getsize(filepath)
    print(f"[UPLOAD] 📤 Upload from mobile: {filename} ({size} bytes)")
    log_history({"type": "upload", "file": filename, "size": size, "from": request.remote_addr, "status": "received"})
    sse_broadcast('file_received', {"filename": filename, "path": filepath})
    return jsonify({'status': 'uploaded', 'filename': filename, 'path': filepath})

# ============ DASHBOARD TEMPLATE ============
INDEX_HTML = r'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gesture Transfer Server</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; text-align: center; padding: 30px 16px;
               background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; margin: 0; color: white; }
        .container { max-width: 760px; margin: 0 auto; }
        h1 { margin-bottom: 4px; }
        .grid { display: flex; flex-wrap: wrap; gap: 14px; margin: 20px 0; }
        .card { flex: 1 1 220px; background: rgba(0,0,0,0.25); backdrop-filter: blur(8px);
                border-radius: 16px; padding: 18px; text-align: left; }
        .card h3 { margin: 0 0 10px; font-size: 15px; }
        .status { background: #4CAF50; display: inline-block; padding: 6px 18px; border-radius: 20px;
                  font-weight: bold; margin: 10px 0; }
        .ip { font-family: monospace; background: #ffeb3b; color: #333; padding: 6px 10px;
              border-radius: 8px; font-weight: bold; display: inline-block; word-break: break-all; }
        button { padding: 10px 20px; font-size: 14px; border: none; border-radius: 25px; cursor: pointer;
                 margin: 6px 4px; font-weight: 600; }
        .btn-light { background: #fff; color: #667eea; }
        .btn-ghost { background: rgba(255,255,255,0.2); color: #fff; border: 1px solid rgba(255,255,255,0.4); }
        .table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
        .table th, .table td { padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.15); }
        .pill { padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
        .pill.mobile { background: #2196F3; }
        .pill.laptop { background: #9C27B0; }
        .pill.unknown { background: #607D8B; }
        .pill.pending { background: #ff9800; }
        .pill.accepted { background: #4CAF50; }
        .pill.declined { background: #f44336; }
        .pill.sent, .pill.received { background: #4CAF50; }
        pre { text-align: left; background: rgba(0,0,0,0.3); padding: 14px; border-radius: 12px;
              font-size: 13px; overflow-x: auto; }
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
                 background: rgba(0,0,0,0.9); color: #fff; padding: 12px 24px; border-radius: 12px;
                 transition: opacity 0.5s; opacity: 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Gesture Transfer Server</h1>
        <div class="status">● ONLINE</div>

        <div class="card" style="text-align:center;">
            <h3 style="text-align:left;">📱 Connect your phone (QR scan or open URL)</h3>
            {% if qr_available %}
            <img src="/qr_mobile" alt="QR" style="max-width:150px; border-radius:12px; margin:6px auto;">
            {% endif %}
            <div><span class="ip">http://{{ local_ip }}:5000/mobile</span></div>
            <button class="btn-light" onclick="copyUrl()">📋 Copy URL</button>
            <button class="btn-ghost" onclick="window.location.href='/mobile'">Open Mobile UI</button>
        </div>

        <div class="grid">
            <div class="card">
                <h3>🖥️ Laptop Camera Gestures</h3>
                <p style="font-size:13px;">👊 Fist (hold) = add files to tray<br>
                🤏 Pinch on card = grab file<br>
                👉 Drag to <b style="color:#20e08a;">➕</b> = drop & transfer<br>
                🖐 Open palm (hold) = clear tray<br>
                ☝ Point at chip = choose receiver</p>
            </div>
            <div class="card">
                <h3>📱 Mobile Camera Gestures</h3>
                <p style="font-size:13px;">📷 Camera opens with a <b style="color:#20e08a;">➕</b> accept zone.<br>
                When a file arrives, show an <b>open palm</b> over the ➕ to ACCEPT.<br>
                (Tap Accept button as fallback)</p>
            </div>
        </div>

        <div class="card">
            <h3>📶 Connected Devices (<span id="devCount">0</span>)</h3>
            <div id="devices"><p style="opacity:.7;">Waiting for devices…</p></div>
        </div>

        <div class="card">
            <h3>📬 Active Offers</h3>
            <div id="offers"><p style="opacity:.7;">None</p></div>
        </div>

        <div class="card">
            <h3>📜 Transfer History</h3>
            <div id="history"><p style="opacity:.7;">None yet</p></div>
        </div>

        <p style="margin-top:24px; opacity:.65; font-size:13px;">Press 'q' in the camera window to close the camera (server keeps running).</p>
    </div>
    <div class="toast" id="toast"></div>

    <script>
        function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.style.opacity=1;
            setTimeout(()=>t.style.opacity=0, 2500); }
        function copyUrl(){ const ip = "{{ local_ip }}"; navigator.clipboard.writeText(`http://${ip}:5000/mobile`).then(()=>toast('URL copied!')); }
        function esc(s){ return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
        function refresh(){
            fetch('/status').then(r=>r.json()).then(d=>{
                document.getElementById('devCount').textContent = d.devices;
                const devs = document.getElementById('devices');
                if(!d.devices_list || Object.keys(d.devices_list).length===0){
                    devs.innerHTML='<p style="opacity:.7;">Waiting for devices…</p>';
                } else {
                    devs.innerHTML = Object.entries(d.devices_list).map(([k,v])=>
                        `<p>📌 <b>${esc(v.name)}</b> (${esc(v.ip)}) <span class="pill ${esc(v.type)}">${esc(v.type)}</span></p>`).join('');
                }
                const off = document.getElementById('offers');
                off.innerHTML = (d.offers && d.offers.length) ? d.offers.map(o=>
                    `<p>${esc(o.filename)} ➜ <span class="pill ${esc(o.status)}">${esc(o.status)}</span></p>`).join('')
                    : '<p style="opacity:.7;">None</p>';
                const hist = document.getElementById('history');
                hist.innerHTML = (d.history && d.history.length) ? d.history.slice(0,12).map(h=>
                    `<p style="font-size:12px;">[${esc(h.ts)}] ${esc(h.file)} → ${esc(h.to||h.from||'?')} <span class="pill ${esc(h.status)}">${esc(h.status)}</span></p>`).join('')
                    : '<p style="opacity:.7;">None yet</p>';
            }).catch(()=>{});
        }
        setInterval(refresh, 2500);
        refresh();
        const es = new EventSource('/events');
        es.addEventListener('offer', e => { refresh(); toast('📤 New offer sent!'); });
        es.addEventListener('offer_status', e => { refresh(); toast('📬 Offer status updated'); });
        es.addEventListener('device' , e => { refresh(); });
        es.addEventListener('devices_update', e => { refresh(); });
        es.addEventListener('file_received', e => { refresh(); toast('📥 New file received'); });
    </script>
</body>
</html>
'''

# ============ MAIN ============
if __name__ == '__main__':
    receiver_thread = threading.Thread(target=file_receiver, daemon=True)
    receiver_thread.start()

    gesture_thread = threading.Thread(target=hand_gesture_listener, daemon=True)
    gesture_thread.start()

    local_ip = get_local_ip()
    print("\n[STARTING] Gesture Transfer Server Starting...")
    print(f"[WEB] Dashboard: http://localhost:{PORT}")
    print(f"[MOBILE] Mobile interface: http://{local_ip}:{PORT}/mobile")
    print(f"[LAPTOP] Laptop-to-laptop: Run this on both laptops")

    if not os.environ.get('GT_NO_BROWSER'):
        webbrowser.open(f'http://localhost:{PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)