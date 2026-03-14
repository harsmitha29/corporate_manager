# process_images.py

import os
import json
import mysql.connector
from face_recognition import load_image_file, face_encodings

db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Harsmitha@123",
    database="corporate_manager"
)
cursor = db.cursor()

# 🔹 Folder containing user images
IMAGES_FOLDER = "face_utils/images"  # filename = user_id.jpg

def process_images():
    for filename in os.listdir(IMAGES_FOLDER):
        if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        user_id = os.path.splitext(filename)[0]  # e.g. 30.jpg → 30
        image_path = os.path.join(IMAGES_FOLDER, filename)

        # 🔹 Load image & extract face encoding
        image = load_image_file(image_path)
        encodings = face_encodings(image)

        if not encodings:
            print(f"[!] No face found in {filename}")
            continue

        encoding = encodings[0]  # 128-d vector

        # ✅ CONVERT TO JSON (CRITICAL FIX)
        encoding_json = json.dumps(encoding.tolist())

        # 🔹 Save to DB
        cursor.execute(
            "UPDATE tbl_users SET face_encoding=%s WHERE user_id=%s",
            (encoding_json, user_id)
        )

        print(f"[+] Face encoding saved for user_id={user_id}")

    db.commit()
    print("[✅] All images processed successfully")

if __name__ == "__main__":
    process_images()
