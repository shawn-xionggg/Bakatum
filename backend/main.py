import os
from dotenv import load_dotenv
from steel import Steel

load_dotenv("envs.local")

client = Steel(
    steel_api_key=os.getenv("STEEL_API_KEY")
)


session = client.sessions.create()
print("Session created:", session.id)
client.sessions.release(session.id)
