import os
import anthropic
from django.core.mail import message
from dotenv import load_dotenv

# Load variables from envs.local
load_dotenv("envs.local")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def message_claude(prompt):
    message = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": prompt}
    ] 
    )
    return message.content[0].text