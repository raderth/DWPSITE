from flask import Flask, render_template, request, jsonify, redirect, url_for
import shelve
import discord
from discord.ext import commands
from discord import app_commands
import requests
import queue
import asyncio
import json
from urllib.parse import urlencode
import os
from mcrcon import MCRcon  # For RCON support
import time  # For caching

app = Flask(__name__)
message_queue = queue.Queue()
APPLICATIONS_KEY = "applications"
PLAYER_CACHE_KEY = "player_cache"  # Cache for player skin data
PLAYER_CACHE_TIME = 3600  # Cache time in seconds (1 hour)
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def set(key, value):
    with shelve.open('mydb') as db:
        db[key] = value

def get(key):
    with shelve.open('mydb') as db:
        return db.get(key)

# RCON connection helper function
async def execute_rcon_command(command):
    try:
        # Get RCON settings from database
        rcon_host = get("rcon_host")
        rcon_port = get("rcon_port")
        rcon_password = get("rcon_password")
        
        with MCRcon(rcon_host, rcon_password, port=rcon_port) as mcr:
            resp = mcr.command(command)
            return {"status": "success", "message": resp}
    except Exception as e:
        print(f"RCON Error: {e}")
        return {"status": "error", "message": str(e)}

# New function to get player skin data with caching
def get_player_skin(username):
    # Check cache first
    cache = json.loads(get(PLAYER_CACHE_KEY) or '{}')
    current_time = time.time()
    
    # If player is in cache and cache is not expired
    if username in cache and current_time - cache[username]['timestamp'] < PLAYER_CACHE_TIME:
        return cache[username]['data']
    
    try:
        # Call Mojang API to get UUID
        uuid_response = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{username}")
        
        if uuid_response.status_code != 200:
            # Handle username not found
            return None
            
        uuid = uuid_response.json()['id']
        
        # Call Mojang API to get profile (contains skin URL)
        profile_response = requests.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}")
        
        if profile_response.status_code != 200:
            # Handle profile not found
            return None
            
        profile_data = profile_response.json()
        player_data = {
            'uuid': uuid,
            'name': profile_data['name'],
            'profile': profile_data
        }
        
        # Update cache
        if not username in cache:
            cache[username] = {}
            
        cache[username]['data'] = player_data
        cache[username]['timestamp'] = current_time
        set(PLAYER_CACHE_KEY, json.dumps(cache))
        
        return player_data
    except Exception as e:
        print(f"Error fetching player skin: {e}")
        return None

# New API endpoint to get whitelisted players
@app.route('/api/whitelisted-players')
def whitelisted_players():
    # Get all links which contain Discord ID to Minecraft username mappings
    links = get("links") or {}
    players = []
    
    for discord_id, minecraft_name in links.items():
        # Get player skin data
        player_data = get_player_skin(minecraft_name)
        
        player_info = {
            'name': minecraft_name,
            'discord_id': discord_id
        }
        
        if player_data:
            player_info['uuid'] = player_data['uuid']
        
        players.append(player_info)
    
    return jsonify(players)

##### Configuration initiation #####
refresh_commands = False
if not get("domain"):
    set("domain", "playdwp.net")
    refresh_commands = True
if not get("whitelist"):
    user_input = input("Whitelist command (username is added on the end later) (don't include the '/'): ")
    if user_input[:1] == " ":
        user_input = user_input[:-1]
    set("whitelist", user_input)
if not get("token"):
    set("token", input("Discord bot token: "))
if not get("secret"):
    set("secret", input("Discord client secret: "))
if not get("client_id"):
    set("client_id", input("Bot's client ID: "))
if not get("links"):
    set("links", {})
if not get("managed_roles"):
    set("managed_roles", [])
if not get(PLAYER_CACHE_KEY):
    set(PLAYER_CACHE_KEY, '{}')

# Add RCON configuration
if not get("rcon_host"):
    set("rcon_host", input("Minecraft server IP address: "))
if not get("rcon_port"):
    set("rcon_port", int(input("RCON port (default is 25575): ") or 25575))
if not get("rcon_password"):
    set("rcon_password", input("RCON password: "))

if get("domain") != "":
    print("Remember to add this url to your redirects: " + "https://" + get("domain") + "/callback")
else:
    print("Remember to add this url to your redirects: http://your-public-ip/callback")
if refresh_commands:
    print("Did you enter info wrongly prompt")

