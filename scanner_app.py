import os
import io
import json
import time
import zipfile
import threading

from flask import Flask, request, jsonify, render_template, send_file, abort
from scan_logic import (load_image_bgr, find_screen_contour, process_document,
                        transform_with_points, ensure_thumbnail, to_png_bytes,
                        HEIC_OK)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "need_to_scan")
DONE_DIR = os.path.join(INPUT_DIR, "done")
OUTPUT_DIR = os.path.join(BASE_DIR, "scanned")
THUMB_DIR = os.path.join(OUTPUT_DIR, "thumbs")
FAILURES_FILE = os.path.join(INPUT_DIR, "failures.json")
ALLOWED = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".heif"}
PORT = 5050
PING_TIMEOUT = 12  # секунд без отклика страницы - сервер выключается

for d in (INPUT_DIR, DONE_DIR, OUTPUT_DIR, THUMB_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 МБ на загрузку

last_ping = {"t": time.time()}


def safe_name(name):
    """Безопасное имя файла, сохраняет кириллицу (локальное приложение)."""
    name = os.path.basename(name.replace("\\", "/"))
    return name.replace("/", "").replace("\x00", "")


def load_failures():
    if os.path.exists(FAILURES_FILE):
        try:
            return set(json.load(open(FAILURES_FILE)))
        except Exception:
            return set()
    return set()


def save_failures(s):
    json.dump(sorted(s), open(FAILURES_FILE, "w"), ensure_ascii=False)


@app.before_request
def touch_ping():
    last_ping["t"] = time.time()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ping", methods=["POST"])
def ping():
    return "ok"


@app.route("/state")
def state():
    fails = load_failures()
    queue = sorted(
        f for f in os.listdir(INPUT_DIR)
        if os.path.splitext(f)[1].lower() in ALLOWED and f not in fails
    )
    scans = []
    for f in sorted(os.listdir(OUTPUT_DIR)):
        if f.lower().endswith(".pdf"):
            thumb = os.path.splitext(f)[0] + ".png"
            ensure_thumbnail(os.path.join(OUTPUT_DIR, f),
                             os.path.join(THUMB_DIR, thumb))
            scans.append({"name": f, "thumb": thumb})
    return jsonify(queue=queue, scans=scans, failures=sorted(fails), heic=HEIC_OK)


@app.route("/upload", methods=["POST"])
def upload():
    overwrite = request.form.get("overwrite") == "1"
    saved, conflicts, errors = [], [], []
    for f in request.files.getlist("files"):
        name = safe_name(f.filename)
        if not name:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED:
            errors.append(name + ": неподдерживаемый формат")
            continue
        if ext in (".heic", ".heif") and not HEIC_OK:
            errors.append(name + ": HEIC не поддерживается, выполните: "
                                 "pip install --user pillow-heif")
            continue
        dest = os.path.join(INPUT_DIR, name)
        if os.path.exists(dest) and not overwrite:
            conflicts.append(name)
            continue
        f.save(dest)
        saved.append(name)
    return jsonify(saved=saved, conflicts=conflicts, errors=errors)


@app.route("/scan_all", methods=["POST"])
def scan_all():
    fails = load_failures()
    ok, bad = 0, []
    for f in sorted(os.listdir(INPUT_DIR)):
        ext = os.path.splitext(f)[1].lower()
        if ext not in ALLOWED or f in fails:
            continue
        src = os.path.join(INPUT_DIR, f)
        out = os.path.join(OUTPUT_DIR, os.path.splitext(f)[0] + "_scaned.pdf")
        try:
            success = process_document(src, out)
        except Exception:
            success = False
        if success:
            ok += 1
            fails.discard(f)
            os.replace(src, os.path.join(DONE_DIR, f))
        else:
            fails.add(f)
            bad.append(f)
    save_failures(fails)
    return jsonify(ok=ok, failed=bad)


@app.route("/thumb/<name>")
def thumb(name):
    return send_file(os.path.join(THUMB_DIR, safe_name(name)))


@app.route("/download/<name>")
def download(name):
    return send_file(os.path.join(OUTPUT_DIR, safe_name(name)), as_attachment=True)


@app.route("/delete/<name>", methods=["POST"])
def delete(name):
    name = safe_name(name)
    p = os.path.join(OUTPUT_DIR, name)
    if os.path.exists(p):
        os.remove(p)
    t = os.path.join(THUMB_DIR, os.path.splitext(name)[0] + ".png")
    if os.path.exists(t):
        os.remove(t)
    return jsonify(ok=True)


@app.route("/download_all")
def download_all():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(OUTPUT_DIR)):
            if f.lower().endswith(".pdf"):
                z.write(os.path.join(OUTPUT_DIR, f), f)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="scans.zip", mimetype="application/zip")


@app.route("/manual/<name>")
def manual(name):
    name = safe_name(name)
    if not os.path.exists(os.path.join(INPUT_DIR, name)):
        abort(404)
    return render_template("manual.html", name=name)


@app.route("/image/<name>")
def image(name):
    name = safe_name(name)
    src = os.path.join(INPUT_DIR, name)
    ext = os.path.splitext(name)[1].lower()
    if ext in (".pdf", ".heic", ".heif"):
        return send_file(to_png_bytes(src), mimetype="image/png")
    return send_file(src)


@app.route("/manual/<name>/apply", methods=["POST"])
def manual_apply(name):
    name = safe_name(name)
    src = os.path.join(INPUT_DIR, name)
    if not os.path.exists(src):
        abort(404)
    points = request.json["points"]
    out = os.path.join(OUTPUT_DIR, os.path.splitext(name)[0] + "_scaned.pdf")
    transform_with_points(src, points, out)
    fails = load_failures()
    fails.discard(name)
    save_failures(fails)
    os.replace(src, os.path.join(DONE_DIR, name))
    return jsonify(ok=True)


def watchdog():
    """Выключение сервера, когда страница закрыта (heartbeat пропал)."""
    while True:
        time.sleep(2)
        if time.time() - last_ping["t"] > PING_TIMEOUT:
            os._exit(0)


if __name__ == "__main__":
    threading.Thread(target=watchdog, daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
