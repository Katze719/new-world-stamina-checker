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
import json

# Setze die Locale auf Deutsch
locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")

spreadsheet_payout_update = asyncio.Lock()

class Column(Enum):
    COMPANY = 'E'
    NAME = 'F'
    START = 'L'

# Offset for the first 6 entries
COLUMN_START_OFFSET = 6
ROW_START_OFFSET = 4


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
    
    # check if the worksheet exists if not copy the one from the last month
    try:
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
    except gspread.exceptions.WorksheetNotFound:
        # Copy the last month sheet
        print("patch")
        last_month = now - datetime.timedelta(days=now.day)
        last_month_name = last_month.strftime("%B")
        last_month_year = last_month.strftime("%Y")
        last_month_sheet = await worksheet.worksheet(f"Payoutliste {last_month_name} {last_month_year}")
        await worksheet.duplicate_sheet(last_month_sheet.id, insert_sheet_index=3, new_sheet_name=f"Payoutliste {current_month} {current_year}")
        await last_month_sheet.update_tab_color("#ffffff")

        # Get the new sheet
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
        await sheet.batch_clear([f"{Column.START.value}7:AAA300"])

        # Get the values
        A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
        A_col = A_col[0]
        A_col = A_col[COLUMN_START_OFFSET:]

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
        member_list = [full_parse(member) for member in channel_race.guild.members]

        for member in A_col:
            if member.replace(" ", "") not in member_list:
                # get the row of the member
                row = A_col.index(member) + 1 + COLUMN_START_OFFSET
                await sheet.delete_rows(row)
                print(f"Deleted {member} from payout list")


    data = await raidhelper_id_manager.load()

    mesage_ids = data.get("raidhelper_message_ids")
    if not mesage_ids:
        data["raidhelper_message_ids"] = []

    for channel, type in [(channel_race, "Push"), (channel_war, "Krieg")]:
        print("Start")
        async for message in channel.history(limit=20):
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
                                usernames = []
                                print(embed.to_dict())
                                json.dump(embed.to_dict(), open("embed.json", "w"))
                        else:
                            pass
                    else:
                        print("Kein Datum gefunden.")

    await raidhelper_id_manager.save(data)


    # Delete first 9 entries

    # print(A_col)

    # print(find_free_cell_in_column(A_col))
