# discord_bot.py
from datetime import datetime
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from mcrcon import MCRcon

# Import database functions
from database import get_value, set_value, get_applications, save_applications, \
                     get_application_from_queue, initial_setup, DB_FILE

# --- Bot Setup ---
intents = discord.Intents.default()
intents.members = True # Crucial for guild.get_member()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- RCON Helper ---
def execute_rcon_command(command):
    rcon_host = get_value("rcon_host")
    rcon_port = get_value("rcon_port")
    rcon_password = get_value("rcon_password")
    
    if not all([rcon_host, rcon_port, rcon_password]):
        return {"status": "error", "message": "RCON settings not fully configured."}
        
    try:
        with MCRcon(rcon_host, rcon_password, port=int(rcon_port)) as mcr:
            resp = mcr.command(command)
            return {"status": "success", "message": resp}
    except ConnectionRefusedError:
        return {"status": "error", "message": "RCON connection refused. Is the server running and RCON enabled?"}
    except Exception as e:
        print(f"RCON Error: {e}")
        return {"status": "error", "message": str(e)}

# --- Application View (for handling Accept/Deny buttons) ---
class ApplicationView(discord.ui.View):
    def __init__(self, application_data, message_id):
        super().__init__(timeout=None) # Persistent view
        self.application_data = application_data
        self.message_id = str(message_id) # Ensure message_id is string for dict keys

    async def handle_application_action(self, interaction: discord.Interaction, status: str, color: discord.Color):
        await interaction.response.defer() # Acknowledge interaction

        player_name = self.application_data.get('in_game_name', 'N/A')
        discord_user_id = self.application_data.get('code') # This is the Discord User ID

        if not discord_user_id:
            await interaction.followup.send("Error: Missing Discord User ID in application data.", ephemeral=True)
            return

        # Update the original message
