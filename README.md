# New World Stamina Checker Bot 🛡️

Welcome to the **New World Stamina Checker Bot**! This Discord bot is designed to enhance your gaming experience in *New World: Aeternum* by providing tools for managing events, analyzing gameplay, and keeping your community organized. 🚀

---

## Features ✨

### 🎥 **Stamina Check**
Analyze YouTube videos for stamina management during wars and events:
- Detects moments when stamina drops to zero.
- Provides timestamps for critical moments.
- Offers humorous and motivational feedback based on performance.

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
- `/set_role`: Configure icons and priorities for roles.
- `/list_roles`: List all roles with their icons and priorities.
- `/set_pattern`: Set the global nickname pattern.
- `/update_all_users`: Update nicknames for all users based on the current pattern.
- `/set_document`: Set the Google Sheets document ID.
- `/sort_spreadsheet`: Sort the spreadsheet by configured rules.
- `/set_error_log_channel`: Set the channel for error logs.

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

