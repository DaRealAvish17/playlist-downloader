from flask import Flask, render_template, send_file, after_this_request
from flask_socketio import SocketIO, emit
import yt_dlp
import threading
import os
import zipfile
import uuid
import shutil

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

BASE_DIR = os.environ.get("RENDER_DISK_PATH", "downloads")
os.makedirs(BASE_DIR, exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


# ✅ Download + auto delete after sending
@app.route("/download/<filename>")
def download_file(filename):
    file_path = os.path.join(BASE_DIR, filename)

    @after_this_request
    def remove_file(response):
        try:
            os.remove(file_path)
        except Exception as e:
            print("Delete error:", e)
        return response

    return send_file(file_path, as_attachment=True)


@socketio.on("start")
def start_download(data):
    url = data["url"]
    format_type = data["format"]
    task_id = str(uuid.uuid4())

    thread = threading.Thread(
        target=download_playlist,
        args=(url, format_type, task_id)
    )
    thread.start()

    emit("started", {"id": task_id})


def download_playlist(url, format_type, task_id):
    try:
        # Get playlist info
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", "playlist")
            entries = info.get("entries", [])
            total_videos = len(entries)

        safe_title = "".join(c for c in title if c.isalnum() or c in " _-")
        folder_id = str(uuid.uuid4())
        folder_path = os.path.join(BASE_DIR, safe_title + "_" + folder_id)
        os.makedirs(folder_path, exist_ok=True)

        socketio.emit("playlist_info", {
            "id": task_id,
            "title": safe_title,
            "total": total_videos
        })

        current_index = {"value": 0}

        def progress_hook(d):
            if d["status"] == "downloading":
                socketio.emit("progress", {
                    "id": task_id,
                    "progress": d.get("_percent_str", "0%"),
                    "current": current_index["value"],
                    "total": total_videos
                })

            elif d["status"] == "finished":
                current_index["value"] += 1
                socketio.emit("video_done", {
                    "id": task_id,
                    "current": current_index["value"],
                    "total": total_videos
                })

        ydl_opts = {
            "outtmpl": f"{folder_path}/%(title)s.%(ext)s",
            "progress_hooks": [progress_hook],
            "ignoreerrors": True,
            "concurrent_fragment_downloads": 5,
        }

        if format_type == "mp3":
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320"
                }]
            })
        else:
            ydl_opts.update({
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Create ZIP
        zip_name = f"{safe_title}.zip"
        zip_path = os.path.join(BASE_DIR, zip_name)

        with zipfile.ZipFile(zip_path, "w") as zipf:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    full = os.path.join(root, file)
                    zipf.write(full, file)

        # Remove raw folder immediately (keep only ZIP)
        shutil.rmtree(folder_path, ignore_errors=True)

        socketio.emit("complete", {
            "id": task_id,
            "zip": f"/download/{zip_name}"
        })

    except Exception as e:
        socketio.emit("error", {
            "id": task_id,
            "message": str(e)
        })


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=10000)