# Update the original message - keep original info and add status

        try:
            original_message = await interaction.original_response()
            original_embed = original_message.embeds[0] if original_message.embeds else None

            if original_embed:
                # Keep the original embed and add status information
                original_embed.title = f"Whitelist Application - {status}"
                original_embed.color = color
                # Add status field at the top
                original_embed.insert_field_at(0, name="Status", value=f"{status} by {interaction.user.mention}", inline=False)

                await interaction.edit_original_response(embed=original_embed, view=None)

            else:
                # Fallback if no original embed found
                response_embed = discord.Embed(title=f"Application {status}",
                                       description=f"Player {player_name}'s application has been {status.lower()}.",
                                       color=color)

                response_embed.add_field(name="Processed by", value=interaction.user.mention)
                await interaction.edit_original_response(embed=response_embed, view=None)

        

        except discord.HTTPException as e:
            print(f"Failed to edit original message: {e}")
            # Fallback to sending a new message if edit fails
            response_embed = discord.Embed(title=f"Application {status}",
                                   description=f"Player {player_name}'s application has been {status.lower()}.",
                                   color=color)

            response_embed.add_field(name="Processed by", value=interaction.user.mention)
            await interaction.followup.send(embed=response_embed, ephemeral=True)


        guild_id = get_value("guild")
        if not guild_id:
            await interaction.followup.send("Error: Guild ID not configured.", ephemeral=True)
            return

        guild = bot.get_guild(int(guild_id))
        if not guild:
            await interaction.followup.send(f"Error: Bot cannot find configured guild (ID: {guild_id}).", ephemeral=True)
            return
            
        member = guild.get_member(int(discord_user_id))

        if not member:
            dm_message = f"Could not find user with ID {discord_user_id} in the server to send a DM or assign roles."
            print(dm_message)
            await interaction.followup.send(dm_message, ephemeral=True)
            # Still proceed with RCON if accepted, as user might join later
        
        # RCON Whitelisting and Role Assignment
        if status == "Accepted":
            if player_name != 'N/A':
                whitelist_cmd_template = get_value("whitelist")
                rcon_command = f"{whitelist_cmd_template} {player_name}"
                rcon_result = execute_rcon_command(rcon_command)

                if rcon_result["status"] == "success":
                    await interaction.followup.send(f"Successfully whitelisted {player_name} via RCON. {rcon_result['message']}", ephemeral=True)
                    # Add to links
                    links = get_value("links") or {}
                    links[str(discord_user_id)] = player_name
                    set_value("links", links)
                else:
                    await interaction.followup.send(f"Warning: Failed to whitelist {player_name} via RCON: {rcon_result['message']}", ephemeral=True)
            
            if member:
                try:
                    # Set the nickname to the in-game name
                    await member.edit(nick=player_name)
                    print(f"Nicknamed {member} to {player_name}")
                except discord.Forbidden:
                    print(f"Could not change nickname for {member}. Lacking permissions.")
                except Exception as e:
                    print(f"Error changing nickname: {e}")
                role_id = get_value("role")
                print(role_id)
                if role_id:
                    role = guild.get_role(int(role_id))
                    if role:
                        try:
                            await member.add_roles(role)
                            await interaction.followup.send(f"Assigned role '{role.name}' to {member.display_name}.", ephemeral=True)
                        except discord.Forbidden:
                            await interaction.followup.send(f"Error: Bot lacks permissions to assign role '{role.name}'.", ephemeral=True)
                        except Exception as e:
                            await interaction.followup.send(f"Error assigning role: {e}", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Warning: Configured role (ID: {role_id}) not found.", ephemeral=True)
            
                # Send introduction message to chat channel if public profile is enabled
                # Debug log to see what fields are available
                print(f"Application data keys: {self.application_data.keys()}")
                
                # Try different case variations for the field names
                public_profile = None
                about_me = None
                
                # Check for various possible field name formats
                for key in self.application_data:
                    key_lower = key.lower()
                    if 'public profile' in key_lower or 'public_profile' in key_lower:
                        public_profile = self.application_data[key]
                        print(f"Found public profile field: {key} = {public_profile}")
                    if 'about me' in key_lower or 'about_me' in key_lower:
                        about_me = self.application_data[key]
                        print(f"Found about me field: {key} = {about_me}")
                
                # Get the chat channel and intro channel IDs
                chat_channel_id = get_value("chat_channel_id") or 1371760029161754675
                intro_channel_id = get_value("intro_channel_id") or 1371760029161754675
                
                # Compare as string, handling various forms of "true"
                if public_profile and str(public_profile).lower() in ['true', 'yes', '1', 'on'] and about_me:
                    print(f"Attempting to send intro message with about_me: {about_me}")
                    
                    # Create the introduction embed
                    intro_embed = discord.Embed(
                        title=f"Meet {player_name}! 👋",
                        description=f"{member.mention} just joined the server! Here's a little about them:",
                        color=discord.Color.blue()
                    )
                    intro_embed.add_field(name="About Me", value=about_me, inline=False)
                    
                    # Send to chat channel
                    chat_channel = guild.get_channel(chat_channel_id)
                    if chat_channel:
                        try:
                            await chat_channel.send(embed=intro_embed)
                            print(f"Successfully sent introduction for {player_name} to chat channel")
                        except discord.Forbidden:
                            print(f"Forbidden error: Bot lacks permission to send to chat channel {chat_channel_id}")
                        except Exception as e:
                            print(f"Error sending introduction to chat: {e}")
                    else:
                        print(f"Chat channel not found: {chat_channel_id}")
                    
                    # Send to intro channel (only if different from chat channel)
                    if intro_channel_id != chat_channel_id:
                        intro_channel = guild.get_channel(intro_channel_id)
                        if intro_channel:
                            try:
                                await intro_channel.send(embed=intro_embed)
                                print(f"Successfully sent introduction for {player_name} to intro channel")
                            except discord.Forbidden:
                                print(f"Forbidden error: Bot lacks permission to send to intro channel {intro_channel_id}")
                            except Exception as e:
                                print(f"Error sending introduction to intro channel: {e}")
                        else:
                            print(f"Intro channel not found: {intro_channel_id}")
                    
                    await interaction.followup.send("Sent introduction message successfully.", ephemeral=True)
                else:
                    # Send a simple welcome message if public profile is not enabled
                    chat_channel = guild.get_channel(chat_channel_id)
                    if chat_channel:
                        try:
                            await chat_channel.send(f"Welcome {member.mention} to the server! 🎉")
                            await interaction.followup.send("Sent welcome message to chat channel.", ephemeral=True)
                        except discord.Forbidden:
                            await interaction.followup.send("Bot lacks permission to send messages in the chat channel.", ephemeral=True)
                        except Exception as e:
                            await interaction.followup.send(f"Error sending welcome message: {e}", ephemeral=True)
                
                try:
                    dm_embed = discord.Embed(title="Application Accepted!",
                                             description="Congratulations! Your whitelist application has been accepted. You should now be able to join the Minecraft server.",
                                             color=discord.Color.green())
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    print(f"Could not DM user {discord_user_id} (application accepted).")
                    await interaction.followup.send(f"Note: Could not DM user {member.display_name} (they may have DMs disabled).", ephemeral=True)

        elif status == "Denied":
            if member:
                try:
                    dm_embed = discord.Embed(title="Application Denied",
                                             description="We regret to inform you that your whitelist application has been denied at this time.",
                                             color=discord.Color.red())
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    print(f"Could not DM user {discord_user_id} (application denied).")
                    await interaction.followup.send(f"Note: Could not DM user {member.display_name} (they may have DMs disabled).", ephemeral=True)

        # Clean up application from active list
        applications = get_applications()
        if self.message_id in applications:
            del applications[self.message_id]
            save_applications(applications)
            print(f"Removed application {self.message_id} after processing.")
        else:
            print(f"Warning: Tried to remove already processed or non-existent application {self.message_id}")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="persistent_accept_button")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_application_action(interaction, "Accepted", discord.Color.green())

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="persistent_deny_button")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_application_action(interaction, "Denied", discord.Color.red())

# --- Task to process applications from the shelve queue ---
@tasks.loop(seconds=10) # Check for new applications every 10 seconds
async def process_new_applications_task():
    await bot.wait_until_ready() # Ensure bot is logged in and cache is ready
    
    app_data = get_application_from_queue() # Fetches one at a time
    if app_data:
        print(f"Processing new application from queue: {app_data}")
        discord_user_id = app_data.get('code')
        in_game_name = app_data.get('in_game_name', 'N/A')

        if not discord_user_id:
            print("Error: Application data missing Discord User ID ('code'). Skipping.")
            return

        guild_id = get_value("guild")
        channel_id = get_value("channel")

        if not guild_id or not channel_id:
            print("Error: Guild or Channel ID not set. Cannot post application.")
            # Optionally, re-queue the item or log to a dead-letter queue
            # For now, we'll just drop it to prevent loop if config is missing
            return
            
        guild = bot.get_guild(int(guild_id))
        if not guild:
            print(f"Error: Bot cannot find configured guild (ID: {guild_id}).")
            return
        
        channel = guild.get_channel(int(channel_id)) # Or bot.get_channel()
        if not channel:
            print(f"Error: Bot cannot find configured channel (ID: {channel_id}).")
            return

        member = guild.get_member(int(discord_user_id))
        if member:
            try:
                confirmation_embed = discord.Embed(
                    title="Application Submitted",
                    description="Your whitelist application has been successfully submitted and is awaiting review by staff.",
                    color=discord.Color.orange()
                )
                await member.send(embed=confirmation_embed)
            except discord.Forbidden:
                print(f"Could not DM user {discord_user_id} with submission confirmation.")
        else:
            print(f"Could not find member with ID {discord_user_id} to send submission confirmation DM.")

        # Create embed for staff channel
        staff_embed = discord.Embed(title="New Whitelist Application", color=discord.Color.blue())
        staff_embed.add_field(name="Minecraft IGN", value=in_game_name, inline=False)
        staff_embed.add_field(name="Discord User", value=member.mention if member else f"ID: {discord_user_id}", inline=False)
        
        # Add other form data to the embed
        for key, value in app_data.items():
            if key not in ['code', 'in_game_name']: # Already handled or internal
                staff_embed.add_field(name=key.replace('_', ' ').title(), value=value, inline=False)
        
        try:
            application_message = await channel.send(embed=staff_embed)
            # Store application with message ID for the view
            applications = get_applications()
            applications[str(application_message.id)] = app_data
            save_applications(applications)
            
            # Add view to the message
            view = ApplicationView(app_data, application_message.id)
            await application_message.edit(view=view)
            print(f"Posted application for {in_game_name} to staff channel. Message ID: {application_message.id}")

        except discord.Forbidden:
            print(f"Error: Bot lacks permission to send messages in channel {channel_id}.")
        except Exception as e:
            print(f"An error occurred while sending application to staff channel: {e}")


