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

CONFIDENCE_THRESHOLD = 70   # LOWER = stricter (LBPH)
FACE_TIMEOUT = 3
TELEGRAM_COOLDOWN = 30

EMAIL_ADDRESS = "growpfiveim312@gmail.com"
EMAIL_APP_PASSWORD = "qerlwnbhfcaprcll"
RECEIVER_EMAIL = "ocmaikreedvejee6@gmail.com"

NGROK_AUTH_TOKEN = "YOUR_NGROK_TOKEN"

PERSON_DETECT_INTERVAL = 3

# ================= GLOBALS =================
last_face_time = 0
last_telegram_time = 0
system_on = False
unknown_triggered = False
frame_global = None
lock = threading.Lock()
STREAM_URL = None
frame_count = 0

# ================= NGROK =================
ngrok.set_auth_token(NGROK_AUTH_TOKEN)

# ================= MODELS =================
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer1.yml")

label_map = np.load("labels1.npy", allow_pickle=True).item()

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# PERSON DETECTOR (built-in)
hog = cv2.HOGDescriptor()
hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

# ================= ARDUINO =================
arduino = serial.Serial(SERIAL_PORT, BAUD_RATE)
time.sleep(2)

# ================= CAMERA =================
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
cap.set(cv2.CAP_PROP_FPS, 30)

# ================= FLASK =================
app = Flask(__name__)

def generate_frames():
    global frame_global
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]

    while True:
        with lock:
            if frame_global is None:
                continue
            frame = frame_global.copy()

        ret, buffer = cv2.imencode('.jpg', frame, encode_param)
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

# ================= TELEGRAM =================
def send_telegram_image(image_path, message):
    global last_telegram_time, STREAM_URL

    now = time.time()
    if now - last_telegram_time < TELEGRAM_COOLDOWN:
        return

    try:
        with open(image_path, "rb") as photo:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": photo},
                data={"chat_id": CHAT_ID,
                      "caption": message + f"\n🔗 {STREAM_URL}"}
            )

        last_telegram_time = now
        print("✅ Telegram sent")

    except Exception as e:
        print("Telegram error:", e)

# ================= EMAIL =================
def send_email(image_path):
    global STREAM_URL

    msg = EmailMessage()
    msg['Subject'] = "🚨 Unknown Person Detected"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECEIVER_EMAIL
    msg.set_content(f"Unknown detected\nLive: {STREAM_URL}")

    try:
        with open(image_path, 'rb') as f:
            msg.add_attachment(f.read(), maintype='image', subtype='jpeg')

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            smtp.send_message(msg)

        print("📧 Email sent")

    except Exception as e:
        print("Email error:", e)

# ================= CLEANUP =================
def cleanup_old_images(folder="captures", max_files=100):
    if not os.path.exists(folder):
        return

    files = sorted(
        [os.path.join(folder, f) for f in os.listdir(folder)],
        key=os.path.getctime
    )

    while len(files) > max_files:
        os.remove(files[0])
        files.pop(0)

# ================= MAIN =================
def main():
    global last_face_time, system_on, unknown_triggered
    global frame_global, STREAM_URL, frame_count

    print("🚀 System Running...")
    print("🌍 Live:", STREAM_URL)

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.resize(frame, (640, 360))

        with lock:
            frame_global = frame

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # ===== FACE DETECTION =====
        faces = face_cascade.detectMultiScale(gray, 1.2, 6)
        face_detected = len(faces) > 0

        # ===== PERSON DETECTION =====
        person_detected = False
        boxes = []

        frame_count += 1
        if frame_count % PERSON_DETECT_INTERVAL == 0:
            boxes, _ = hog.detectMultiScale(frame, winStride=(8, 8))
            person_detected = len(boxes) > 0

        now = time.time()

        # ===== CONTROL LOGIC =====
        if person_detected or face_detected:
            last_face_time = now

            if not system_on:
                arduino.write(b'ON\n')
                system_on = True
                print("💡 RELAYS ON")

            # ===== FACE RECOGNITION =====
            for (x, y, w, h) in faces:
                face = gray[y:y+h, x:x+w]

                try:
                    label, confidence = recognizer.predict(face)

                    if confidence < CONFIDENCE_THRESHOLD:
                        name = label_map.get(label, "Unknown")
                    else:
                        name = "Unknown"

                except:
                    name = "Unknown"

                # UNKNOWN ALERT
                if name == "Unknown" and not unknown_triggered:
                    unknown_triggered = True

                    if not os.path.exists("captures"):
                        os.makedirs("captures")

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    path = f"captures/unknown_{timestamp}.jpg"
                    cv2.imwrite(path, frame)

                    threading.Thread(
                        target=send_telegram_image,
                        args=(path, f"Unknown detected\n{timestamp}")
                    ).start()

                    threading.Thread(
                        target=send_email,
                        args=(path,)
                    ).start()

                    cleanup_old_images()

                # ===== DRAW FACE =====
                cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 0, 0), 2)

                cv2.rectangle(frame, (x, y-25), (x+w, y), (0, 0, 0), -1)
                cv2.putText(frame, name,
                            (x, y-5),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (255, 255, 255),
                            2)

        else:
            unknown_triggered = False

            if system_on and (now - last_face_time > FACE_TIMEOUT):
                arduino.write(b'OFF\n')
                system_on = False
                print("❌ RELAYS OFF")

        # ===== DRAW PERSON =====
        for (x, y, w, h) in boxes:
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

            cv2.rectangle(frame, (x, y-25), (x+w, y), (0, 0, 0), -1)
            cv2.putText(frame, "Person",
                        (x, y-5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 0),
                        2)

        time.sleep(0.03)

# ================= START =================
if __name__ == "__main__":
    tunnel = ngrok.connect(5000, "http")
    STREAM_URL = tunnel.public_url

    print("🌍 NGROK URL:", STREAM_URL)

    threading.Thread(target=run_flask, daemon=True).start()
    main()
