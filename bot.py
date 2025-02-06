import discord
from discord import app_commands
import yt_dlp
import os
import cv2
import pytesseract
import numpy as np
import re
import asyncio
import shutil
import time
from videoAnalyzer import VideoAnalyzer
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

async def edit_msg(interaction: discord.Interaction, msg_id: int, embed: discord.Embed):
    msg = await interaction.channel.fetch_message(msg_id)
    if msg:
        await msg.edit(embed=embed)

async def resend_ephemeral_message(interaction: discord.Interaction, msg_id: int, embed: discord.Embed):
    # Nachricht in einem internen Speicher (Datenbank, Dictionary) gespeichert
    try:
        await interaction.followup.delete_message(msg_id)  # Löscht die alte ephemere Nachricht
    except discord.NotFound:
        pass  # Falls die Nachricht nicht mehr existiert

    new_msg = await interaction.followup.send(embed=embed, ephemeral=True)  # Sende neue ephemere Nachricht
    return new_msg.id  # Speichere die neue Message-ID


async def download_video(youtube_url, interaction: discord.Interaction, msg_id: int):
    video_path = f"{DOWNLOAD_FOLDER}video.mp4"
    
    loop = asyncio.get_running_loop()

    def progress_hook(d):
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(report_progress(d, interaction, msg_id), loop)
            try:
                future.result()
            except Exception as e:
                print(f"Error in progress_hook: {e}")

    ydl_opts = {
        "outtmpl": video_path,
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mp4',
        # 'progress_hooks': [progress_hook],
        # 'proxy': 'socks5://tor_proxy:9050'
    }
    
    await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).download([youtube_url]))
    return video_path

async def report_progress(d, interaction: discord.Interaction, msg_id: int):
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
                await edit_msg(interaction, msg_id, embed)

async def send_images(interaction: discord.Interaction, folder_path: str):
    """Sendet alle Bilder aus einem Ordner als ephemere Nachrichten an den User."""
    files = [f for f in os.listdir(folder_path) if f.endswith(('.png', '.jpg', '.jpeg', '.gif'))]
    
    if not files:
        await interaction.followup.send("Keine Bilder im Ordner gefunden.", ephemeral=True)
        return

    for file in files:
        file_path = os.path.join(folder_path, file)
        with open(file_path, "rb") as img:
            await interaction.followup.send(file=discord.File(img, filename=file), ephemeral=True)

def format_time(seconds):
    """Wandelt Sekunden in ein MM:SS Format um."""
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes:02}:{seconds:02}"

@tree.command(name="stamina_check", description="Analysiert ein YouTube-Video auf Stamina-Null-Zustände.")
async def stamina_check(interaction: discord.Interaction, youtube_url: str, debug_mode: bool = False):
    stamina_queue.append(interaction)
    position = len(stamina_queue)

    base_msg = discord.Embed(
        title="🏁 Stamina Check startet gleich!",
        description="🔍 Bereite alles vor...\n\n⚙️ **Warteschlange wird organisiert...**\n\n🕐 Bitte habe etwas Geduld!",
        color=discord.Color.blue()
    )

    embed = discord.Embed(
        title="⏳ Warteschlange",
        description=f"Du bist auf Platz {position} in der Warteschlange.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=base_msg, ephemeral=True)
    await asyncio.sleep(3)
    msg = await interaction.channel.send(embed=embed)

    while stamina_queue[0] != interaction:
        await asyncio.sleep(10)
        new_position = stamina_queue.index(interaction) + 1
        embed.description = f"Du bist jetzt auf Platz {new_position} in der Warteschlange."
        await edit_msg(interaction, msg.id, embed)

    async with stamina_lock:
        stamina_queue.popleft()
        try:
            os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
            os.makedirs(OUTPUT_FOLDER, exist_ok=True)

            embed.title = "📥 Video-Download"
            embed.description = "Lade Video herunter..."
            await edit_msg(interaction, msg.id, embed)

            time_start_download = time.time()
            video_path = await download_video(youtube_url, interaction, msg.id)
            time_end_download = time.time()

            
            video_analyzer = VideoAnalyzer(video_path, debug=debug_mode)
            training_frame_count = min(15000, video_analyzer.frame_count)

            embed.title = "🚀 Training läuft"
            embed.description = f"Trainiere Algorythmus mit {training_frame_count} von {video_analyzer.frame_count} Frames..."
            await edit_msg(interaction, msg.id, embed)

            time_start_training = time.time()
            stable_rectangle = await video_analyzer.find_stable_rectangle(training_frame_count)
            time_end_training = time.time()

            if debug_mode:
                embed.title = "Stabiles Rechteck"
                embed.description = f"Gefunden auf: {stable_rectangle}"
                await interaction.followup.send(embed=embed, ephemeral=True)

            embed.title = "🔍 Analyse läuft"
            embed.description = f"Analysiere {video_analyzer.frame_count} Frames..."
            await edit_msg(interaction, msg.id, embed)

            time_start_analyze = time.time()
            timestamps = await video_analyzer.analyze_video(stable_rectangle)
            time_end_analyze = time.time()

            embed.title = "✅ Analyse abgeschlossen!"
            t_info = f"**Verbrauche Zeit:** {format_time(time_end_analyze - time_start_download)}\n- Download: {format_time(time_end_download - time_start_download)}\n- Training: {format_time(time_end_training - time_start_training)}\n- Analyse: {format_time(time_end_analyze - time_start_analyze)}"
            embed.description = f"{t_info}\n\n⏱ **Gefundene Zeitstempel:**\n" + "\n".join(f"{idx+1}. {ts}" for idx, ts in enumerate(timestamps))
            embed.color = discord.Color.green()
            await edit_msg(interaction, msg.id, embed)
        except Exception as e:
            embed.title = "❌ Fehler"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await edit_msg(interaction, msg.id, embed)

        if debug_mode:
            await send_images(interaction, OUTPUT_FOLDER)

        shutil.rmtree(DOWNLOAD_FOLDER)
        shutil.rmtree(OUTPUT_FOLDER)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ {bot.user} ist online und bereit!")

bot.run(DISCORD_TOKEN)