# --- Helper for checking managed roles ---

def has_required_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        # Check for specific role ID
        role_id = 1371766350040531005
        if any(role.id == role_id for role in interaction.user.roles):
            return True
        
        # Check for "admin" role name
        if any(role.name.lower() == "admin" for role in interaction.user.roles):
            return True
            
        return False
    return app_commands.check(predicate)

def has_managed_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user is None: return False # Should not happen with app commands
        # Handle cases where interaction.user might not be a full Member object (e.g., in DMs if command was global)
        # However, these commands are guild-specific due to permission checks.
        if not interaction.guild: return False # Should have guild context

        user_roles_ids = [role.id for role in interaction.user.roles]
        managed_roles_ids = get_value("managed_roles")
        if not managed_roles_ids: # If no roles are set, deny access for safety
            await interaction.response.send_message("No management roles configured. Access denied.", ephemeral=True)
            return False
        
        is_admin = interaction.user.guild_permissions.administrator
        if is_admin: # Admins can always use commands
            return True

        can_use = any(role_id in user_roles_ids for role_id in managed_roles_ids)
        if not can_use:
            await interaction.response.send_message("You do not have the required role to use this command.", ephemeral=True)
        return can_use
    return app_commands.check(predicate)

# --- Slash Commands ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} ({bot.user.id})')
    
    # Load persistent views
    # This ensures buttons on old messages still work after bot restarts.
    applications = get_applications()
    guild_id = get_value("guild") # Get guild_id once
    channel_id = get_value("channel") # Get channel_id once

    if guild_id and channel_id:
        guild = bot.get_guild(int(guild_id))
        if guild:
            channel = guild.get_channel(int(channel_id))
            if channel:
                active_app_count = 0
                apps_to_remove = []
                for msg_id_str, app_data in applications.items():
                    try:
                        msg_id = int(msg_id_str)
                        # Check if message still exists and has no view or a different view
                        message = await channel.fetch_message(msg_id)
                        # Re-add view if it's missing or to ensure it's the latest version
                        # This is important if the view definition changes
                        view = ApplicationView(app_data, msg_id)
                        await message.edit(view=view) # This will fail if message was already processed and view removed
                        active_app_count += 1
                    except discord.NotFound:
                        print(f"Message {msg_id_str} for application not found. Removing from active list.")
                        apps_to_remove.append(msg_id_str)
                    except discord.HTTPException as e:
                        # This can happen if message was already handled (view removed)
                        if e.status == 404 : # Not found
                             apps_to_remove.append(msg_id_str)
                        else:
                            print(f"HTTP error re-adding view to message {msg_id_str}: {e}")
                    except Exception as e:
                        print(f"Error re-adding view to message {msg_id_str}: {e}")
                
                if apps_to_remove:
                    current_apps = get_applications()
                    for m_id in apps_to_remove:
                        current_apps.pop(m_id, None)
                    save_applications(current_apps)
                print(f"Re-added views to {active_app_count} active application messages.")
            else:
                print("Configured application channel not found on ready.")
        else:
            print("Configured guild not found on ready.")
    else:
        print("Guild or Channel ID not configured. Cannot load persistent views for applications.")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

    if not process_new_applications_task.is_running():
        process_new_applications_task.start()

