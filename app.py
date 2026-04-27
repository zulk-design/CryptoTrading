from flask import Flask
import subprocess

app = Flask(__name__)

@app.route("/")
def run_bot():
    result = subprocess.run(
        ["python", "main.py", "--once", "--no-trade"],
        capture_output=True,
        text=True
    )
    return f"<pre>{result.stdout}\n{result.stderr}</pre>"

app.run(host="0.0.0.0", port=8080)
