import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum
import jsonFileManager
import asyncio
from logger import log


spreadsheet_member_update = asyncio.Lock()

class Column(Enum):
    NAME = 'A'
    COMPANY = 'B'
    CLASS = 'C'
    KUEKEN = 'F'

# Offset for the first 9 entries
OFFSET = 9

def find_free_cell_in_column(column: list):
    for i, cell in enumerate(column):
        if cell == "":
            return i + 1 + OFFSET
    return len(column) + 1 + OFFSET

AUSNAHMEN = ["Gernhart Reinda", "krabby", "XALegend"]

async def _update_member(client: gspread_asyncio.AsyncioGspreadClientManager, member: discord.Member, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):

    # check if member has a company role
    spreadsheet_role_settings = await spread_settings.load()
    if "document_id" not in spreadsheet_role_settings:
        return

    user_roles = member.roles
    user_role_ids = [role.id for role in user_roles]

    def get_company(member: discord.Member):
        user_roles_ = member.roles
        user_role_ids_ = [role.id for role in user_roles_]
        company = None
            
        for user_role_id in user_role_ids_:
            if str(user_role_id) in spreadsheet_role_settings["company_role"]:
                company = spreadsheet_role_settings["company_role"][str(user_role_id)]
                break

        return company
    
    company = get_company(member)
    if company is None:
        return

    def full_parse(member):
        # Parse the display name
        member_name = parse_display_name(member)

        # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
        member_name = member_name.split(" | ")[0]
        member_name = member_name.split(" I ")[0]
        member_name = member_name.replace("🏮 ", "")
        member_name = member_name.replace("🏮", "")
        return member_name
    
    member_name = full_parse(member)
    
    if member_name in AUSNAHMEN:
        return

    # Open the worksheet
    auth = await client.authorize()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    sheet = await worksheet.worksheet("Memberliste")

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

    # Prepare batch update
    batch_update = []

    # Update cell NAME
    batch_update.append({
        'range': f"{Column.NAME.value}{row_number}",
        'values': [[member_name]]
    })

    # Update cell COMPANY
    batch_update.append({
        'range': f"{Column.COMPANY.value}{row_number}",
        'values': [[company]]
    })

    # Set class role
    class_role = "Unbekannt"
    for user_role_id in user_role_ids:
        if str(user_role_id) in spreadsheet_role_settings["class_role"]:
            class_role = spreadsheet_role_settings["class_role"][str(user_role_id)]
            break

    batch_update.append({
        'range': f"{Column.CLASS.value}{row_number}",
        'values': [[class_role]]
    })

    # Set küken role
    kueken_role = ""
    if "kueken_role" in spreadsheet_role_settings:
        for user_role_id in user_role_ids:
            if str(user_role_id) in spreadsheet_role_settings["kueken_role"]:
                kueken_role = spreadsheet_role_settings["kueken_role"][str(user_role_id)]
                break
    
    batch_update.append({
        'range': f"{Column.KUEKEN.value}{row_number}",
        'values': [[kueken_role]]
    })

    # Execute batch update
    await sheet.batch_update(batch_update, value_input_option=gspread.utils.ValueInputOption.raw)

    # Delete users from list that are not in discord
    member_list = [full_parse(member) for member in member.guild.members if get_company(member) is not None or 
                   any([str(role.id) in spreadsheet_role_settings.get("class_role", {}) for role in member.roles]) or
                   any([str(role.id) in spreadsheet_role_settings.get("kueken_role", {}) for role in member.roles])]
    delete_batch_update = []
    for i, cell in enumerate(A_col):
        if cell and cell not in member_list:
            for j in range(12):
                delete_batch_update.append({
                    'range': f"{chr(ord('A') + j)}{i + OFFSET + 1}",
                    'values': [[""]]
                })

    if delete_batch_update:
        await sheet.batch_update(delete_batch_update, value_input_option=gspread.utils.ValueInputOption.raw)

async def update_member(client: gspread_asyncio.AsyncioGspreadClientManager, member: discord.Member, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):
    async with spreadsheet_member_update:
        log.info(f"Updating member {member.display_name} ({member.id}) in spreadsheet")
        await _update_member(client, member, parse_display_name, spread_settings)
        log.info(f"Member {member.display_name} ({member.id}) updated in spreadsheet")

async def _sort_member(client: gspread_asyncio.AsyncioGspreadClientManager, spread_settings: jsonFileManager.JsonFileManager):
    """
    Sort member by class role and company role
    """
    # Open the worksheet
    auth = await client.authorize()
    spreadsheet_role_settings = await spread_settings.load()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    sheet = await worksheet.worksheet("Memberliste")
    
    # Sort by class role and company role
    await sheet.sort([(3, 'des'), (2, 'asc'), (1, 'asc')], range='A10:L114')

async def sort_member(client: gspread_asyncio.AsyncioGspreadClientManager, spread_settings: jsonFileManager.JsonFileManager):
    async with spreadsheet_member_update:
        log.info("Sorting member list")
        await _sort_member(client, spread_settings)
        log.info("Member list sorted")