@bot.tree.command(name="relink", description="Relink a Discord user to a different Minecraft username or fix incorrect links.")
@has_managed_role()
@app_commands.describe(
    discord_user="Discord user to relink",
    new_minecraft_username="New Minecraft username to link to this Discord user",
    old_minecraft_username="Old/current Minecraft username (optional - helps find existing link)"
)
async def relink_command(interaction: discord.Interaction, discord_user: discord.Member, new_minecraft_username: str, old_minecraft_username: str = None):
    await interaction.response.defer(ephemeral=True)
    
    links = get_value("links") or {}
    discord_id = str(discord_user.id)
    
    # Check if the new username is already linked to someone else
    existing_discord_id = None
    for existing_id, minecraft_name in links.items():
        if minecraft_name.lower() == new_minecraft_username.lower():
            existing_discord_id = existing_id
            break
    
    # Remove old link for this Discord user
    old_username = None
    if discord_id in links:
        old_username = links[discord_id]
        del links[discord_id]
    
    # If new username was linked to someone else, remove that link too
    if existing_discord_id and existing_discord_id != discord_id:
        old_owner = None
        if existing_discord_id.startswith("manual_"):
            old_owner = "Manual entry"
        else:
            try:
                old_member = interaction.guild.get_member(int(existing_discord_id))
                old_owner = old_member.display_name if old_member else f"Discord ID: {existing_discord_id}"
            except ValueError:
                old_owner = f"Invalid Discord ID: {existing_discord_id}"
        
        del links[existing_discord_id]
    
    # Create the new link
    links[discord_id] = new_minecraft_username
    set_value("links", links)
    
    # Update the user's nickname to match their new Minecraft username
    try:
        await discord_user.edit(nick=new_minecraft_username)
        nickname_updated = True
    except discord.Forbidden:
        nickname_updated = False
    except Exception:
        nickname_updated = False
    
    # Build response message
    response_parts = [f"Successfully relinked {discord_user.display_name} to **{new_minecraft_username}**"]
    
    if old_username:
        response_parts.append(f"Previous link: **{old_username}** → {discord_user.display_name}")
    
    if existing_discord_id and existing_discord_id != discord_id:
        response_parts.append(f"Removed conflicting link: **{new_minecraft_username}** → {old_owner}")
    
    if nickname_updated:
        response_parts.append(f"Updated Discord nickname to **{new_minecraft_username}**")
    elif not nickname_updated and old_username != new_minecraft_username:
        response_parts.append("⚠️ Could not update Discord nickname (insufficient permissions)")
    
    # Optional: Update whitelist on Minecraft server
    whitelist_cmd_template = get_value("whitelist")
    if whitelist_cmd_template:
        # Remove old username from whitelist if it exists
        if old_username and old_username.lower() != new_minecraft_username.lower():
            remove_result = execute_rcon_command(f"whitelist remove {old_username}")
            if remove_result["status"] == "success":
                response_parts.append(f"Removed **{old_username}** from server whitelist")
        
        # Add new username to whitelist
        add_result = execute_rcon_command(f"{whitelist_cmd_template} {new_minecraft_username}")
        if add_result["status"] == "success":
            response_parts.append(f"Added **{new_minecraft_username}** to server whitelist")
        else:
            response_parts.append(f"⚠️ Failed to whitelist **{new_minecraft_username}**: {add_result['message']}")
    
    embed = discord.Embed(
        title="Player Relinked Successfully",
        description="\n".join(response_parts),
        color=discord.Color.green()
    )
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="notes", description="View or add notes for a user.")
@has_managed_role()
@app_commands.describe(
    discord_user="Discord user to add note for",
    minecraft_username="Minecraft username to add note for", 
    note="Note to add (leave empty to view existing notes)"
)
async def notes_command(interaction: discord.Interaction, discord_user: discord.Member = None, minecraft_username: str = None, note: str = None):
    await interaction.response.defer(ephemeral=True)
    
    if not discord_user and not minecraft_username:
        await interaction.followup.send("You must provide either a Discord user or Minecraft username.", ephemeral=True)
        return
    
    # Find user identifier
    user_identifier = None
    display_name = None
    
    if discord_user:
        user_identifier = str(discord_user.id)
        display_name = discord_user.display_name
    elif minecraft_username:
        # Check if user exists in links
        links = get_value("links") or {}
        for discord_id, minecraft_name in links.items():
            if minecraft_name.lower() == minecraft_username.lower():
                user_identifier = discord_id
                display_name = minecraft_username
                break
        if not user_identifier:
            user_identifier = minecraft_username
            display_name = minecraft_username
    
    if note:
        # Add note
        from database import add_user_note
        add_user_note(user_identifier, note, interaction.user.display_name)
        await interaction.followup.send(f"Added note for {display_name}.")
    else:
        # View notes
        from database import get_user_notes
        notes = get_user_notes(user_identifier)
        
        if not notes:
            await interaction.followup.send(f"No notes found for {display_name}.")
            return
        
        embed = discord.Embed(title=f"Notes for {display_name}", color=discord.Color.blue())
        for i, note_entry in enumerate(notes):
            timestamp = note_entry.get("timestamp", "Unknown time")
            author = note_entry.get("author", "Unknown")
            note_text = note_entry.get("note", "")
            embed.add_field(
                name=f"Note {i+1} - {author}",
                value=f"{note_text}\n*{timestamp}*",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="flag", description="Flag a user positively or negatively.")
@has_managed_role()
@app_commands.describe(
    discord_user="Discord user to flag",
    minecraft_username="Minecraft username to flag",
    flag_type="Type of flag (positive/amber/negative/remove)"
)
@app_commands.choices(flag_type=[
    app_commands.Choice(name="Positive", value="positive"),
    app_commands.Choice(name="Amber Warning", value="amber"),
    app_commands.Choice(name="Negative", value="negative"),
    app_commands.Choice(name="Remove Flag", value="remove")
])
async def flag_command(interaction: discord.Interaction, flag_type: str, discord_user: discord.Member = None, minecraft_username: str = None):
    await interaction.response.defer(ephemeral=True)
    
    if not discord_user and not minecraft_username:
        await interaction.followup.send("You must provide either a Discord user or Minecraft username.", ephemeral=True)
        return
    
    # Find user identifier
    user_identifier = None
    display_name = None
    
    if discord_user:
        user_identifier = str(discord_user.id)
        display_name = discord_user.display_name
    elif minecraft_username:
        links = get_value("links") or {}
        for discord_id, minecraft_name in links.items():
            if minecraft_name.lower() == minecraft_username.lower():
                user_identifier = discord_id
                display_name = minecraft_username
                break
        if not user_identifier:
            user_identifier = minecraft_username
            display_name = minecraft_username
    
    from database import set_user_flag
    
    if flag_type == "remove":
        set_user_flag(user_identifier, None)
        await interaction.followup.send(f"Removed flag for {display_name}.")
    else:
        set_user_flag(user_identifier, flag_type)
        flag_emoji = {"positive": "🟢", "amber": "🟡", "negative": "🔴"}[flag_type]
        await interaction.followup.send(f"Flagged {display_name} as {flag_type} {flag_emoji}")

