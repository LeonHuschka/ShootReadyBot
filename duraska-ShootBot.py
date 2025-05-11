import os
import sys
import glob
import logging
import datetime
import asyncio
import subprocess

import httpx
from telegram import InputFile, Update
from telegram.error import RetryAfter
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

import yt_dlp
import instaloader

# === KONFIGURATION ===
TOKEN = '7945597113:AAELc2WQQ7tssYtysuE-nLhudhBCPbrc54U'  # <-- hier deinen Bot-Token einfügen
DOWNLOAD_FOLDER = './downloads'
SESSION_PATH = "./session-leonbusinessresearch"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# === HTTPX CLIENT ===
class CustomHTTPXRequest(HTTPXRequest):
    def __init__(self):
        super().__init__()
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=5),
            timeout=httpx.Timeout(120.0)
        )

    async def close(self):
        await self._client.aclose()

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === BOT-LOGIK ===
class DuraskaBot:
    def download_instagram_video(self, link, folder_path, date, timestamp):
        formatted_date = date.replace('.', '_')
        filename = f"t_{formatted_date}_{timestamp}.mp4"
        output_path = os.path.join(folder_path, filename)

        ydl_opts = {
            'outtmpl': output_path,
            'format': 'mp4',
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
            return output_path

        except Exception as e:
            logging.warning(f"yt-dlp Fehler: {e}")

            try:
                shortcode = link.split('/')[-2]
                L = instaloader.Instaloader(
                    dirname_pattern=folder_path,
                    filename_pattern=f"t_{formatted_date}_{timestamp}"
                )
                L.download_comments = False
                L.download_geotags = False
                L.download_usertags = False
                L.save_metadata = False
                L.download_video_thumbnails = False

                L.load_session_from_file('session-leonbusinessresearch', SESSION_PATH)
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                L.download_post(post, target=folder_path)

                for f in glob.glob(os.path.join(folder_path, "*.txt")):
                    os.remove(f)

                for f in glob.glob(os.path.join(folder_path, "*.mp4")):
                    return f

            except Exception as e2:
                logging.error(f"Instaloader Fehler: {e2}")
                return None

def append_10s_silence_ffmpeg(input_path):
    output_path = input_path.replace(".mp4", "_extended.mp4")
    command = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-f", "lavfi",
        "-t", "10",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-f", "lavfi",
        "-t", "10",
        "-i", "color=black:s=1280x720",
        "-filter_complex", "[0:v][0:a][2:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-c:a", "aac",
        output_path
    ]

    try:
        subprocess.run(command, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error(f"ffmpeg Fehler: {e}")
        return None

async def send_telegram_video(bot, chat_id, file_path, caption=None, message_thread_id=None):
    max_retries = 2
    timeout_duration = 20

    for attempt in range(max_retries):
        try:
            with open(file_path, "rb") as file:
                await asyncio.sleep(0.2)
                send_task = asyncio.create_task(
                    bot.send_video(
                        chat_id=chat_id,
                        video=InputFile(file),
                        caption=caption,
                        message_thread_id=message_thread_id,
                        disable_notification=True
                    )
                )
                await asyncio.wait_for(send_task, timeout=timeout_duration)
                logging.info(f"Video erfolgreich gesendet.")
                return

        except asyncio.TimeoutError:
            logging.error(f"Timeout beim Senden: {file_path}")
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                logging.warning("Task wurde abgebrochen.")

        except RetryAfter as e:
            logging.warning(f"Flood control – warte {e.retry_after} Sekunden.")
            await asyncio.sleep(e.retry_after)

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logging.error(f"Netzwerkfehler: {e}")

        if attempt < max_retries - 1:
            logging.info("Neuer Sendeversuch...")
            await asyncio.sleep(2)
        else:
            logging.error("Maximale Anzahl an Versuchen erreicht.")

# === NACHRICHTEN-HANDLER ===
duraska = DuraskaBot()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat_id = message.chat_id
    thread_id = message.message_thread_id
    user = message.from_user.first_name if message.from_user else "Unbekannt"
    text = message.text or ""

    logging.info(f"📨 Nachricht von {user} | Chat-ID: {chat_id} | Thread-ID: {thread_id} | Inhalt: {text}")

    # Prüfen, ob Link enthalten ist
    if "instagram.com" in text or "tiktok.com" in text:
        await message.reply_text("📥 Lade Video herunter…")

        now = datetime.datetime.now()
        date = now.strftime("%d.%m.%Y")
        timestamp = now.strftime("%H%M%S")

        raw_video_path = duraska.download_instagram_video(text, DOWNLOAD_FOLDER, date, timestamp)

        if not raw_video_path:
            await message.reply_text("❌ Download fehlgeschlagen.")
            return

        extended_video_path = append_10s_silence_ffmpeg(raw_video_path)

        if not extended_video_path:
            await message.reply_text("⚠️ Fehler beim Verarbeiten des Videos.")
            return

        await send_telegram_video(
            bot=context.bot,
            chat_id=chat_id,
            file_path=extended_video_path,
            caption="🎬 Inspo-Clip – Capture Ready!",
            message_thread_id=thread_id
        )

# === BOT START ===
async def main():
    request = CustomHTTPXRequest()
    app = Application.builder().token(TOKEN).request(request).build()

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    logging.info("🤖 Duraska Bot läuft...")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    asyncio.get_event_loop().run_until_complete(main())