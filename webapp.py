# webapp.py
from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
from urllib.parse import urlencode
import os
import time # For player skin caching logic if directly used here

# Import database functions
from database import get_value, set_value, add_application_to_queue, get_cached_player_skin, cache_player_skin

app = Flask(__name__)

# Create a templates directory and add your HTML files there
# templates/index.html
# templates/whitelist.html
# templates/success.html

# Ensure templates directory exists and create placeholder files if they don't
if not os.path.exists("templates"):
    os.makedirs("templates")

default_html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
</head>
<body>
    <h1>{heading}</h1>
    <p>{message}</p>
    {extra_content}
</body>
</html>
"""

if not os.path.exists("templates/index.html"):
    with open("templates/index.html", "w") as f:
        f.write(default_html_content.format(title="Home", heading="Welcome!", message="This is the main page.", extra_content="<a href='/whitelist'>Apply for Whitelist</a>"))

if not os.path.exists("templates/whitelist.html"):
    with open("templates/whitelist.html", "w") as f:
        # A very basic form example
        form_content = """
        <form id="whitelistForm">
            <label for="in_game_name">Minecraft In-Game Name:</label><br>
            <input type="text" id="in_game_name" name="in_game_name" required><br>
            <label for="why_join">Why do you want to join?:</label><br>
            <textarea id="why_join" name="why_join" required></textarea><br><br>
            <input type="hidden" id="code" name="code" value="{{ code }}">
            <button type="submit">Submit Application</button>
        </form>
        <script>
            document.getElementById('whitelistForm').addEventListener('submit', async function(event) {
                event.preventDefault();
                const formData = new FormData(event.target);
                const data = Object.fromEntries(formData.entries());
                
                const response = await fetch('/submit', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(data),
                });
                
                if (response.ok) {
                    window.location.href = '/success';
                } else {
                    alert('Submission failed. Please try again.');
                }
            });
        </script>
        """
        f.write(default_html_content.format(title="Whitelist Application", heading="Whitelist Application Form", message="Please fill out the form below.", extra_content=form_content))

if not os.path.exists("templates/success.html"):
    with open("templates/success.html", "w") as f:
        f.write(default_html_content.format(title="Success", heading="Application Submitted!", message="Your application has been submitted successfully.", extra_content=""))


# Mojang API interaction with caching
def get_player_skin(username):
    cached_data = get_cached_player_skin(username)
    if cached_data:
        return cached_data
    
    try:
        uuid_response = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{username}")
        if uuid_response.status_code != 200:
            return None
        uuid = uuid_response.json()['id']
        
        profile_response = requests.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}")
        if profile_response.status_code != 200:
            return None
            
        profile_data = profile_response.json()
        player_data = {
            'uuid': uuid,
            'name': profile_data['name'],
            'profile': profile_data # Contains skin data if needed
        }
        cache_player_skin(username, player_data)
        return player_data
    except Exception as e:
        print(f"Error fetching player skin for {username}: {e}")
        return None

@app.route('/api/whitelisted-players')
def whitelisted_players_api():
    links = get_value("links") or {}
    players = []
    for discord_id, minecraft_name in links.items():
        player_data = get_player_skin(minecraft_name) # Uses caching
        player_info = {
            'name': minecraft_name,
            'discord_id': discord_id
        }
        if player_data:
            player_info['uuid'] = player_data['uuid']
        players.append(player_info)
    return jsonify(players)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/whitelist')
def whitelist_form():
    # This 'code' is now the Discord User ID after callback
    user_id = request.args.get('code')
    if user_id:
        return render_template("whitelist.html", code=user_id)
    
    # No code, redirect to Discord OAuth
    client_id = get_value("client_id")
    domain = get_value("domain")
    if not client_id or not domain:
        return "Error: Discord application not configured properly. Missing Client ID or Domain.", 500
    
    redirect_uri = f"https://{domain}/callback"
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': 'identify' # Basic scope to get user ID
    }
    auth_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    auth_code = request.args.get('code')
    if not auth_code:
        return "Error: No authorization code provided by Discord.", 400

    client_id = get_value("client_id")
    client_secret = get_value("secret")
    domain = get_value("domain")

    if not client_id or not client_secret or not domain:
        return "Error: Discord application not configured properly on the server.", 500

    redirect_uri = f"https://{domain}/callback"
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': auth_code,
        'redirect_uri': redirect_uri
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    try:
        token_response = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
        token_response.raise_for_status() # Raises an exception for bad status codes
        access_token = token_response.json()['access_token']

        user_info_headers = {'Authorization': f'Bearer {access_token}'}
        user_response = requests.get('https://discord.com/api/users/@me', headers=user_info_headers)
        user_response.raise_for_status()
        user_id = user_response.json()['id']

        # Redirect to the whitelist form, passing the user_id as 'code' query parameter
        return redirect(url_for('whitelist_form', code=user_id))
    except requests.exceptions.RequestException as e:
        print(f"OAuth Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response content: {e.response.text}")
        return f"Error during Discord OAuth: {e}", 500
    
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json
    
    # Validate required fields
    if not data:
        return jsonify({"status": "error", "message": "No data provided"}), 400
    
    # Check for required fields
    if 'code' not in data:
        return jsonify({"status": "error", "message": "Discord user ID (code) is required"}), 400
    
    if 'in_game_name' not in data:
        return jsonify({"status": "error", "message": "Minecraft username is required"}), 400
    
    # Log the submission
    print(f"Received whitelist application: {data}")
    
    # Ensure all required data is present in the expected format for the Discord bot
    formatted_data = {
        'code': data['code'],                                   # Discord User ID
        'in_game_name': data['in_game_name'],                   # Minecraft username
        'playtime_experience': data.get('playtime_experience', 'Not provided'),
        'about_me': data.get('about_me', 'Not provided'),
        'public_profile': data.get('public_profile', False)
    }
    
    # Add to the application queue for the Discord bot to process
    add_application_to_queue(formatted_data)
    
    return jsonify({
        "status": "success", 
        "message": "Application submitted for review. Please check Discord for updates."
    }), 200

@app.route('/success')
def success():
    return render_template("success.html")

if __name__ == "__main__":
    # For development only. Use Gunicorn or similar for production.
    # Ensure initial setup is run if this is the first time
    # from database import initial_setup
    # initial_setup() # You might want to run this separately
    app.run(host='0.0.0.0', port=80, debug=True) # Changed port for clarity

