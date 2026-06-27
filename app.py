from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import os
import uuid
import json
from datetime import datetime
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

def groq_signal(text):
    prompt = f"""You are an AI content detector. Analyze the following text and determine 
the probability that it was AI-generated (vs written by a human).

Respond with ONLY a JSON object in this exact format:
{{"ai_probability": 0.0, "reasoning": "brief explanation"}}

The ai_probability should be a float between 0 and 1, where:
- 1.0 = certainly AI-generated
- 0.0 = certainly human-written

Text to analyze:
{text}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    
    result = json.loads(response.choices[0].message.content)
    return result["ai_probability"]

LOG_FILE = "audit_log.json"

def write_log(entry):
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

def read_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        return json.load(f)
    
@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()
    text = data.get("text")
    creator_id = data.get("creator_id")
    content_id = str(uuid.uuid4())

    llm_score = groq_signal(text)

    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "attribution": "likely_ai" if llm_score > 0.5 else "likely_human",
        "confidence": llm_score,
        "llm_score": llm_score,
        "status": "classified"
    }
    write_log(entry)

    return jsonify({
        "content_id": content_id,
        "attribution": entry["attribution"],
        "confidence": llm_score,
        "label": "placeholder — coming in M5"
    })

@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": read_log()})

if __name__ == "__main__":
    app.run(debug=True)