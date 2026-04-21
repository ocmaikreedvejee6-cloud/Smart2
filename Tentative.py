import cv2
import numpy as np
import serial
import time
import requests
import smtplib
import os
from datetime import datetime
from email.message import EmailMessage
from flask import Flask, Response
import threading
from pyngrok import ngrok

# ================= CONFIG =================
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

TELEGRAM_TOKEN = "8490765768:AAFU-Vpi0HAiS5_2V2mcboWYeiG8W4neiVE"
CHAT_ID = "7175315173"

CONFIDENCE_THRESHOLD = 70
FACE_TIMEOUT = 3
TELEGRAM_COOLDOWN = 30

EMAIL_ADDRESS = "growpfiveim312@gmail.com"
EMAIL_APP_PASSWORD = "qerlwnbhfcaprcll"
RECEIVER_EMAIL = "ocmaikreedvejee6@gmail.com"

NGROK_AUTH_TOKEN = "3CNooZSFRM64UqMFHQhvjL167bU_4RZuEZf7oztKsnwVyVcHJ"

PERSON_DETECT_INTERVAL = 3
VIDEO_TIMEOUT = 3

# ================= GLOBALS =================
last_face_time = 0
last_telegram_time = 0
system_on = False
auto_mode = True  # 🔥 MODE FROM ESP32

frame_global = None
lock = threading.Lock()
STREAM_URL = None

frame_count = 0

recording = False
video_writer = None
last_intruder_time = 0

cap = None
esp32 = None

# ================= NGROK =================
ngrok.set_auth_token(NGROK_AUTH_TOKEN)

# ================= MODELS =================
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer1.yml")
label_map = np.load("labels1.npy", allow_pickle=True).item()

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

hog = cv2.HOGDescriptor()
hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

# ================= ESP32 CONNECTION =================
def connect_esp32():
    global esp32
    while True:
        try:
            esp32 = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            time.sleep(2)
            print("✅ ESP32 connected")
            return
        except:
            print("❌ ESP32 not found, retrying...")
            time.sleep(3)

def send(cmd):
    global esp32
    try:
        if esp32:
            esp32.write(cmd)
    except:
        print("⚠️ ESP32 disconnected. Reconnecting...")
        connect_esp32()

# ================= MODE LISTENER =================
def read_serial():
    global auto_mode

    while True:
        try:
            if esp32 and esp32.in_waiting:
                msg = esp32.readline().decode().strip()

                if msg == "MODE:AUTO":
                    auto_mode = True
                    print("🤖 AUTO MODE")

                elif msg == "MODE:MANUAL":
                    auto_mode = False
                    print("🎮 MANUAL MODE")

        except:
            pass

        time.sleep(0.1)

# ================= RELAY CONTROL =================
def relay_all(state):
    if state:
        send(b'ALL_ON\n')
    else:
        send(b'ALL_OFF\n')

# ================= CAMERA =================
def connect_camera():
    global cap
    while True:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
            print("✅ Camera connected")
            return
        else:
            print("❌ Camera not found, retrying...")
            time.sleep(2)

# ================= FLASK =================
app = Flask(__name__)

def generate_frames():
    global frame_global
    while True:
        with lock:
            if frame_global is None:
                continue
            frame = frame_global.copy()

        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# ================= ALERTS =================
def send_telegram(image_path, message):
    global last_telegram_time, STREAM_URL

    now = time.time()
    if now - last_telegram_time < TELEGRAM_COOLDOWN:
        return

    try:
        with open(image_path, "rb") as photo:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": photo},
                data={
                    "chat_id": CHAT_ID,
                    "caption": f"{message}\n\n🌍 Live:\n{STREAM_URL}"
                }
            )
        last_telegram_time = now
        print("✅ Telegram sent")

    except Exception as e:
        print("Telegram error:", e)

def send_email(image_path):
    msg = EmailMessage()
    msg['Subject'] = "🚨 Intruder Detected"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECEIVER_EMAIL
    msg.set_content(f"Intruder detected\nLive: {STREAM_URL}")

    try:
        with open(image_path, 'rb') as f:
            msg.add_attachment(f.read(), maintype='image', subtype='jpeg')

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            smtp.send_message(msg)

        print("📧 Email sent")

    except Exception as e:
        print("Email error:", e)

# ================= VIDEO =================
def start_recording(frame):
    global recording, video_writer

    os.makedirs("videos", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"videos/intruder_{timestamp}.avi"

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    h, w, _ = frame.shape

    video_writer = cv2.VideoWriter(path, fourcc, 20, (w, h))
    recording = True

    snap = path.replace(".avi", ".jpg")
    cv2.imwrite(snap, frame)

    threading.Thread(target=send_telegram, args=(snap, "🚨 INTRUDER DETECTED")).start()
    threading.Thread(target=send_email, args=(snap,)).start()

    print("🎥 Recording started")

def stop_recording():
    global recording, video_writer

    if video_writer:
        video_writer.release()
        video_writer = None

    recording = False
    print("🛑 Recording stopped")

# ================= MAIN =================
def main():
    global last_face_time, system_on
    global frame_global, frame_count
    global recording, last_intruder_time
    global cap

    print("🚀 System Running...")

    connect_esp32()
    connect_camera()

    threading.Thread(target=read_serial, daemon=True).start()

    while True:
        ret, frame = cap.read()

        if not ret:
            cap.release()
            time.sleep(2)
            connect_camera()
            continue

        frame = cv2.resize(frame, (640, 360))

        with lock:
            frame_global = frame.copy()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(gray, 1.2, 6)
        face_detected = len(faces) > 0

        # PERSON DETECTION
        person_detected = False
        boxes = []

        frame_count += 1
        if frame_count % PERSON_DETECT_INTERVAL == 0:
            boxes, _ = hog.detectMultiScale(frame)
            person_detected = len(boxes) > 0

        intruder = False

        # FACE RECOGNITION (UNCHANGED)
        for (x, y, w, h) in faces:
            face = gray[y:y+h, x:x+w]

            try:
                label, conf = recognizer.predict(face)
                name = label_map.get(label, "Unknown") if conf < CONFIDENCE_THRESHOLD else "Unknown"
            except:
                name = "Unknown"

            if name == "Unknown":
                intruder = True

            cv2.rectangle(frame, (x, y), (x+w, y+h), (255,0,0), 2)
            cv2.putText(frame, name, (x, y-10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

        if person_detected and not face_detected:
            intruder = True

        now = time.time()

        # ================= RELAY CONTROL (AUTO ONLY) =================
        if person_detected or face_detected:
            last_face_time = now

        if auto_mode:
            if person_detected or face_detected:
                if not system_on:
                    relay_all(True)
                    system_on = True
                    print("💡 RELAYS ON (AUTO)")
            else:
                if system_on and (now - last_face_time > FACE_TIMEOUT):
                    relay_all(False)
                    system_on = False
                    print("❌ RELAYS OFF (AUTO)")

        # ================= RECORDING (ALWAYS ACTIVE) =================
        if intruder:
            last_intruder_time = now
            if not recording:
                start_recording(frame)

        if recording and video_writer:
            video_writer.write(frame)

        if recording and (now - last_intruder_time > VIDEO_TIMEOUT):
            stop_recording()

        time.sleep(0.03)

# ================= START =================
if __name__ == "__main__":
    tunnel = ngrok.connect(5000)
    STREAM_URL = tunnel.public_url

    print("🌍 NGROK:", STREAM_URL)

    threading.Thread(target=run_flask, daemon=True).start()
    main()
