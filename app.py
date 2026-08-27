from flask import Flask, request, jsonify, send_from_directory, Response
from openai import OpenAI
from dotenv import load_dotenv
import sqlite3
import os
import json

# =========================
# PATHS
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# =========================
# FLASK
# =========================

app = Flask(__name__)

# =========================
# OPENROUTER
# =========================

API_KEY = os.getenv("OPENROUTER_API_KEY")

print("OPENROUTER KEY:", bool(API_KEY))

if API_KEY:
    client = OpenAI(
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=60.0,
        max_retries=1
    )
else:
    client = None


# =========================
# DATABASE
# =========================
#
# استفاده از /tmp باعث می‌شود دیتابیس قدیمی
# موجود در image باعث مشکل نشود.
#

DB_FILE = os.path.join("/tmp", "whale_ai.db")


def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    # همیشه مطمئن شو جدول‌ها وجود دارند
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'گفت‌وگوی جدید',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
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


# =========================
# CREATE CONVERSATION
# =========================

def create_conversation():

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO conversations (title)
        VALUES (?)
        """,
        ("گفت‌وگوی جدید",)
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return conversation_id


# =========================
# HOME
# =========================

@app.route("/")
def index():

    index_path = os.path.join(
        BASE_DIR,
        "index.html"
    )

    if not os.path.exists(index_path):

        return (
            "index.html پیدا نشد.",
            404
        )

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():

    try:

        conn = get_db()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) AS count FROM conversations"
        )

        cursor.fetchone()

        conn.close()

        return jsonify({
            "status": "ok",
            "database": "ok",
            "openrouter_key": bool(API_KEY)
        })

    except Exception as error:

        print(
            "HEALTH ERROR:",
            repr(error)
        )

        return jsonify({
            "status": "error",
            "error": str(error)
        }), 500


# =========================
# NEW CONVERSATION
# =========================

@app.route(
    "/conversations",
    methods=["POST"]
)
def new_conversation():

    try:

        conversation_id = (
            create_conversation()
        )

        return jsonify({
            "id": conversation_id,
            "title": "گفت‌وگوی جدید"
        })

    except Exception as error:

        print(
            "NEW CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در ساخت گفتگو."
        }), 500


# =========================
# GET CONVERSATIONS
# =========================

@app.route(
    "/conversations",
    methods=["GET"]
)
def get_conversations():

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                c.id,
                c.title,
                c.created_at,
                COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m
            ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.id DESC
        """)

        rows = cursor.fetchall()

        conn.close()

        return jsonify([
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "message_count": row["message_count"]
            }
            for row in rows
        ])

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت تاریخچه."
        }), 500


# =========================
# GET MESSAGES
# =========================

@app.route(
    "/conversations/<int:conversation_id>/messages"
)
def get_messages(conversation_id):

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id,
                role,
                content,
                created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (
            conversation_id,
        ))

        rows = cursor.fetchall()

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

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در دریافت پیام‌ها."
        }), 500


