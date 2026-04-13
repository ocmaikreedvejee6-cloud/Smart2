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

# ================= CONFIG =================
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 9600

TELEGRAM_TOKEN = "8490765768:AAFU-Vpi0HAiS5_2V2mcboWYeiG8W4neiVE"
CHAT_ID = "7175315173"

CONFIDENCE_THRESHOLD = 70

FACE_TIMEOUT = 3
TELEGRAM_COOLDOWN = 30

EMAIL_ADDRESS = "growpfiveim312@gmail.com"
EMAIL_PASSWORD = "group5123456789"
RECEIVER_EMAIL = "ocmaikreedvejee6@gmail.com"

# 🔥 CHANGE THIS TO YOUR RASPBERRY PI IP
RASPBERRY_PI_IP = "192.168.1.100"
STREAM_URL = f"http://{RASPBERRY_PI_IP}:5000"

last_face_time = 0
last_telegram_time = 0
system_on = False
unknown_triggered = False

frame_global = None
lock = threading.Lock()
# =========================================

# ================= LOAD MODEL =================
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer1.yml")

label_map = np.load("labels1.npy", allow_pickle=True).item()

# ================= FACE DETECTOR =================
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# ================= ARDUINO =================
arduino = serial.Serial(SERIAL_PORT, BAUD_RATE)
time.sleep(2)

# ================= CAMERA =================
cap = cv2.VideoCapture(0)

# ================= TELEGRAM =================
def send_telegram_image(image_path, message):
    global last_telegram_time

    now = time.time()
    if now - last_telegram_time < TELEGRAM_COOLDOWN:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    try:
        with open(image_path, "rb") as photo:
            response = requests.post(
                url,
                files={"photo": photo},
                data={
                    "chat_id": CHAT_ID,
                    "caption": message + f"\n🔗 Live: {STREAM_URL}"
                }
            )

        print("Telegram response:", response.text)

        if response.status_code == 200:
            last_telegram_time = now
            print("✅ Telegram sent")

    except Exception as e:
        print("Telegram error:", e)

# ================= EMAIL =================
def send_email(image_path):
    msg = EmailMessage()
    msg['Subject'] = "Unknown Person Detected"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECEIVER_EMAIL
    msg.set_content(f"Unknown person detected.\n\nLive Stream:\n{STREAM_URL}")

    try:
        with open(image_path, 'rb') as f:
            file_data = f.read()

        msg.add_attachment(file_data, maintype='image', subtype='jpeg', filename="unknown.jpg")

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        print("📧 Email sent")

    except Exception as e:
        print("Email error:", e)

# ================= CLEANUP =================
def cleanup_old_images(folder="captures", max_files=100):
    files = sorted(
        [os.path.join(folder, f) for f in os.listdir(folder)],
        key=os.path.getctime
    )

    while len(files) > max_files:
        os.remove(files[0])
        files.pop(0)

# ================= FLASK STREAM =================
app = Flask(__name__)

def generate_frames():
    global frame_global
    while True:
        with lock:
            if frame_global is None:
                continue
            ret, buffer = cv2.imencode('.jpg', frame_global)
            frame = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# ================= MAIN LOOP =================
def main():
    global last_face_time, system_on, unknown_triggered, frame_global

    print("🚀 System Running with LIVE STREAM...")

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        with lock:
            frame_global = frame.copy()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        now = time.time()

        if len(faces) > 0:
            last_face_time = now

            if not system_on:
                arduino.write(b'ON\n')
                system_on = True
                print("💡 RELAYS ON")

            unknown_triggered = False

            for (x, y, w, h) in faces:
                face = gray[y:y+h, x:x+w]

                try:
                    label, confidence = recognizer.predict(face)
                except:
                    continue

                if confidence >= CONFIDENCE_THRESHOLD:
                    print("⚠️ Unknown detected")

                    if not unknown_triggered:
                        if not os.path.exists("captures"):
                            os.makedirs("captures")

                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        img_path = f"captures/unknown_{timestamp}.jpg"

                        cv2.imwrite(img_path, frame)

                        send_telegram_image(
                            img_path,
                            f"Unknown detected\nTime: {timestamp}"
                        )

                        send_email(img_path)

                        cleanup_old_images()
                        unknown_triggered = True

        else:
            if system_on and (now - last_face_time > FACE_TIMEOUT):
                arduino.write(b'OFF\n')
                system_on = False
                print("❌ RELAYS OFF")

        time.sleep(0.2)

# ================= RUN THREADS =================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    main()
