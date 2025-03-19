import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum
import jsonFileManager
import asyncio
import datetime
import locale

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


async def _update_payoutlist(client: gspread_asyncio.AsyncioGspreadClientManager, spread_settings: jsonFileManager.JsonFileManager):
    """
    Update the payout list
    """
    # Open the worksheet
    auth = await client.authorize()
    spreadsheet_role_settings = await spread_settings.load()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    
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
        last_month = now - datetime.timedelta(days=now.day)
        last_month_name = last_month.strftime("%B")
        last_month_year = last_month.strftime("%Y")
        last_month_sheet = await worksheet.worksheet(f"Payoutliste {last_month_name} {last_month_year}")
        await last_month_sheet.copy_to(f"Payoutliste {current_month} {current_year}")

        # Get the new sheet
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")

    # Get the values
    A_col = await sheet.get_values("B1:B114", major_dimension="COLUMNS")
    A_col = A_col[0]

    # Delete first 9 entries
    A_col = A_col[COLUMN_START_OFFSET:]

    print(A_col)

    print(find_free_cell_in_column(A_col))