# =========================
# CHAT
# =========================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        text = (
            data.get(
                "message",
                ""
            )
            .strip()
        )

        conversation_id = (
            data.get(
                "conversation_id"
            )
        )

        if not text:

            return jsonify({
                "error": "پیامی دریافت نشد."
            }), 400

        if not API_KEY:

            return jsonify({
                "error":
                    "OPENROUTER_API_KEY تنظیم نشده است."
            }), 500

        # اگر conversation وجود ندارد،
        # یک conversation جدید بساز
        if not conversation_id:

            conversation_id = (
                create_conversation()
            )

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        )

        conversation = (
            cursor.fetchone()
        )

        if not conversation:

            # اگر ID قدیمی/نامعتبر بود
            # یک گفتگوی جدید بساز
            conn.close()

            conversation_id = (
                create_conversation()
            )

            conn = get_db()
            cursor = conn.cursor()

        # ذخیره پیام کاربر
        cursor.execute("""
            INSERT INTO messages
            (
                conversation_id,
                role,
                content
            )
            VALUES (?, ?, ?)
        """, (
            conversation_id,
            "user",
            text
        ))

        conn.commit()

        # دریافت تاریخچه
        cursor.execute("""
            SELECT
                role,
                content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (
            conversation_id,
        ))

        history = cursor.fetchall()

        conn.close()

        messages = [
            {
                "role": "system",
                "content": (
                    "تو Whale AI هستی. "
                    "دقیق، مفید و طبیعی پاسخ بده. "
                    "اگر کاربر فارسی صحبت کرد، "
                    "فارسی پاسخ بده."
                )
            }
        ]

        for item in history:

            messages.append({
                "role": item["role"],
                "content": item["content"]
            })

        # =========================
        # STREAM RESPONSE
        # =========================

        def generate():

            full_reply = ""

            try:

                print(
                    "در حال دریافت پاسخ..."
                )

                response = (
                    client.chat.completions.create(
                        model="openrouter/free",
                        messages=messages,
                        temperature=0.7,
                        stream=True
                    )
                )

                for chunk in response:

                    if not chunk.choices:
                        continue

                    delta = (
                        chunk.choices[0]
                        .delta
                        .content
                    )

                    if delta:

                        full_reply += delta

                        yield (
                            json.dumps(
                                {
                                    "type": "text",
                                    "content": delta
                                },
                                ensure_ascii=False
                            )
                            + "\n"
                        )

                if not full_reply:

                    full_reply = (
                        "پاسخی دریافت نشد."
                    )

                # ذخیره پاسخ AI
                conn = get_db()
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO messages
                    (
                        conversation_id,
                        role,
                        content
                    )
                    VALUES (?, ?, ?)
                """, (
                    conversation_id,
                    "assistant",
                    full_reply
                ))

                # تعداد پیام‌ها
                cursor.execute("""
                    SELECT COUNT(*) AS count
                    FROM messages
                    WHERE conversation_id = ?
                """, (
                    conversation_id,
                ))

                count = (
                    cursor.fetchone()["count"]
                )

                # عنوان گفتگو
                if count == 2:

                    title = text[:40]

                    if len(text) > 40:

                        title += "..."

                    cursor.execute("""
                        UPDATE conversations
                        SET title = ?
                        WHERE id = ?
                    """, (
                        title,
                        conversation_id
                    ))

                conn.commit()
                conn.close()

                yield (
                    json.dumps(
                        {
                            "type": "done",
                            "conversation_id":
                                conversation_id
                        },
                        ensure_ascii=False
                    )
                    + "\n"
                )

            except Exception as error:

                print(
                    "AI ERROR:",
                    repr(error)
                )

                yield (
                    json.dumps(
                        {
                            "type": "error",
                            "content":
                                "ارتباط با هوش مصنوعی برقرار نشد."
                        },
                        ensure_ascii=False
                    )
                    + "\n"
                )

        return Response(
            generate(),
            mimetype="application/x-ndjson"
        )

    except Exception as error:

        print(
            "CHAT ERROR:",
            repr(error)
        )

        return jsonify({
            "error": str(error)
        }), 500


# =========================
# DELETE ONE
# =========================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(
    conversation_id
):

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            """
            DELETE FROM messages
            WHERE conversation_id = ?
            """,
            (conversation_id,)
        )

        cursor.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True
        })

    except Exception as error:

        print(
            "DELETE ERROR:",
            repr(error)
        )

        return jsonify({
            "error": "خطا در حذف گفتگو."
        }), 500


# =========================
# DELETE ALL
# =========================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

    try:

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM messages"
        )

        cursor.execute(
            "DELETE FROM conversations"
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success": True
        })

    except Exception as error:

        print(
            "DELETE ALL ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "خطا در حذف گفتگوها."
        }), 500


# =========================
# START
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    print("==============================")
    print("          WHALE AI")
    print("==============================")
    print("Server starting...")
    print("PORT:", port)
    print("DATABASE:", DB_FILE)
    print("==============================")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
