import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum

import jsonFileManager
import asyncio
import datetime
import locale
import re
import requests
import json
import traceback
import io

EDITOR_MAILS = ["bot-300@black-beach-453214-f6.iam.gserviceaccount.com"]

# Setze die Locale auf Deutsch
locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")

spreadsheet_payout_update = asyncio.Lock()

class Column(Enum):
    COMPANY = 'E'
    NAME = 'F'
    START = 'L'

# Offset for the first 6 entries
COLUMN_START_OFFSET = 6
ROW_START_OFFSET = 11


raidhelper_id_manager = jsonFileManager.JsonFileManager("./raidhelper_id.json")



def find_free_cell_in_column(column: list):
    for i, cell in enumerate(column):
        if cell == "":
            return i + 1 + COLUMN_START_OFFSET
    return len(column) + 1 + COLUMN_START_OFFSET

def find_free_cell_in_row(row: list):
    for i, cell in enumerate(row):
        if cell == "":
            return i + 1 + ROW_START_OFFSET
    return len(row) + 1 + ROW_START_OFFSET


async def _update_payoutlist(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager, gp_channel_manager: jsonFileManager.JsonFileManager):
    """
    Update the payout list
    """
    # Open the worksheet
    auth = await client.authorize()
    spreadsheet_role_settings = await spread_settings.load()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    
    channels = await gp_channel_manager.load()
    channel_race = bot.get_channel(channels.get("raidhelper_race"))
    channel_war = bot.get_channel(channels.get("raidhelper_war"))

    if not channel_race or not channel_war:
        return

    # get current month name in german
    now = datetime.datetime.now()

    # Formatieren: Monat und Jahr
    current_month = now.strftime("%B")  # Vollständiger Monatsname
    current_year = now.strftime("%Y")   # Jahr
    
    def get_company(member: discord.Member):
        user_roles_ = member.roles
        user_role_ids_ = [role.id for role in user_roles_]
        company = None
            
        for user_role_id in user_role_ids_:
            if str(user_role_id) in spreadsheet_role_settings["company_role"]:
                company = spreadsheet_role_settings["company_role"][str(user_role_id)]
                break

        return company

    def full_parse(member):
        # Parse the display name
        member_name = parse_display_name(member)

        # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
        member_name = member_name.split(" | ")[0]
        member_name = member_name.split(" I ")[0]
        member_name = member_name.replace("🏮 ", "")
        member_name = member_name.replace("🏮", "")
        member_name = member_name.replace(" ", "")
        return member_name

    # check if users from sheet are in in discord
    discord_memeber_list = [(full_parse(member), get_company(member)) for member in channel_race.guild.members if get_company(member) is not None]

    # check if the worksheet exists if not copy the one from the last month
    try:
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
    except gspread.exceptions.WorksheetNotFound:
        # Copy the last month sheet
        last_month = now - datetime.timedelta(days=now.day)
        last_month_name = last_month.strftime("%B")
        last_month_year = last_month.strftime("%Y")
        last_month_sheet = await worksheet.worksheet(f"Payoutliste {last_month_name} {last_month_year}")
        await worksheet.duplicate_sheet(last_month_sheet.id, insert_sheet_index=3, new_sheet_name=f"Payoutliste {current_month} {current_year}")
        await last_month_sheet.update_tab_color("#ffffff")

        # Get the new sheet
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
        await sheet.add_protected_range("A1:AAA3000", EDITOR_MAILS)
        await sheet.batch_clear([f"{Column.START.value}7:AAA300", "L2:AAA4"])

        # protect new created sheet so only specific users can edit the sheet

        # Get the values
        A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
        A_col = A_col[0]
        A_col = A_col[COLUMN_START_OFFSET:]

        for member in A_col:
            if member.replace(" ", "") not in [mem[0] for mem in discord_memeber_list]:
                # get the row of the member
                row = A_col.index(member) + 1 + COLUMN_START_OFFSET
                await sheet.batch_clear([f"A{row}:AAA{row}"])

    data = await raidhelper_id_manager.load()

    mesage_ids = data.get("raidhelper_message_ids")
    if not mesage_ids:
        data["raidhelper_message_ids"] = []

    for channel, type in [(channel_race, "Push"), (channel_war, "Krieg")]:
        async for message in channel.history(limit=20):
            raidhelper_usernames = []
            column_event = None
            if message.embeds:
                embed = message.embeds[0]
                if embed.fields:
                    # print(embed.fields[0].value)
                    embed_field_value = embed.fields[0].value
                    match = re.search(r"<t:(\d+):", embed_field_value)
                    if match:
                        timestamp = int(match.group(1))
                        # Konvertiere den Timestamp in ein datetime-Objekt (UTC)
                        dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                        now = datetime.datetime.now(tz=datetime.timezone.utc)
                        
                        if now > dt:
                            if message.id not in data["raidhelper_message_ids"]:
                                data["raidhelper_message_ids"].append(message.id)

                                # read usernames and save in list
                                for field in embed.fields:
                                    match = re.search(r'\[Web View\]\((https://raid-helper\.dev/event/(\d+))\)', field.value)
                                    if match:
                                        event_id = match.group(2)
                                        api_url = f"https://raid-helper.dev/api/v2/events/{event_id}"
                                        response = requests.get(api_url)
                                        json_data = response.json()
                                        for signup in json_data["signUps"]:
                                            raidhelper_usernames.append(signup["name"])

                                events = await sheet.get_values("L3:AAA3", major_dimension="ROWS")
                                events = events[0]
                                column_event = find_free_cell_in_row(events)
                                await sheet.update_cell(3, column_event, "Raidhelper")
                                await sheet.update_cell(4, column_event, f"{type} {dt.strftime('%d.%m.')}")
                        else:
                            pass

            if raidhelper_usernames and column_event:
                # Get the column A
                A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
                A_col = A_col[0]
                A_col = A_col[COLUMN_START_OFFSET:]

                for discord_username, company in discord_memeber_list:
                    row = find_free_cell_in_column(A_col)
                    if any(discord_username in username for username in raidhelper_usernames):
                        if discord_username in A_col:
                            row = A_col.index(discord_username)
                            await sheet.update_cell(row + 1 + COLUMN_START_OFFSET, column_event, "1")
                        else:  
                            await sheet.update_cell(row, column_event, "1")
                    if discord_username not in A_col:
                        await sheet.update_cell(row, 6, discord_username)
                        # stats
                        await sheet.update_cell(row, 1, f'=(ZÄHLENWENNS($L$4:$AAA$4; "*Push*";$L$3:$AAA$3;"*Teilnehmer*";L{row}:AAA{row}; ">0") + ZÄHLENWENNS($L$4:$AAA$4; "*Push*";$L$3:$AAA$3;"*Teilnehmer*";L{row}:AAA{row}; "x"))/$A$5')
                        await sheet.update_cell(row, 2, f'=(ZÄHLENWENNS($L$4:$AAA$4; "*Krieg*";$L$3:$AAA$3;"*Teilnehmer*";L{row}:AAA{row}; ">0") + ZÄHLENWENNS($L$4:$AAA$4; "*Krieg*";$L$3:$AAA$3;"*Teilnehmer*";L{row}:AAA{row}; "x"))/$B$5')
                        await sheet.update_cell(row, 3, f'=(ZÄHLENWENNS($L$3:$AAA$3;"*Raidhelper*";L{row}:AAA{row}; ">0") + ZÄHLENWENNS($L$3:$AAA$3;"*Raidhelper*";L{row}:AAA{row}; "x"))/$C$5')
                        await sheet.update_cell(row, 4, f'=(ZÄHLENWENNS($L$3:$AAA$3;"*VOD*";L{row}:AAA{row}; ">0") + ZÄHLENWENNS($L$3:$AAA$3;"*VOD*";L{row}:AAA{row}; "x"))/$D$5')
                        await sheet.update_cell(row, 5, company)
                        await sheet.update_cell(row, 9, f'=SUMME(K{row}:AAA{row})')
                        await sheet.update_cell(row, 10, f'=I{row}*$J$3')
                        if row - 1 - COLUMN_START_OFFSET < len(A_col):
                            A_col[row - 1 - COLUMN_START_OFFSET] = discord_username
                        else:
                            A_col.append(discord_username)
            await raidhelper_id_manager.save(data)

async def update_payoutlist(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager, gp_channel_manager: jsonFileManager.JsonFileManager):
    async with spreadsheet_payout_update:
        try:
            await _update_payoutlist(bot, client, parse_display_name, spread_settings, gp_channel_manager)
        except gspread.exceptions.APIError as e:
            channel = await gp_channel_manager.load()
            error_channel = bot.get_channel(channel.get("error_log_channel", 0))
            if error_channel:
                stacktrace = traceback.format_exc()
                
                # Speichere den Stacktrace in einem in-memory String-Stream
                file_obj = io.StringIO(stacktrace)
                
                # Erstelle ein discord.File-Objekt, das als "stacktrace.txt" gesendet wird
                discord_file = discord.File(fp=file_obj, filename="stacktrace.txt")
                
                # Sende die Nachricht mit der angehängten Datei
                await error_channel.send(content=f"# {__file__}\n```{json.dumps(e.response.json(), indent=4)}```", file=discord_file)