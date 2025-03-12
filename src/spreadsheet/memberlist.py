import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum
import jsonFileManager

class Column(Enum):
    NAME = 'A'
    COMPANY = 'B'
    CLASS = 'C'

# Offset for the first 9 entries
OFFSET = 9

def find_free_cell_in_column(column: list):
    for i, cell in enumerate(column):
        if cell == "":
            return i + 1 + OFFSET
    return len(column) + 1 + OFFSET

async def update_member(client: gspread_asyncio.AsyncioGspreadClientManager, member: discord.Member, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):

    # check if member has a company role
    spreadsheet_role_settings = await spread_settings.load()
    if "document_id" not in spreadsheet_role_settings:
        return

    user_roles = member.roles
    user_role_ids = [role.id for role in user_roles]
    company = None
        
    for user_role_id in user_role_ids:
        if str(user_role_id) in spreadsheet_role_settings["company_role"]:
            company = spreadsheet_role_settings["company_role"][str(user_role_id)]
            break
    else:
        return

    # Parse the display name
    member_name = parse_display_name(member)

    # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
    member_name = member_name.split(" | ")[0]

    # Open the worksheet
    auth = await client.authorize()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    sheet = await worksheet.get_worksheet(0)

    # Get the column A
    A_col = await sheet.get_values("A1:A114", major_dimension="COLUMNS")
    A_col = A_col[0]

    # Delete first 9 entries
    A_col = A_col[OFFSET:]

    row_number = None
    # Check if member is already in the list
    if member_name in A_col:
        row_number = A_col.index(member_name) + 1 + OFFSET
    else:
        row_number = find_free_cell_in_column(A_col)
        # update cell NAME 
        c = await sheet.acell(f"{Column.NAME.value}{row_number}")
        c.value = member_name
        await sheet.update_cells([c], value_input_option=gspread.utils.ValueInputOption.raw)

    # update cell COMPANY
    await sheet.update_acell(f"{Column.COMPANY.value}{row_number}", company)

    # set class role
    for user_role_id in user_role_ids:
        if str(user_role_id) in spreadsheet_role_settings["class_role"]:
            class_role = spreadsheet_role_settings["class_role"][str(user_role_id)]
            await sheet.update_acell(f"{Column.CLASS.value}{row_number}", class_role)
            break
    else:
        await sheet.update_acell(f"{Column.CLASS.value}{row_number}", "Unbekannt")
