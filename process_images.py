"""
process_images.py - Face enrollment script
Uses dlib/numpy directly instead of face_recognition to avoid the models check.
"""
import os
import json
import sys
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

db = mysql.connector.connect(
    host     = os.environ.get("DB_HOST",     "localhost"),
    user     = os.environ.get("DB_USER",     "root"),
    password = os.environ.get("DB_PASSWORD", ""),
    database = os.environ.get("DB_NAME",     "corporate_manager"),
)
cursor = db.cursor()

IMAGES_FOLDER = "face_utils/images"


def get_face_encoding(image_path):
    """Get face encoding using dlib directly — bypasses face_recognition models check."""
    try:
        import dlib
        import numpy as np
        from PIL import Image

        # Load dlib models from face_recognition_models if available,
        # otherwise use dlib's built-in
        try:
            import face_recognition_models
            shape_model_path    = face_recognition_models.pose_predictor_5_point_model_location()
            face_rec_model_path = face_recognition_models.face_recognition_model_location()
            detector_model      = face_recognition_models.cnn_face_detector_model_location()
        except Exception:
            print("[!] face_recognition_models not found via import, trying direct path...")
            # Try to find models in site-packages
            import site
            sp = site.getsitepackages()[0]
            model_dir = os.path.join(sp, "face_recognition_models", "models")
            if not os.path.isdir(model_dir):
                return None
            shape_model_path    = os.path.join(model_dir, "shape_predictor_5_face_landmarks.dat")
            face_rec_model_path = os.path.join(model_dir, "dlib_face_recognition_resnet_model_v1.dat")
            detector_model      = os.path.join(model_dir, "mmod_human_face_detector.dat")

        # Load models
        face_detector    = dlib.get_frontal_face_detector()
        shape_predictor  = dlib.shape_predictor(shape_model_path)
        face_rec_model   = dlib.face_recognition_model_v1(face_rec_model_path)

        # Load image
        img   = Image.open(image_path).convert("RGB")
        img_array = np.array(img)

        # Detect faces
        detections = face_detector(img_array, 1)
        if not detections:
            return None

        # Get shape and encoding
        shape    = shape_predictor(img_array, detections[0])
        encoding = face_rec_model.compute_face_descriptor(img_array, shape)
        return list(encoding)

    except Exception as e:
        print(f"[!] Error getting face encoding: {e}")
        return None


def process_images():
    if not os.path.isdir(IMAGES_FOLDER):
        print(f"[!] Folder not found: {IMAGES_FOLDER}")
        return

    processed = 0
    for filename in os.listdir(IMAGES_FOLDER):
        if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
            continue

        user_id    = os.path.splitext(filename)[0]
        image_path = os.path.join(IMAGES_FOLDER, filename)

        print(f"[~] Processing {filename} (user_id={user_id})...")

        encoding = get_face_encoding(image_path)

        if encoding is None:
            print(f"[!] No face detected in {filename} — skipping")
            continue

        encoding_json = json.dumps(encoding)
        cursor.execute(
            "UPDATE tbl_users SET face_encoding=%s WHERE user_id=%s",
            (encoding_json, user_id)
        )
        print(f"[+] Face encoding saved for user_id={user_id}")
        processed += 1

    db.commit()
    db.close()
    print(f"\n[✅] Done — {processed} employee(s) enrolled successfully")


if __name__ == "__main__":
    process_images()