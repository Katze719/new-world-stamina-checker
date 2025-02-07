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
import json
from videoAnalyzer import VideoAnalyzer
from collections import deque
from logger import logger as log

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

DISCORD_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_FOLDER = "./downloads/"
OUTPUT_FOLDER = "./output/"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

intents = discord.Intents.default()
intents.message_content = True
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


async def download_video(youtube_url):
    video_path = f"{DOWNLOAD_FOLDER}video.mp4"
    
    ydl_opts = {
        "outtmpl": video_path,
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mp4',
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

# Datei, in der die Kanäle gespeichert werden
VOD_CHANNELS_FILE_PATH = "./vod_channels.json"
vod_channels_file_lock = asyncio.Lock()

def load_channels():
    if not os.path.exists(VOD_CHANNELS_FILE_PATH):
        return []
    with open(VOD_CHANNELS_FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

async def save_channels(channels):
    with open(VOD_CHANNELS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(channels, f, indent=4)

@tree.command(name="add_this_channel", description="Füge diesen Channel zur VOD-Prüfliste hinzu")
async def add_this_channel(interaction: discord.Interaction):
    async with vod_channels_file_lock:
        channels = load_channels()
        channel_id = interaction.channel.id
    
        if channel_id in channels:
            await interaction.response.send_message("Dieser Channel ist bereits in der VOD-Prüfliste.", ephemeral=True)
            return
    
        channels.append(channel_id)
        await save_channels(channels)
        await interaction.response.send_message("Channel wurde erfolgreich zur VOD-Prüfliste hinzugefügt!", ephemeral=True)

@tree.command(name="remove_this_channel", description="Entferne diesen Channel von der VOD-Prüfliste")
async def remove_this_channel(interaction: discord.Interaction):
    async with vod_channels_file_lock:
        channels = load_channels()
        channel_id = interaction.channel.id
    
        if channel_id not in channels:
            await interaction.response.send_message("Dieser Channel ist nicht in der VOD-Prüfliste.", ephemeral=True)
            return
    
        channels.remove(channel_id)
        await save_channels(channels)
        await interaction.response.send_message("Channel wurde erfolgreich von der VOD-Prüfliste entfernt!", ephemeral=True)

@tree.command(name="stamina_check", description="Analysiert ein YouTube-Video auf Stamina-Null-Zustände.")
async def stamina_check(interaction: discord.Interaction, youtube_url: str, debug_mode: bool = False):
    stamina_queue.append(interaction.id)
    position = len(stamina_queue)

    log.info(f"Neue anfrage von {interaction.user.display_name}, warteschlange ist {len(stamina_queue)}")

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
    
    msg = await interaction.followup.send(embed=embed, wait=True)

    while stamina_queue[0] != interaction.id:
        await asyncio.sleep(10)
        new_position = stamina_queue.index(interaction.id) + 1
        embed.description = f"Du bist jetzt auf Platz {new_position} in der Warteschlange."
        await edit_msg(interaction, msg.id, embed)

    embed.description = "Du bist als nächstes drann."
    await edit_msg(interaction, msg.id, embed)

    async with stamina_lock:
        stamina_queue.popleft()
        try:
            log.info(f"Geht los für {interaction.user.display_name}")

            shutil.rmtree(DOWNLOAD_FOLDER)
            shutil.rmtree(OUTPUT_FOLDER)
            os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
            os.makedirs(OUTPUT_FOLDER, exist_ok=True)

            embed.title = "📥 Video-Download"
            embed.description = "Lade Video herunter..."
            await edit_msg(interaction, msg.id, embed)

            log.info("Starte Download")
            time_start_download = time.time()
            video_path = await download_video(youtube_url)
            time_end_download = time.time()

            
            video_analyzer = VideoAnalyzer(video_path, debug=debug_mode)
            training_frame_count = min(15000, video_analyzer.frame_count)

            embed.title = "🚀 Training läuft"
            embed.description = f"Trainiere Algorythmus mit {training_frame_count} von {video_analyzer.frame_count} Frames..."
            await edit_msg(interaction, msg.id, embed)

            log.info("Starte Training")
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

            async def send_progress_update(processed: int, total: int):
                embed = discord.Embed(
                    title="🔍 Analyse läuft...",
                    description=f"Fortschritt: {processed} von {total} Frames analysiert.",
                    color=discord.Color.blue()
                )
                log.info(f"Fortschritt: {processed} von {total} Frames analysiert.")
                await edit_msg(interaction, msg.id, embed)

            log.info("Starte Analyse")
            time_start_analyze = time.time()
            timestamps = await video_analyzer.analyze_video(stable_rectangle, send_progress_update)
            time_end_analyze = time.time()

            if len(timestamps) > 10:
                message = "Bitte noch etwas an deinem Staminamanagement arbeiten!"
            else:
                message = "Wow! weiter so, dein Staminamangement ist göttlich!"

            embed.title = "✅ Analyse abgeschlossen!"

            if debug_mode:
                t_info = f"**Verbrauche Zeit:** {format_time(time_end_analyze - time_start_download)}\n- Download: {format_time(time_end_download - time_start_download)}\n- Training: {format_time(time_end_training - time_start_training)}\n- Analyse: {format_time(time_end_analyze - time_start_analyze)}\n\n"
            else:
                t_info = ""
            embed.description = f"{t_info}⏱ **An Folgenden Stellen bist du Out Of Stamina:**\n{message}\n"

            # Liste für die drei Gruppen
            fields = ["", "", ""]
            # Alle Timestamps durchgehen und in die passende Gruppe einordnen
            for index, timestamp in enumerate(timestamps, start=1):
                group_index = (index - 1) // (len(timestamps) // 3)  # Bestimmt die Gruppe (0, 1 oder 2)
                group_index = min(group_index, 2)  # Falls `remaining_items` existiert, Begrenzung auf max. 2
                fields[group_index] += f"**#{index}.** {timestamp}\n"

            # Felder zum Embed hinzufügen
            for field_content in fields:
                embed.add_field(name="", value=field_content, inline=True)

            embed.color = discord.Color.green()
            await edit_msg(interaction, msg.id, embed)
        except Exception as e:
            embed.title = "❌ Fehler"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await edit_msg(interaction, msg.id, embed)

        if debug_mode:
            await send_images(interaction, OUTPUT_FOLDER)

        log.info(f"Anfrage Fertig von {interaction.user.display_name}")

@bot.event
async def on_ready():
    await tree.sync()
    log.info(f"✅ {bot.user} ist online und bereit!")

# YouTube-Link-Erkennung
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    channels = load_channels()
    if message.channel.id not in channels:
        return
    
    match = YOUTUBE_REGEX.search(message.content)
    if match:
        stamina_queue.append(message.id)

        embed = discord.Embed()
        embed.title = f"Neues VOD von {message.author.display_name}"
        embed.description = f"{message.content}\n\nJump: {message.jump_url}"
        embed.color = discord.Color.blue()

        channel = bot.get_channel(1337499488272519299)
        if channel:
            await channel.send(embed=embed)

        await message.add_reaction("⏳")

        while stamina_queue[0] != message.id:
            await asyncio.sleep(10)

        async with stamina_lock:
            log.info(f"Bearbeite VOD hidden for {message.author.display_name}")
            video_path = await download_video(message.content)
            video_analyzer = VideoAnalyzer(video_path)
            stable_rectangle = await video_analyzer.find_stable_rectangle(15000)

            async def send_progress_update(processed: int, total: int):
                log.info(f"Fortschritt: {processed} von {total} Frames analysiert.")

            timestamps = await video_analyzer.analyze_video(stable_rectangle, send_progress_update)
            if len(timestamps) > 10:
                mot_message = "Bitte noch etwas an deinem Staminamanagement arbeiten!"
            else:
                mot_message = "Wow! weiter so, dein Staminamangement ist göttlich!"

            embed = discord.Embed()
            embed.title = "✅ Analyse abgeschlossen!"
            embed.description = f"⏱ **An Folgenden Stellen bist du Out Of Stamina:**\n{mot_message}\n"
            embed.color = discord.Color.green()

            # Liste für die drei Gruppen
            fields = ["", "", ""]
            # Alle Timestamps durchgehen und in die passende Gruppe einordnen
            for index, timestamp in enumerate(timestamps, start=1):
                group_index = (index - 1) // (len(timestamps) // 3)  # Bestimmt die Gruppe (0, 1 oder 2)
                group_index = min(group_index, 2)  # Falls `remaining_items` existiert, Begrenzung auf max. 2
                fields[group_index] += f"**#{index}.** {timestamp}\n"

            # Felder zum Embed hinzufügen
            for field_content in fields:
                embed.add_field(name="", value=field_content, inline=True)

            await message.channel.send(embed=embed)

            await message.remove_reaction("⏳", bot.user)
            await message.add_reaction("✅")

bot.run(DISCORD_TOKEN)
