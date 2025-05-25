# New World Stamina Checker Bot 🛡️

Welcome to the **New World Stamina Checker Bot**! This Discord bot is designed to enhance your gaming experience in *New World: Aeternum* by providing tools for managing events, analyzing gameplay, and keeping your community organized. 🚀

---

## Features ✨

### 🎥 **Stamina Check**
Analyze YouTube videos for stamina management during wars and events:
- Detects moments when stamina drops to zero.
- Provides timestamps for critical moments.
- Offers humorous and motivational feedback based on performance.

### 🏆 **Level-System**
A comprehensive user activity tracking and rewards system:
- Users earn XP through text messages and voice chat activity
- Progress through 100 levels with increasing XP requirements
- Level numbers display automatically in user nicknames
- Track individual progress and compete on server-wide leaderboards
- Rewards both active chatters and voice participants

### 📊 **Activity Analytics**
Detailed tracking and visualization of server activity:
- Complete XP history with timestamps and sources
- Visual graphs for individual XP progression
- Monthly statistics and server-wide activity analysis
- Performance breakdown by activity type (messages, voice, etc.)
- Historical data for long-term trend analysis

### 📊 **Google Sheets Integration**
Seamlessly integrates with Google Sheets to manage and update:
- **Payout lists**: Automatically updates participation stats for wars and events.
- **Member lists**: Keeps track of guild members and their roles.
- **Stats**: Displays individual player statistics directly from the spreadsheet.

### 🛠️ **Role Management**
- Automatically updates member nicknames based on their roles and icons.
- Supports custom patterns for nickname formatting.
- Allows administrators to configure role icons and priorities.

### 📅 **Event Management**
- Tracks RaidHelper events for wars and races.
- Automatically updates event participation in Google Sheets.

### 🖼️ **User Extraction**
- Extracts usernames from uploaded images in specific channels.
- Matches extracted names with guild members for easy tracking.

### 🔔 **Channel Monitoring**
- Monitors activity in designated channels.
- Sends notifications when channels have been inactive for too long.

### 🏖️ **Absence Tracking**
- Allows users to submit their absence periods.
- Updates the absence information in the Google Sheets.

### 🛡️ **Error Logging**
- Logs errors and sends detailed stack traces to a designated error log channel.

---

## Commands 📜

### Slash Commands
- `/stamina_check <youtube_url>`: Analyze a YouTube video for stamina management.
- `/add_this_channel`: Add the current channel to the VOD review list.
- `/remove_this_channel`: Remove the current channel from the VOD review list.
- `/changelog`: View the latest changelog entry.
- `/stats`: Display your stats from the Google Sheet.
- `/abwesenheit`: Submit your absence period.

#### Level System Commands
- `/level [user]`: Display the current level and XP of a user.
- `/leaderboard [type]`: Show top players by XP, level, messages, or voice time.
- `/leaderboard_all [type]`: Show all users sorted by XP, level, messages, or voice time.
- `/streak [user]`: Display the activity streak and XP multiplier of a user.
- `/streak_leaders`: Show top players by activity streak.
- `/xp_history [user] [days]`: Show XP changes with timestamps and sources.
- `/xp_graph [user] [days]`: Generate a visual graph of XP progression.
- `/monthly_stats [year] [month]`: Display server statistics for a specific month.
- `/server_activity [days]`: Show a stacked bar chart of server-wide activity.

#### Admin Level Commands
- `/add_xp <user> <amount>`: Add a specific amount of XP to a user.
- `/reset_levels <confirm>`: Reset all levels and XP (requires confirmation).
- `/set_level <user> <level>`: Manually set a user's level.
- `/set_streak <user> <streak_days>`: Set the streak days for a user.
- `/level_stats`: Display general statistics about the level system.
- `/set_role <role> <icon> <prio>`: Configure icons and priorities for roles.
- `/clear_role <role>`: Remove the icon and priority for a role.
- `/list_roles`: List all roles with their icons and priorities.
- `/set_pattern <pattern>`: Set the global nickname pattern.
- `/update_all_users`: Update nicknames for all users based on the current pattern.
- `/set_document <document_id>`: Set the Google Sheets document ID.
- `/sort_spreadsheet`: Sort the spreadsheet by configured rules.
- `/set_error_log_channel <channel>`: Set the channel for error logs.
- `/set_company_role <role> <spreadsheet_value>`: Assign a company role to a spreadsheet value.
- `/remove_company_role <role>`: Remove a company role.
- `/list_company_roles`: List all configured company roles.
- `/set_class_role <role> <spreadsheet_value>`: Assign a class role to a spreadsheet value.
- `/remove_class_role <role>`: Remove a class role.
- `/list_class_roles`: List all configured class roles.
- `/set_kueken_role <role> <spreadsheet_value>`: Assign a beginner (küken) role to a spreadsheet value.
- `/remove_kueken_role <role>`: Remove a beginner (küken) role.
- `/list_kueken_roles`: List all configured beginner (küken) roles.
- `/set_channel_raidhelper_race <channel>`: Set the channel for RaidHelper races.
- `/set_channel_raidhelper_war <channel>`: Set the channel for RaidHelper wars.
- `/remove_channel_raidhelper_race`: Remove the channel for RaidHelper races.
- `/remove_channel_raidhelper_war`: Remove the channel for RaidHelper wars.
- `/watch_this_for_user_extraction`: Add the current channel to the user extraction list.
- `/remove_this_from_user_extraction`: Remove the current channel from the user extraction list.
- `/set_check_channel <role>`: Monitor a channel for inactivity and notify a role.
- `/remove_check_channel`: Stop monitoring a channel for inactivity.
- `/test`: A test command for debugging purposes.

---

## Setup 🛠️

### Prerequisites
- Python 3.9+
- A Discord bot token.
- Google Sheets API credentials.

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/Katze719/new-world-stamina-checker.git
   cd new-world-stamina-checker
   ```

2. Install dependencies:
   ```bash
   poetry install
   ```

3. Set up environment variables:
   - `BOT_TOKEN`: Your Discord bot token.
   - `GOOGLE_GEMINI_TOKEN`: API key for Google Gemini (if used).

4. Run the bot:
   ```bash
   python src/bot.py
   ```

---

## Configuration ⚙️

### Google Sheets
- Ensure your Google Sheets document is shared with the bot's service account email.
- Use `/set_document` to link the bot to your spreadsheet.

### Role Management
- Use `/set_role` to assign icons and priorities to roles.
- Use `/set_pattern` to define how nicknames should be formatted.

### Level System
- The level system starts automatically when the bot is first launched
- XP is automatically awarded for messages (1 XP) and voice activity (3 XP per minute)
- You can customize how levels appear in usernames using `/set_pattern`, recommended patterns:
  - `{name} ({level}) [{icons}]` (default)
  - `{name} | ({level}) [{icons}]`
  - `[{icons}] {name} ({level})`
- Administrators can manage the system with `/add_xp`, `/set_level`, and `/reset_levels`
- Level progress is stored in a SQLite database

### Channel Monitoring
- Use `/set_check_channel` to monitor a channel for inactivity.
- Use `/set_error_log_channel` to log errors.

---

## Contributing 🤝

Contributions are welcome! Feel free to open issues or submit pull requests to improve the bot.

---

## License 📜

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## Feedback & Support 💬

If you encounter any issues or have suggestions, please open an issue on the [GitHub repository](https://github.com/Katze719/new-world-stamina-checker).

Happy gaming! 🎮✨