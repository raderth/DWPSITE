# database.py
import shelve
import json
import time

APPLICATIONS_KEY = "applications"
PLAYER_CACHE_KEY = "player_cache"
PLAYER_CACHE_TIME = 3600  # Cache time in seconds (1 hour)
DB_FILE = 'mydb' # Ensure this path is accessible by both services

def set_value(key, value):
    with shelve.open(DB_FILE) as db:
        db[key] = value

def get_value(key):
    with shelve.open(DB_FILE) as db:
        return db.get(key)

# --- user flags/notes ---
from datetime import datetime

def get_all_user_flags():
    """Get all users who have flags set."""
    try:
        with shelve.open(DB_FILE) as db:
            user_flags = db.get("user_flags", {})
            # Only return users who actually have flags set (not None)
            return {user_id: flag for user_id, flag in user_flags.items() if flag is not None}
    except Exception as e:
        print(f"Error getting all user flags: {e}")
        return {}

def get_user_notes(user_identifier):
    """Get notes for a user by IGN or Discord ID"""
    notes = get_value("user_notes") or {}
    return notes.get(str(user_identifier), [])

def add_user_note(user_identifier, note, author):
    """Add a note for a user"""
    notes = get_value("user_notes") or {}
    user_key = str(user_identifier)
    if user_key not in notes:
        notes[user_key] = []
    
    note_entry = {
        "note": note,
        "author": author,
        "timestamp": datetime.now().isoformat()
    }
    notes[user_key].append(note_entry)
    set_value("user_notes", notes)

def get_user_flag(user_identifier):
    """Get flag status for a user"""
    flags = get_value("user_flags") or {}
    return flags.get(str(user_identifier))

def set_user_flag(user_identifier, flag_type):
    """Set flag for a user (positive, negative, or None to remove)"""
    flags = get_value("user_flags") or {}
    user_key = str(user_identifier)
    if flag_type is None:
        flags.pop(user_key, None)
    else:
        flags[user_key] = flag_type
    set_value("user_flags", flags)

# --- Application Specific Helpers ---
def get_applications():
    return json.loads(get_value(APPLICATIONS_KEY) or '{}')

def save_applications(applications):
    set_value(APPLICATIONS_KEY, json.dumps(applications))

def add_application_to_queue(app_data):
    """
    Adds application data to a list in shelve that the bot will process.
    This simulates the old queue behavior.
    """
    with shelve.open(DB_FILE) as db:
        pending_apps = db.get("pending_applications_queue", [])
        pending_apps.append(app_data)
        db["pending_applications_queue"] = pending_apps

def get_application_from_queue():
    """
    Retrieves and removes the oldest application from the queue.
    Returns None if the queue is empty.
    """
    with shelve.open(DB_FILE) as db:
        pending_apps = db.get("pending_applications_queue", [])
        if not pending_apps:
            return None
        app_data = pending_apps.pop(0)
        db["pending_applications_queue"] = pending_apps
        return app_data

# --- Player Cache Specific Helpers ---
def get_player_cache():
    return json.loads(get_value(PLAYER_CACHE_KEY) or '{}')

def save_player_cache(cache):
    set_value(PLAYER_CACHE_KEY, json.dumps(cache))

def get_cached_player_skin(username):
    cache = get_player_cache()
    current_time = time.time()
    if username in cache and current_time - cache[username]['timestamp'] < PLAYER_CACHE_TIME:
        return cache[username]['data']
    return None

def cache_player_skin(username, player_data):
    cache = get_player_cache()
    current_time = time.time()
    if username not in cache: # Ensure 'username' key exists
        cache[username] = {}
    cache[username]['data'] = player_data
    cache[username]['timestamp'] = current_time
    save_player_cache(cache)

# --- Initial Configuration Setup (Consider moving to a separate setup script) ---
def initial_setup():
    print("Running initial configuration setup...")
    refresh_commands = False
    
    if not get_value("token"):
        set_value("token", input("Discord bot token: "))
        print("Discord bot token set.")
    
    if not get_value("guild"):
        guild_id = input("Discord server/guild ID (right-click server icon → Copy ID): ")
        set_value("guild", int(guild_id) if guild_id.isdigit() else guild_id)
        print(f"Guild ID set: {guild_id}")
    
    if not get_value("channel"):
        channel_id = input("Application review channel ID (right-click channel → Copy ID): ")
        set_value("channel", int(channel_id) if channel_id.isdigit() else channel_id)
        print(f"Application channel ID set: {channel_id}")
    
    if not get_value("domain"):
        set_value("domain", input("Domain for webapp (e.g., playdwp.net): ") or "playdwp.net")
        refresh_commands = True
        print("Domain set.")
    
    if not get_value("whitelist"):
        user_input = input("Whitelist command (e.g., 'whitelist add'): ")
        if user_input.startswith("/"):
            user_input = user_input[1:]
        set_value("whitelist", user_input)
        print(f"Set whitelist command: {user_input}")
    
    if not get_value("secret"):
        set_value("secret", input("Discord client secret: "))
        print("Discord client secret set.")
    
    if not get_value("client_id"):
        set_value("client_id", input("Bot's client ID: "))
        print("Bot client ID set.")
    
    if not get_value("role"):
        role_id = input("Role ID for whitelisted members (optional, press Enter to skip): ")
        if role_id and role_id.isdigit():
            set_value("role", int(role_id))
            print(f"Whitelisted member role ID set: {role_id}")
    
    if not get_value("links"):
        set_value("links", {})
        print("Initialized empty links.")
    
    if not get_value("managed_roles"):
        set_value("managed_roles", [])
        print("Initialized empty managed roles.")
    
    if not get_value(PLAYER_CACHE_KEY):
        set_value(PLAYER_CACHE_KEY, '{}')
        print("Initialized player cache.")
    
    if not get_value("rcon_host"):
        set_value("rcon_host", input("Minecraft server IP address: "))
        print("RCON host set.")
    
    if not get_value("rcon_port"):
        port_input = input("RCON port (default is 25575): ") or "25575"
        set_value("rcon_port", int(port_input))
        print(f"RCON port set: {port_input}")
    
    if not get_value("rcon_password"):
        set_value("rcon_password", input("RCON password: "))
        print("RCON password set.")

    domain = get_value("domain")
    if domain:
        print(f"Remember to add this url to your Discord App's OAuth2 redirects: https://{domain}/callback")
    else:
        print("Warning: Domain is not set. Callback URL may not function correctly.")
        print("Remember to add this url to your redirects: http://your-public-ip/callback")

    if refresh_commands:
        print("Configuration has been updated. Consider restarting the bot to sync commands if applicable.")
    
    print("Initial configuration setup finished.")
    return refresh_commands