##### WEB #####
CLIENT_ID = get("client_id")
CLIENT_SECRET = get("secret")
DOMAIN = get("domain")
REDIRECT_URI = f"https://{DOMAIN}/callback"

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/whitelist')
def whitelist_form():
    code = request.args.get('code')
    if code:
        # This is redirected from Discord OAuth
        return render_template("whitelist.html", code=code)
    
    # No code, redirect to Discord OAuth
    params = {
        'client_id': CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'scope': 'identify'
    }
    auth_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "No code provided", 400

    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    r = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    r.raise_for_status()
    access_token = r.json()['access_token']

    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    r = requests.get('https://discord.com/api/users/@me', headers=headers)
    r.raise_for_status()
    user_id = r.json()['id']

    return redirect(f"/whitelist?code={user_id}", code=302)
    
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json
    print(data)
    message_queue.put(data)
    return jsonify({"status": "success"}), 200

@app.route('/success')
def success():
    return render_template("success.html")

##### BOT #####
async def process_message_queue():
    while True:
        try:
            data = message_queue.get_nowait()
            print(data)
            await send_confirmation_message(data)
        except queue.Empty:
            await asyncio.sleep(1)
    
class ApplicationView(discord.ui.View):
    def __init__(self, data, message_id):
        super().__init__(timeout=None)
        self.data = data
        self.message_id = message_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_response(interaction, "Accepted", discord.Color.green())
        # Use RCON to whitelist the player
        minecraft_username = self.data.get('in_game_name', '')
        if minecraft_username:
            whitelist_command = f"{get('whitelist')} {minecraft_username}"
            result = await execute_rcon_command(whitelist_command)
            if result["status"] != "success":
                await interaction.followup.send(f"Warning: Failed to whitelist player through RCON: {result['message']}", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_response(interaction, "Denied", discord.Color.red())

    async def handle_response(self, interaction, status, color):
        player_name = self.data.get('in_game_name', 'Player')
        response_embed = discord.Embed(title=f"{status}!", description=f"{player_name} was {status.lower()}!", color=color)
        await interaction.response.edit_message(embed=response_embed, view=None)

        user_id = self.data.get('code')
        guild_id = get("guild")
        guild = discord.utils.get(bot.guilds, id=guild_id)
        member = guild.get_member(int(user_id))

        if status.lower() == "accepted":
            role_id = get("role")
            role = guild.get_role(role_id)
            if member and role:
                await member.add_roles(role)
              
            embed = discord.Embed(title="Accepted", description="You have been accepted! You can now join the server", color=0x00ff00)
            await member.send(embed=embed)
        else:
            embed = discord.Embed(title="Denied", description="You have been denied! You have been deemed a bad fit for our community", color=0xff0000)
            await member.send(embed=embed)
      
        # Remove the application from the database
        applications = json.loads(get(APPLICATIONS_KEY) or '{}')
        applications.pop(str(self.message_id), None)
        set(APPLICATIONS_KEY, json.dumps(applications))

async def send_confirmation_message(data):
    guild_id = get("guild")
    guild = discord.utils.get(bot.guilds, id=guild_id)

    member = guild.get_member(int(data["code"]))
    embed = discord.Embed(title="Confirmation", description="Your application has been submitted and is being carefully reviewed", color=0xffa500)
    await member.send(embed=embed)

    links = get("links")
    links[data['code']] = data['in_game_name']
    set("links", links)
    
    embed = discord.Embed(title="Whitelist Request")
    for key, value in data.items():
        if key != "code":  # Don't show the Discord ID code in the embed
            embed.add_field(name=key, value=value, inline=False)
    
    channel = bot.get_channel(get("channel"))
    message = await channel.send(embed=embed)
    
    view = ApplicationView(data, message.id)
    await message.edit(view=view)
    
    # Store the application data in the database
    applications = json.loads(get(APPLICATIONS_KEY) or '{}')
    applications[str(message.id)] = data
    set(APPLICATIONS_KEY, json.dumps(applications))

def has_managed_role():
    async def predicate(interaction: discord.Interaction):
        user_roles = [role.id for role in interaction.user.roles]
        return any(role_id in get("managed_roles") for role_id in user_roles)
    return app_commands.check(predicate)

@bot.tree.command(name="set_whitelist_command", description="Update the whitelist command used by the bot")
@app_commands.describe(command="The whitelist command (without the username)")
@has_managed_role()
async def set_whitelist_command(interaction: discord.Interaction, command: str):
    # Remove leading slash if present
    if command.startswith('/'):
        command = command[1:]
    
    set("whitelist", command)
    await interaction.response.send_message(f"Whitelist command updated to: '{command}'", ephemeral=True)

@bot.tree.command(name="cmd", description="Execute any command on the Minecraft server via RCON")
@app_commands.describe(command="The command to execute on the server")
@has_managed_role()
async def run_command(interaction: discord.Interaction, command: str):
    await interaction.response.defer(ephemeral=True)
    
    # Use RCON to execute the command
    result = await execute_rcon_command(command)
    
    if result["status"] == "success":
        await interaction.followup.send(f"Command executed successfully: {result['message']}")
    else:
        await interaction.followup.send(f"Failed to execute command: {result['message']}")

@bot.tree.command(name="role", description="Set a role to apply when a player is accepted (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def set_role(interaction: discord.Interaction, role: discord.Role):
    set("role", role.id)
    await interaction.response.send_message(f"Role '{role.name}' has been set in the database.", ephemeral=True)

@bot.tree.command(name="whitelist", description="Whitelists a player on the Minecraft server")
@app_commands.describe(username="The Minecraft username to whitelist")
@has_managed_role()
async def whitelist_command(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    
    # Use RCON to execute whitelist command
    whitelist_command = f"{get('whitelist')} {username}"
    result = await execute_rcon_command(whitelist_command)
    
    if result["status"] == "success":
        # Add to links so player appears on the website
        links = get("links") or {}
        links["manual_" + username] = username  # Use a prefix for manually added players
        set("links", links)
        
        await interaction.followup.send(f"Successfully whitelisted {username}: {result['message']}")
    else:
        await interaction.followup.send(f"Failed to whitelist {username}: {result['message']}")

@bot.tree.command(name="test_rcon", description="Test the RCON connection to the Minecraft server")
@app_commands.checks.has_permissions(administrator=True)
async def test_rcon(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    result = await execute_rcon_command("list")
    
    if result["status"] == "success":
        await interaction.followup.send(f"RCON connection successful! Server response: {result['message']}")
    else:
        await interaction.followup.send(f"RCON connection failed: {result['message']}")

@bot.tree.command(name="set_rcon", description="Update RCON connection settings")
@app_commands.describe(
    host="The Minecraft server address",
    port="The RCON port (default: 25575)",
    password="The RCON password"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_rcon(interaction: discord.Interaction, host: str, port: int = 25575, password: str = None):
    await interaction.response.defer(ephemeral=True)
    
    set("rcon_host", host)
    set("rcon_port", port)
    
    if password:
        set("rcon_password", password)
        password_message = "Password has been updated."
    else:
        password_message = "Password was not changed."
    
    await interaction.followup.send(f"RCON settings updated!\nHost: {host}\nPort: {port}\n{password_message}")

@bot.tree.command(name="set_channel", description="Sets the channel for whitelist applications")
@app_commands.checks.has_permissions(administrator=True)
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    set("channel", channel.id)
    set("guild", interaction.guild.id)
    await interaction.response.send_message(f"Channel set to: {channel.name}")

@bot.tree.command(name="add_role", description="Add a role for who can use commands")
@app_commands.checks.has_permissions(administrator=True)
async def add_role(interaction: discord.Interaction, role: discord.Role):
    if role.id not in get("managed_roles"):
        roles = get("managed_roles")
        roles.append(role.id)
        set("managed_roles", roles)
        await interaction.response.send_message(f"Added {role.name} to the managed roles list.")
    else:
        await interaction.response.send_message(f"{role.name} is already in the managed roles list.")

@bot.tree.command(name="remove_role", description="Remove a role for who can use commands")
@app_commands.checks.has_permissions(administrator=True)
async def remove_role(interaction: discord.Interaction, role: discord.Role):
    if role.id in get("managed_roles"):
        roles = get("managed_roles")
        roles.remove(role.id)
        set("managed_roles", roles)
        await interaction.response.send_message(f"Removed {role.name} from the managed roles list.")
    else:
        await interaction.response.send_message(f"{role.name} is not in the managed roles list.")

@bot.event
async def on_ready():
    if refresh_commands:
        await bot.tree.sync()
    print(f'Logged in as {bot.user}! Commands synced.')
    # Recreate views for existing applications
    applications = json.loads(get(APPLICATIONS_KEY) or '{}')
    
    for message_id, data in applications.items():
        channel = bot.get_channel(get("channel"))
        try:
            message = await channel.fetch_message(int(message_id))
            view = ApplicationView(data, int(message_id))
            await message.edit(view=view)
        except discord.NotFound:
            # Message no longer exists, remove
            applications.pop(message_id, None)
    
    set(APPLICATIONS_KEY, json.dumps(applications))
  
async def start_bot():
    await bot.start(get("token"))

if __name__ == "__main__":
    # Create a background task for processing messages
    loop = asyncio.get_event_loop()
    loop.create_task(process_message_queue())
    
    # Start the bot
    loop.create_task(start_bot())
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=80)