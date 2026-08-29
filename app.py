from flask import Flask, request, jsonify, send_from_directory
import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = "/tmp/whale_ai.db"

API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'گفتگوی جدید',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


@app.route("/")
def home():
    index_file = os.path.join(BASE_DIR, "index.html")

    if os.path.exists(index_file):
        return send_from_directory(BASE_DIR, "index.html")

    return "Whale AI is running."


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "openrouter_key": bool(API_KEY)
    }), 200


@app.route("/conversations", methods=["GET"])
def get_conversations():
    conn = get_db()

    rows = conn.execute("""
        SELECT id, title, created_at
        FROM conversations
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"]
        }
        for row in rows
    ])


@app.route("/conversations", methods=["POST"])
def create_conversation():
    conn = get_db()

    cursor = conn.execute(
        "INSERT INTO conversations (title) VALUES (?)",
        ("گفتگوی جدید",)
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return jsonify({
        "id": conversation_id,
        "title": "گفتگوی جدید"
    })


@app.route("/conversations/<int:conversation_id>/messages")
def get_messages(conversation_id):
    conn = get_db()

    rows = conn.execute("""
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
    """, (conversation_id,)).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"]
        }
        for row in rows
    ])


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {}

        user_message = str(
            data.get("message", "")
        ).strip()

        conversation_id = data.get("conversation_id")

        if not user_message:
            return jsonify({
                "error": "پیام خالی است."
            }), 400

        if not API_KEY:
            return jsonify({
                "error": "OPENROUTER_API_KEY تنظیم نشده است."
            }), 500

        if not conversation_id:
            conn = get_db()

            cursor = conn.execute(
                "INSERT INTO conversations (title) VALUES (?)",
                (user_message[:40],)
            )

            conversation_id = cursor.lastrowid

            conn.commit()
            conn.close()

        conn = get_db()

        rows = conn.execute("""
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (conversation_id,)).fetchall()

        conn.execute("""
            INSERT INTO messages
            (conversation_id, role, content)
            VALUES (?, ?, ?)
        """, (
            conversation_id,
            "user",
            user_message
        ))

        conn.commit()
        conn.close()

        messages = [
            {
                "role": "system",
                "content": (
                    "تو Whale AI هستی. "
                    "دقیق و مفید پاسخ بده. "
                    "اگر کاربر فارسی صحبت کرد، فارسی پاسخ بده."
                )
            }
        ]

        for row in rows:
            messages.append({
                "role": row["role"],
                "content": row["content"]
            })

        messages.append({
            "role": "user",
            "content": user_message
        })

        print("================================")
        print("WHALE AI")
        print("OPENROUTER KEY:", bool(API_KEY))
        print("SENDING REQUEST TO OPENROUTER")
        print("================================")

        headers = {
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://snapdeploy.dev",
            "X-Title": "Whale AI"
        }

        payload = {
            "model": "openrouter/free",
            "messages": messages,
            "temperature": 0.7,
            "stream": False
        }

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=120
        )

        print(
            "OPENROUTER STATUS:",
            response.status_code
        )

        if response.status_code != 200:
            print(response.text[:3000])

            return jsonify({
                "error": "OpenRouter خطا داد.",
                "status": response.status_code
            }), 502

        result = response.json()

        reply = result["choices"][0]["message"]["content"]

        conn = get_db()

        conn.execute("""
            INSERT INTO messages
            (conversation_id, role, content)
            VALUES (?, ?, ?)
        """, (
            conversation_id,
            "assistant",
            reply
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "type": "done",
            "content": reply,
            "conversation_id": conversation_id
        })

    except requests.RequestException as error:
        print("OPENROUTER CONNECTION ERROR:", repr(error))

        return jsonify({
            "error": "ارتباط با OpenRouter برقرار نشد."
        }), 502

    except Exception as error:
        print("CHAT ERROR:", repr(error))

        return jsonify({
            "error": "خطای داخلی سرور."
        }), 500


@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(conversation_id):
    conn = get_db()

    conn.execute(
        "DELETE FROM messages WHERE conversation_id = ?",
        (conversation_id,)
    )

    conn.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


@app.route("/conversations", methods=["DELETE"])
def delete_all():
    conn = get_db()

    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM conversations")

    conn.commit()
    conn.close()

    return jsonify({
        "success": True
    })


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "5000")
    )

    print("WHALE AI STARTING")
    print("PORT:", port)
    print("OPENROUTER KEY:", bool(API_KEY))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
