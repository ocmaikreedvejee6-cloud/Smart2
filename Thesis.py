import cv2
import numpy as np
import serial
import time
import requests
import smtplib
import os
import threading
from datetime import datetime
from email.message import EmailMessage
from flask import Flask, Response
from pyngrok import ngrok

# ================= CONFIG =================
SERIAL_PORT = "/dev/serial0"
BAUD_RATE = 9600

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

EMAIL_ADDRESS = "YOUR_EMAIL"
EMAIL_APP_PASSWORD = "YOUR_APP_PASSWORD"
RECEIVER_EMAIL = "RECEIVER_EMAIL"

NGROK_AUTH_TOKEN = "YOUR_NGROK_TOKEN"

# ================= GLOBALS =================
arduino = None
auto_mode = True

frame_global = None
lock = threading.Lock()

cap = None
system_on = False

last_alert_time = 0
ALERT_COOLDOWN = 30

# ================= NGROK =================
ngrok.set_auth_token(NGROK_AUTH_TOKEN)

# ================= UART =================
def connect_uart():
    global arduino
    while True:
        try:
            arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            time.sleep(2)
            print("✅ UART Connected")
            return
        except:
            print("❌ UART retry...")
            time.sleep(2)

def send(cmd):
    try:
        if arduino:
            arduino.write(cmd.encode())
    except:
        connect_uart()

# ================= CAMERA =================
def connect_camera():
    global cap
    while True:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            print("📷 Camera OK")
            return
        time.sleep(2)

# ================= MODE LISTENER =================
def read_serial():
    global auto_mode
    while True:
        try:
            if arduino and arduino.in_waiting:
                msg = arduino.readline().decode().strip()

                if msg == "AUTO_ON":
                    auto_mode = True
                    print("🤖 AUTO MODE")

                elif msg == "AUTO_OFF":
                    auto_mode = False
                    print("🎮 MANUAL MODE")

        except:
            pass
        time.sleep(0.1)

# ================= AI DETECTION =================
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

hog = cv2.HOGDescriptor()
hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

def detect_intruder(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.2, 5)
    face_detected = len(faces) > 0

    boxes, _ = hog.detectMultiScale(frame)

    person_detected = len(boxes) > 0

    intruder = (person_detected and not face_detected) or (not face_detected)

    return intruder

# ================= RELAY CONTROL =================
def relay_all(state):
    if state:
        send("R1_ON\n")
        send("R2_ON\n")
        send("R3_ON\n")
    else:
        send("R1_OFF\n")
        send("R2_OFF\n")
        send("R3_OFF\n")

# ================= ALERT SYSTEM =================
def send_telegram(image_path, msg):
    global last_alert_time

    if time.time() - last_alert_time < ALERT_COOLDOWN:
        return

    try:
        with open(image_path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": f},
                data={"chat_id": CHAT_ID, "caption": msg}
            )
        last_alert_time = time.time()
    except:
        pass

def send_email(image_path):
    msg = EmailMessage()
    msg['Subject'] = "🚨 Intruder Alert"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECEIVER_EMAIL

    try:
        with open(image_path, "rb") as f:
            msg.add_attachment(f.read(), maintype='image', subtype='jpeg')

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            s.send_message(msg)
    except:
        pass

# ================= FLASK STREAM =================
app = Flask(__name__)

def generate():
    global frame_global
    while True:
        with lock:
            if frame_global is None:
                continue
            frame = frame_global.copy()

        _, buffer = cv2.imencode(".jpg", frame)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' +
               buffer.tobytes() + b'\r\n')

@app.route("/")
def video():
    return Response(generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame")

# ================= MAIN =================
def main():
    global frame_global, system_on

    connect_uart()
    connect_camera()

    threading.Thread(target=read_serial, daemon=True).start()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.resize(frame, (640, 360))

        with lock:
            frame_global = frame.copy()

        intruder = detect_intruder(frame)

        # ================= AUTO MODE =================
        if auto_mode:
            if intruder and not system_on:
                relay_all(True)
                system_on = True

                # Save snapshot
                path = f"intruder_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                cv2.imwrite(path, frame)

                threading.Thread(target=send_telegram,
                    args=(path, "🚨 INTRUDER DETECTED")).start()

                threading.Thread(target=send_email,
                    args=(path,)).start()

            elif not intruder and system_on:
                relay_all(False)
                system_on = False

        time.sleep(0.03)

# ================= START =================
if __name__ == "__main__":
    url = ngrok.connect(5000)
    print("🌍 Live:", url)

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000),
                     daemon=True).start()

    main()
