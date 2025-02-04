import discord
from discord import app_commands
import yt_dlp
import os
import cv2
import pytesseract
import numpy as np
import re
import asyncio
from collections import deque

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

DISCORD_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_FOLDER = "./downloads/"
OUTPUT_FOLDER = "./output/"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

stamina_lock = asyncio.Lock()
stamina_queue = deque()

async def download_video(youtube_url, interaction: discord.Interaction):
    video_path = f"{DOWNLOAD_FOLDER}video.mp4"
    
    # Get the running event loop to use later in the thread
    loop = asyncio.get_running_loop()

    def progress_hook(d):
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(report_progress(d, interaction), loop)
            try:
                future.result()  # Ensure any errors in the coroutine get raised
            except Exception as e:
                print(f"Error in progress_hook: {e}")

    ydl_opts = {
        "outtmpl": video_path,
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mp4',
        'progress_hooks': [progress_hook],  # Correctly handles async in a sync function
        'proxy': 'socks5://tor_proxy:9050'
    }
    
    await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).download([youtube_url]))
    return video_path

async def report_progress(d, interaction: discord.Interaction):
    if d['status'] == 'downloading':
        downloaded = d.get('downloaded_bytes', 0)
        total = d.get('total_bytes', d.get('total_bytes_estimate', 0))
        if total > 0:
            percent = downloaded / total * 100
            if int(percent) % 5 == 0:
                embed = discord.Embed(
                    title="📥 Video-Download",
                    description=f"Fortschritt: {percent:.2f}% ({downloaded / 1_048_576:.2f} MB / {total / 1_048_576:.2f} MB)",
                    color=discord.Color.blue()
                )
                await interaction.edit_original_response(embed=embed)

async def analyze_stamina(video_path, interaction: discord.Interaction):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return "Fehler: Video konnte nicht geladen werden!"

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    frames_to_skip = fps * 3
    skip_until_frame = 0

    zero_stamina_count = 0
    frame_count = 0
    consecutive_zero_frames = 0
    required_zero_frames = 1

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        if frame_count < skip_until_frame:
            continue

        if frame_count % 20 == 0:
            percent_done = (frame_count / total_frames) * 100
            embed = discord.Embed(title="🔍 Analyse läuft", description=f"{frame_count}/{total_frames} Frames verarbeitet ({percent_done:.2f}%)", color=discord.Color.blue())
            await interaction.edit_original_response(embed=embed)

        height, width, _ = frame.shape
        roi_x1, roi_x2 = int(width * 0.495), int(width * 0.505)
        roi_y1, roi_y2 = int(height * 0.91), int(height * 0.93)
        roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        black_pixels = np.sum(gray == 0)
        total_pixels = gray.size
        black_ratio = black_pixels / total_pixels
        
        if black_ratio >= 0.25:
            is_zero_stamina = False
        else:
            custom_config = "--psm 10 digits"
            stamina_text = pytesseract.image_to_string(gray, config=custom_config).strip()
            is_zero_stamina = re.fullmatch(r"0", stamina_text) is not None

        if is_zero_stamina:
            consecutive_zero_frames += 1
        else:
            consecutive_zero_frames = 0

        if consecutive_zero_frames == required_zero_frames:
            zero_stamina_count += 1
            skip_until_frame = frame_count + frames_to_skip
    
    cap.release()
    os.remove(video_path)
    return f"Stamina auf 0 gefallen: {zero_stamina_count} Mal"

@tree.command(name="stamina_check", description="Analysiert ein YouTube-Video auf Stamina-Null-Zustände.")
async def stamina_check(interaction: discord.Interaction, youtube_url: str):
    stamina_queue.append(interaction)
    position = len(stamina_queue)

    embed = discord.Embed(title="⏳ Warteschlange", description=f"Du bist auf Platz {position} in der Warteschlange.", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)
    
    while stamina_queue[0] != interaction:
        await asyncio.sleep(1)
        new_position = stamina_queue.index(interaction) + 1
        embed.description = f"Du bist jetzt auf Platz {new_position} in der Warteschlange."
        await interaction.edit_original_response(embed=embed)
    
    async with stamina_lock:
        stamina_queue.popleft()
        try:
            embed.title = "📥 Video-Download"
            embed.description = "Lade Video herunter..."
            await interaction.edit_original_response(embed=embed)
            video_path = await download_video(youtube_url, interaction)

            embed.title = "🔍 Analyse läuft"
            embed.description = "Analysiere Stamina-Status..."
            await interaction.edit_original_response(embed=embed)
            result = await analyze_stamina(video_path, interaction)

            embed.title = "✅ Analyse abgeschlossen!"
            embed.description = result
            embed.color = discord.Color.green()
            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            embed.title = "❌ Fehler"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await interaction.edit_original_response(embed=embed)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ {bot.user} ist online und bereit!")

bot.run(DISCORD_TOKEN)