@bot.tree.command(name="setup_initial_config", description="Run interactive setup for bot configuration (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
async def setup_config_command(interaction: discord.Interaction):
    await interaction.response.send_message("Starting initial configuration process in the bot's console. Please check the terminal.", ephemeral=True)
    # Running initial_setup which has input() calls.
    # This is not ideal to run from a command but can be a first step.
    # Consider a dedicated setup script for production.
    try:
        initial_setup() # This will print to console and ask for input there.
        await interaction.followup.send("Initial setup prompts have been run in the console. Please provide input there if needed.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error during console setup: {e}",ephemeral=True)


@bot.tree.command(name="list_flags", description="List all flagged users sorted from worst to best.")
@has_managed_role()
@app_commands.describe(
    flag_filter="Filter by specific flag type (optional)"
)
@app_commands.choices(flag_filter=[
    app_commands.Choice(name="All Flags", value="all"),
    app_commands.Choice(name="Negative Only", value="negative"),
    app_commands.Choice(name="Amber Only", value="amber"),
    app_commands.Choice(name="Positive Only", value="positive")
])
async def list_flags_command(interaction: discord.Interaction, flag_filter: str = "all"):
    await interaction.response.defer(ephemeral=True)
    
    from database import get_all_user_flags
    
    # Get all flagged users
    flagged_users = get_all_user_flags()
    
    if not flagged_users:
        await interaction.followup.send("No flagged users found in the database.")
        return
    
    # Filter if requested
    if flag_filter != "all":
        flagged_users = {user_id: flag for user_id, flag in flagged_users.items() if flag == flag_filter}
        
        if not flagged_users:
            await interaction.followup.send(f"No users found with {flag_filter} flags.")
            return
    
    # Sort by flag priority (worst to best: negative, amber, positive)
    flag_priority = {"negative": 0, "amber": 1, "positive": 2}
    sorted_users = sorted(flagged_users.items(), key=lambda x: flag_priority.get(x[1], 3))
    
    # Get guild for member lookup
    guild = interaction.guild
    links = get_value("links") or {}
    
    # Build the list
    flag_list = []
    flag_emojis = {"positive": "🟢", "amber": "🟡", "negative": "🔴"}
    
    for user_id, flag in sorted_users:
        flag_emoji = flag_emojis.get(flag, "❓")
        
        # Try to find the display name
        display_info = None
        
        # Check if it's a Discord user ID
        if user_id.isdigit():
            member = guild.get_member(int(user_id)) if guild else None
            if member:
                minecraft_name = links.get(user_id, "Unknown")
                display_info = f"{member.display_name} → {minecraft_name}"
            else:
                minecraft_name = links.get(user_id, "Unknown")
                display_info = f"Discord ID: {user_id} → {minecraft_name}"
        else:
            # Probably a minecraft username or manual entry
            display_info = f"Minecraft: {user_id}"
        
        flag_list.append(f"{flag_emoji} **{flag.title()}**: {display_info}")
    
    # Create embed(s)
    if len(flag_list) <= 20:
        embed = discord.Embed(
            title=f"Flagged Users ({flag_filter.title() if flag_filter != 'all' else 'All Flags'})",
            description="\n".join(flag_list),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Sorted from worst to best: Red → Amber → Green")
        await interaction.followup.send(embed=embed)
    else:
        # Send in chunks if too many
        chunks = [flag_list[i:i+20] for i in range(0, len(flag_list), 20)]
        for i, chunk in enumerate(chunks):
            chunk_embed = discord.Embed(
                title=f"Flagged Users (Page {i+1}/{len(chunks)}) - {flag_filter.title() if flag_filter != 'all' else 'All Flags'}",
                description="\n".join(chunk),
                color=discord.Color.orange()
            )
            if i == 0:  # Only add footer to first page
                chunk_embed.set_footer(text="Sorted from worst to best: Red → Amber → Green")
            await interaction.followup.send(embed=chunk_embed, ephemeral=True)

# Updated find_player command to show amber flags properly
@bot.tree.command(name="find_player", description="Find a player's information by Discord user or Minecraft username.")
@has_managed_role()
@app_commands.describe(
    discord_user="Discord user to search for",
    minecraft_username="Minecraft username to search for"
)
async def find_player(interaction: discord.Interaction, discord_user: discord.Member = None, minecraft_username: str = None):
    await interaction.response.defer(ephemeral=True)

    if not discord_user and not minecraft_username:
        await interaction.followup.send("You must provide either a Discord user or Minecraft username to search for.", ephemeral=True)
        return

    links = get_value("links") or {}
    found_matches = []

    # Search by Discord user
    if discord_user:
        discord_id = str(discord_user.id)
        if discord_id in links:
            minecraft_name = links[discord_id]
            
            # Get notes and flag
            from database import get_user_notes, get_user_flag
            notes = get_user_notes(discord_id)
            flag = get_user_flag(discord_id)
            
            match_info = f"Discord: {discord_user.display_name} ({discord_user.mention})\nMinecraft: {minecraft_name}"
            
            # Add flag info with proper emoji
            if flag:
                flag_emojis = {"positive": "🟢", "amber": "🟡", "negative": "🔴"}
                flag_emoji = flag_emojis.get(flag, "❓")
                match_info += f"\nFlag: {flag.title()} {flag_emoji}"
            
            # Add notes count
            if notes:
                match_info += f"\nNotes: {len(notes)} note(s)"
            
            found_matches.append(match_info)

    # Search by Minecraft username
    if minecraft_username:
        for discord_id, minecraft_name in links.items():
            if minecraft_name.lower() == minecraft_username.lower():
                from database import get_user_notes, get_user_flag
                notes = get_user_notes(discord_id)
                flag = get_user_flag(discord_id)
                
                if discord_id.startswith("manual"):
                    match_info = f"Minecraft: {minecraft_name}\nType: Manual whitelist"
                else:
                    member = interaction.guild.get_member(int(discord_id)) if interaction.guild else None
                    if member:
                        match_info = f"Minecraft: {minecraft_name}\nDiscord: {member.display_name} ({member.mention})"
                    else:
                        match_info = f"Minecraft: {minecraft_name}\nDiscord ID: {discord_id} (User not found)"
                
                # Add flag info with proper emoji
                if flag:
                    flag_emojis = {"positive": "🟢", "amber": "🟡", "negative": "🔴"}
                    flag_emoji = flag_emojis.get(flag, "❓")
                    match_info += f"\nFlag: {flag.title()} {flag_emoji}"
                
                # Add notes count
                if notes:
                    match_info += f"\nNotes: {len(notes)} note(s)"
                
                found_matches.append(match_info)

    if found_matches:
        embed = discord.Embed(title="Player Search Results", color=discord.Color.green())
        for i, match in enumerate(found_matches):
            embed.add_field(name=f"Match {i+1}", value=match, inline=False)
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send("No matching players found in the database.")

@bot.tree.command(name="set_channel", description="Sets the channel for whitelist applications (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where applications will be posted.")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return
    set_value("channel", channel.id)
    set_value("guild", interaction.guild.id) # Store guild ID as well
    await interaction.response.send_message(f"Whitelist applications will now be posted in {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set_chat_channel", description="Set the channel for welcome/intro messages (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where welcome messages will be posted.")
async def set_chat_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_value("chat_channel_id", channel.id)
    await interaction.response.send_message(f"Welcome messages will now be posted in {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set_intro_channel", description="Set the channel for introduction messages (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where introduction messages will be posted.")
async def set_intro_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_value("intro_channel_id", channel.id)
    await interaction.response.send_message(f"Introduction messages will now be posted in {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set_member_role", description="Set the role to give members upon whitelist acceptance (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="The role to assign.")
async def set_member_role(interaction: discord.Interaction, role: discord.Role):
    set_value("role", role.id)
    await interaction.response.send_message(f"'{role.name}' will be assigned to accepted applicants.", ephemeral=True)

@bot.tree.command(name="add_management_role", description="Add a role that can use bot management commands (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="The role to add.")
async def add_management_role(interaction: discord.Interaction, role: discord.Role):
    managed_roles = get_value("managed_roles") or []
    if role.id not in managed_roles:
        managed_roles.append(role.id)
        set_value("managed_roles", managed_roles)
        await interaction.response.send_message(f"Role '{role.name}' can now use management commands.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Role '{role.name}' is already in the management list.", ephemeral=True)

@bot.tree.command(name="remove_management_role", description="Remove a role from bot management (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="The role to remove.")
async def remove_management_role(interaction: discord.Interaction, role: discord.Role):
    managed_roles = get_value("managed_roles") or []
    if role.id in managed_roles:
        managed_roles.remove(role.id)
        set_value("managed_roles", managed_roles)
        await interaction.response.send_message(f"Role '{role.name}' can no longer use management commands.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Role '{role.name}' is not in the management list.", ephemeral=True)

@bot.tree.command(name="set_rcon_details", description="Update RCON connection settings (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(host="Server IP/hostname", port="RCON port", password="RCON password")
async def set_rcon_details(interaction: discord.Interaction, host: str, port: int, password: str):
    set_value("rcon_host", host)
    set_value("rcon_port", port)
    set_value("rcon_password", password)
    await interaction.response.send_message(f"RCON settings updated: Host={host}, Port={port}.", ephemeral=True)

@bot.tree.command(name="set_whitelist_rcon_command", description="Set the RCON command for whitelisting (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(command="The RCON command (e.g., 'whitelist add')")
async def set_whitelist_rcon_command(interaction: discord.Interaction, command: str):
    if command.startswith('/'): # RCON commands typically don't need '/'
        command = command[1:]
    set_value("whitelist", command)
    await interaction.response.send_message(f"Whitelist RCON command set to: `{command} <username>`", ephemeral=True)

@bot.tree.command(name="rcon", description="Execute an RCON command on the Minecraft server.")
@has_required_role()
@app_commands.describe(command="The command to execute (without '/')")
async def rcon_command(interaction: discord.Interaction, command: str):
    await interaction.response.defer(ephemeral=True)
    result = execute_rcon_command(command)
    if result["status"] == "success":
        await interaction.followup.send(f"RCON Success: ```{result['message']}```")
    else:
        await interaction.followup.send(f"RCON Error: {result['message']}")

@bot.tree.command(name="manual_whitelist", description="Manually whitelist a Minecraft user via RCON.")
@has_managed_role()
@app_commands.describe(username="Minecraft username")
async def manual_whitelist(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    whitelist_cmd_template = get_value("whitelist")
    if not whitelist_cmd_template:
        await interaction.followup.send("Whitelist command not configured. Use `/set_whitelist_rcon_command`.",ephemeral=True)
        return
        
    rcon_command_to_run = f"{whitelist_cmd_template} {username}"
    result =  execute_rcon_command(rcon_command_to_run)
    
    if result["status"] == "success":
        # Add to links so player appears on the website (if desired)
        links = get_value("links") or {}
        # Use a placeholder for Discord ID for manually added players or decide on a convention
        links[f"manual_{username}"] = username
        set_value("links", links)
        await interaction.followup.send(f"Successfully whitelisted {username}: {result['message']}")
    else:
        await interaction.followup.send(f"Failed to whitelist {username}: {result['message']}")

@bot.tree.command(name="remove_whitelist", description="Remove a player from the whitelist via RCON.")
@has_managed_role()
@app_commands.describe(username="Minecraft username to remove")
async def remove_whitelist(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    
    # Execute whitelist remove command
    rcon_command = f"whitelist remove {username}"
    result = execute_rcon_command(rcon_command)
    
    if result["status"] == "success":
        # Remove from links database
        links = get_value("links") or {}
        removed_entries = []
        
        # Find and remove entries with this username
        for discord_id, minecraft_name in list(links.items()):
            if minecraft_name.lower() == username.lower():
                del links[discord_id]
                removed_entries.append(discord_id)
        
        set_value("links", links)
        
        response_msg = f"Successfully removed {username} from whitelist: {result['message']}"
        if removed_entries:
            response_msg += f"\nRemoved {len(removed_entries)} link(s) from database."
        
        await interaction.followup.send(response_msg)
    else:
        await interaction.followup.send(f"Failed to remove {username} from whitelist: {result['message']}")

@bot.tree.command(name="remove_player_data", description="Remove a player's data from the bot's database.")
@has_required_role()
@app_commands.describe(
    discord_user="Discord user to remove data for",
    minecraft_username="Minecraft username to remove (optional if discord_user provided)"
)
async def remove_player_data(interaction: discord.Interaction, discord_user: discord.Member = None, minecraft_username: str = None):
    await interaction.response.defer(ephemeral=True)
    
    if not discord_user and not minecraft_username:
        await interaction.followup.send("You must provide either a Discord user or Minecraft username.", ephemeral=True)
        return
    
    links = get_value("links") or {}
    removed_entries = []
    
    # Remove by Discord user
    if discord_user:
        discord_id = str(discord_user.id)
        if discord_id in links:
            minecraft_name = links[discord_id]
            del links[discord_id]
            removed_entries.append(f"Discord: {discord_user.display_name} -> Minecraft: {minecraft_name}")
    
    # Remove by Minecraft username
    if minecraft_username:
        for discord_id, minecraft_name in list(links.items()):
            if minecraft_name.lower() == minecraft_username.lower():
                del links[discord_id]
                removed_entries.append(f"Discord ID: {discord_id} -> Minecraft: {minecraft_name}")
    
    set_value("links", links)
    
    if removed_entries:
        response = f"Removed {len(removed_entries)} player link(s) from database:\n"
        for entry in removed_entries:
            response += f"• {entry}\n"
        await interaction.followup.send(response)
    else:
        await interaction.followup.send("No matching player data found to remove.")

@bot.tree.command(name="list_whitelisted_players", description="List all whitelisted players in the database.")
@has_managed_role()
async def list_whitelisted_players(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    links = get_value("links") or {}
    
    if not links:
        await interaction.followup.send("No whitelisted players found in the database.")
        return
    
    # Create embed with player list
    embed = discord.Embed(title="Whitelisted Players", color=discord.Color.blue())
    
    player_list = []
    guild = interaction.guild
    
    for discord_id, minecraft_name in links.items():
        if discord_id.startswith("manual_"):
            player_list.append(f"**{minecraft_name}** (Manual)")
        else:
            member = guild.get_member(int(discord_id)) if guild else None
            if member:
                player_list.append(f"**{minecraft_name}** → {member.display_name}")
            else:
                player_list.append(f"**{minecraft_name}** → Discord ID: {discord_id}")
    
    # Split into chunks if too long
    if len(player_list) <= 20:
        embed.description = "\n".join(player_list)
        await interaction.followup.send(embed=embed)
    else:
        # Send in chunks
        chunks = [player_list[i:i+20] for i in range(0, len(player_list), 20)]
        for i, chunk in enumerate(chunks):
            chunk_embed = discord.Embed(
                title=f"Whitelisted Players (Page {i+1}/{len(chunks)})",
                description="\n".join(chunk),
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=chunk_embed, ephemeral=True)

@bot.tree.command(name="bulk_remove_whitelist", description="Remove multiple players from whitelist (comma-separated usernames).")
@has_required_role()
@app_commands.describe(usernames="Comma-separated list of Minecraft usernames to remove")
async def bulk_remove_whitelist(interaction: discord.Interaction, usernames: str):
    await interaction.response.defer(ephemeral=True)
    
    # Parse usernames
    username_list = [name.strip() for name in usernames.split(',') if name.strip()]
    
    if not username_list:
        await interaction.followup.send("No valid usernames provided.", ephemeral=True)
        return
    
    if len(username_list) > 10:
        await interaction.followup.send("Maximum 10 usernames allowed per bulk operation.", ephemeral=True)
        return
    
    results = []
    links = get_value("links") or {}
    
    for username in username_list:
        # Execute whitelist remove command
        rcon_command = f"whitelist remove {username}"
        result = execute_rcon_command(rcon_command)
        
        if result["status"] == "success":
            # Remove from links database
            removed_count = 0
            for discord_id, minecraft_name in list(links.items()):
                if minecraft_name.lower() == username.lower():
                    del links[discord_id]
                    removed_count += 1
            
            results.append(f"✅ **{username}**: Removed from whitelist (DB entries: {removed_count})")
        else:
            results.append(f"❌ **{username}**: {result['message']}")
    
    set_value("links", links)
    
    embed = discord.Embed(title="Bulk Whitelist Removal Results", color=discord.Color.orange())
    embed.description = "\n".join(results)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="cleanup_database", description="Remove invalid entries and check whitelist status (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_database(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    links = get_value("links") or {}
    guild = interaction.guild
    
    if not guild:
        await interaction.followup.send("This command must be used in a server.", ephemeral=True)
        return
    
    # Get the whitelist from the server via RCON
    whitelist_result = execute_rcon_command("whitelist list")
    if whitelist_result["status"] != "success":
        await interaction.followup.send(f"Failed to get whitelist from server: {whitelist_result['message']}", ephemeral=True)
        return
    
    # Parse whitelist - the response format is usually "There are X whitelisted players: player1, player2, player3"
    whitelist_response = whitelist_result["message"]
    print(f"Whitelist response: {whitelist_response}")
    
    # Extract player names from the response
    whitelisted_players = set()
    if ":" in whitelist_response:
        players_part = whitelist_response.split(":", 1)[1].strip()
        if players_part and players_part != "":
            whitelisted_players = set(name.strip() for name in players_part.split(",") if name.strip())
    
    print(f"Found whitelisted players: {whitelisted_players}")
    
    # Get roles
    target_role = guild.get_role(1371766288942370878)  # Role to check
    not_whitelisted_role = guild.get_role(1380984484551327775)  # Role to assign if not whitelisted
    
    if not target_role:
        await interaction.followup.send("Target role (1371766288942370878) not found in server.", ephemeral=True)
        return
    
    if not not_whitelisted_role:
        await interaction.followup.send("Not-whitelisted role (1380984484551327775) not found in server.", ephemeral=True)
        return
    
    # Get all members with the target role
    members_with_role = [member for member in guild.members if target_role in member.roles]
    
    cleaned_entries = []
    valid_links = {}
    not_in_database = []
    removed_from_server = []
    not_whitelisted_members = []
    
    # Check members with role
    for member in members_with_role:
        discord_id = str(member.id)
        
        if discord_id not in links:
            not_in_database.append(f"{member.display_name} ({member.mention})")
        else:
            minecraft_name = links[discord_id]
            
            # Check if their minecraft name is actually whitelisted
            if minecraft_name.lower() not in [name.lower() for name in whitelisted_players]:
                not_whitelisted_members.append(f"{member.display_name} → {minecraft_name}")
                
                # Add the not-whitelisted role
                try:
                    await member.add_roles(not_whitelisted_role)
                    print(f"Added not-whitelisted role to {member.display_name}")
                except discord.Forbidden:
                    print(f"Could not add role to {member.display_name} - insufficient permissions")
                except Exception as e:
                    print(f"Error adding role to {member.display_name}: {e}")
    
    # Clean up database entries
    for discord_id, minecraft_name in links.items():
        # Skip manual entries
        if discord_id.startswith("manual_"):
            valid_links[discord_id] = minecraft_name
            continue
        
        # Check if Discord user still exists in server
        try:
            member = guild.get_member(int(discord_id))
            if member:
                valid_links[discord_id] = minecraft_name
            else:
                # User not in server - remove from whitelist and database
                remove_result = execute_rcon_command(f"whitelist remove {minecraft_name}")
                if remove_result["status"] == "success":
                    removed_from_server.append(f"Removed {minecraft_name} from whitelist (user left server)")
                else:
                    removed_from_server.append(f"Failed to remove {minecraft_name} from whitelist: {remove_result['message']}")
                
                cleaned_entries.append(f"Removed: {minecraft_name} (Discord user not in server)")
        except ValueError:
            cleaned_entries.append(f"Removed: {minecraft_name} (Invalid Discord ID: {discord_id})")
    
    set_value("links", valid_links)
    
    # Create comprehensive report
    embed = discord.Embed(title="Database Cleanup & Whitelist Check Results", color=discord.Color.yellow())
    
    report_sections = []
    
    if not_in_database:
        report_sections.append(f"**Members with role but not in database ({len(not_in_database)}):**\n" + "\n".join(not_in_database))
    
    if not_whitelisted_members:
        report_sections.append(f"**Members not actually whitelisted - given role ({len(not_whitelisted_members)}):**\n" + "\n".join(not_whitelisted_members))
    
    if removed_from_server:
        report_sections.append(f"**Removed from server whitelist ({len(removed_from_server)}):**\n" + "\n".join(removed_from_server))
    
    if cleaned_entries:
        report_sections.append(f"**Database entries cleaned ({len(cleaned_entries)}):**\n" + "\n".join(cleaned_entries))
    
    if not report_sections:
        embed.description = "✅ Everything looks good! No issues found."
    else:
        # Split into multiple embeds if too long
        full_report = "\n\n".join(report_sections)
        if len(full_report) <= 4000:
            embed.description = full_report
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # Send in chunks
            embed.description = f"Found {len(report_sections)} types of issues. Sending detailed report..."
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            for section in report_sections:
                section_embed = discord.Embed(title="Cleanup Report (continued)", description=section, color=discord.Color.yellow())
                await interaction.followup.send(embed=section_embed, ephemeral=True)
        return
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="test_rcon_connection", description="Test RCON connectivity to the server (Admin Only).")
@app_commands.checks.has_permissions(administrator=True)
async def test_rcon_connection(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    # A simple command like 'list' or 'version' is good for testing
    result = execute_rcon_command("list")     
    if result["status"] == "success":
        await interaction.followup.send(f"RCON connection successful! Response: ```{result['message']}```")
    else:
        await interaction.followup.send(f"RCON connection failed: {result['message']}")


# --- Main Execution ---
if __name__ == "__main__":
    bot_token = get_value("token")
    if not bot_token:
        print("Bot token not found in database. Running initial setup...")
        # Try to run initial setup if essential configs are missing
        # This helps on first run if DB is empty.
        initial_setup()
        bot_token = get_value("token")
        if not bot_token:
            print("CRITICAL: Bot token is still not set after setup attempt. Exiting.")
            exit() # Or raise an error

    print(f"Attempting to start bot with token from DB...")
    try:
        bot.run(bot_token)
    except discord.LoginFailure:
        print("CRITICAL: Failed to log in with the provided bot token. Please check the token in your 'mydb' shelve database or re-run setup.")
    except Exception as e:
        print(f"An unexpected error occurred while running the bot: {e}")
