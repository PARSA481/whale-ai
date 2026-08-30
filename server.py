import os
import json
import time
import sqlite3
import requests

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    Response
)

# =========================================================
# OPTIONAL FILE READERS
# =========================================================

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from docx import Document
except Exception:
    Document = None


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "whale.db")

OPENROUTER_URL = (
    "https://openrouter.ai/api/v1/chat/completions"
)

# =========================================================
# MODEL
# =========================================================

DEFAULT_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "minimax/minimax-m3:free"
).strip()

# Fallback can be changed from SnapDeploy environment variables.
# Leave empty if you do not want fallback.
FALLBACK_MODEL = os.environ.get(
    "OPENROUTER_FALLBACK_MODEL",
    ""
).strip()

# =========================================================
# LIMITS
# =========================================================

MAX_HISTORY_MESSAGES = 80
MAX_MEMORY_ITEMS = 100
MAX_FILE_CHARS = 100000
MAX_MESSAGE_CHARS = 30000
MAX_CONVERSATION_TITLE = 80

REQUEST_CONNECT_TIMEOUT = 20
REQUEST_READ_TIMEOUT = 180

MAX_RETRIES = 2

# =========================================================
# API KEY
# =========================================================

def get_api_key():

    key = os.environ.get(
        "OPENROUTER_API_KEY",
        ""
    ).strip()

    if key.lower().startswith("bearer "):
        key = key[7:].strip()

    return key


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    conn.execute("""
        PRAGMA journal_mode=WAL
    """)

    conn.execute("""
        PRAGMA foreign_keys=ON
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(conversation_id)
                REFERENCES conversations(id)
                ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    return conn


# =========================================================
# DATABASE MIGRATION
# =========================================================

def migrate_database():

    conn = get_db()

    columns = conn.execute(
        "PRAGMA table_info(conversations)"
    ).fetchall()

    column_names = {
        row["name"]
        for row in columns
    }

    if "updated_at" not in column_names:

        conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN updated_at
            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """)

    conn.commit()
    conn.close()


migrate_database()


# =========================================================
# MEMORY
# =========================================================

def get_memories():

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            key,
            value,
            created_at,
            updated_at
        FROM memories
        ORDER BY id ASC
        LIMIT ?
    """, (MAX_MEMORY_ITEMS,)).fetchall()

    conn.close()

    return [
        {
            "id": row["id"],
            "key": row["key"],
            "value": row["value"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }
        for row in rows
    ]


def save_memory(key, value):

    key = str(key).strip()
    value = str(value).strip()

    if not key or not value:
        return

    conn = get_db()

    existing = conn.execute(
        """
        SELECT id
        FROM memories
        WHERE key = ?
        """,
        (key,)
    ).fetchone()

    if existing:

        conn.execute(
            """
            UPDATE memories
            SET value = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = ?
            """,
            (value, key)
        )

    else:

        conn.execute(
            """
            INSERT INTO memories
            (
                key,
                value
            )
            VALUES (?, ?)
            """,
            (
                key,
                value
            )
        )

    conn.commit()
    conn.close()


def delete_memory(key):

    conn = get_db()

    conn.execute(
        """
        DELETE FROM memories
        WHERE key = ?
        """,
        (key,)
    )

    conn.commit()
    conn.close()


def clear_memories():

    conn = get_db()

    conn.execute(
        "DELETE FROM memories"
    )

    conn.commit()
    conn.close()


def build_memory_context():

    memories = get_memories()

    if not memories:
        return ""

    lines = []

    for memory in memories:

        lines.append(
            "- {}: {}".format(
                memory["key"],
                memory["value"]
            )
        )

    return (
        "\n\n"
        "IMPORTANT USER MEMORY:\n"
        + "\n".join(lines)
        + "\n"
    )


# =========================================================
# FILE EXTRACTION
# =========================================================

def extract_file_text(file):

    filename = file.filename or ""

    extension = (
        os.path.splitext(filename)[1]
        .lower()
        .replace(".", "")
    )

    # -------------------------
    # TEXT FILES
    # -------------------------

    if extension in (
        "txt",
        "md",
        "csv",
        "json",
        "xml",
        "html",
        "py",
        "js",
        "css",
        "java",
        "cpp",
        "c",
        "sql"
    ):

        raw = file.read()

        try:

            text = raw.decode(
                "utf-8"
            )

        except UnicodeDecodeError:

            text = raw.decode(
                "utf-8",
                errors="replace"
            )

        return (
            text[:MAX_FILE_CHARS],
            extension
        )


    # -------------------------
    # PDF
    # -------------------------

    if extension == "pdf":

        if PdfReader is None:

            raise RuntimeError(
                "PDF support is not installed. "
                "Install pypdf."
            )

        reader = PdfReader(file)

        parts = []
        total = 0

        for page in reader.pages:

            try:

                page_text = (
                    page.extract_text()
                    or ""
                )

            except Exception:

                page_text = ""

            if page_text:

                parts.append(
                    page_text
                )

                total += len(
                    page_text
                )

            if total >= MAX_FILE_CHARS:
                break

        text = "\n\n".join(parts)

        return (
            text[:MAX_FILE_CHARS],
            extension
        )


    # -------------------------
    # DOCX
    # -------------------------

    if extension == "docx":

        if Document is None:

            raise RuntimeError(
                "DOCX support is not installed. "
                "Install python-docx."
            )

        document = Document(file)

        paragraphs = []

        for paragraph in document.paragraphs:

            text = paragraph.text.strip()

            if text:

                paragraphs.append(
                    text
                )

        text = "\n".join(
            paragraphs
        )

        return (
            text[:MAX_FILE_CHARS],
            extension
        )


    raise ValueError(
        "Unsupported file type: .{}".format(
            extension
        )
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    api_key = get_api_key()

    database = False

    try:

        conn = get_db()

        conn.execute(
            "SELECT 1"
        ).fetchone()

        conn.close()

        database = True

    except Exception as error:

        print(
            "HEALTH DATABASE ERROR:",
            repr(error)
        )

    return jsonify({

        "status":
            "ok",

        "database":
            database,

        "openrouter_key":
            bool(api_key),

        "key_length":
            len(api_key),

        "model":
            DEFAULT_MODEL,

        "fallback_model":
            FALLBACK_MODEL,

        "web_search":
            True,

        "memory":
            True,

        "file_upload":
            True

    })


# =========================================================
# UPLOAD
# =========================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload_file():

    try:

        if "file" not in request.files:

            return jsonify({
                "error":
                    "No file was uploaded."
            }), 400

        file = request.files["file"]

        if not file.filename:

            return jsonify({
                "error":
                    "The file has no name."
            }), 400

        text, extension = (
            extract_file_text(file)
        )

        return jsonify({

            "success":
                True,

            "filename":
                file.filename,

            "extension":
                extension,

            "text":
                text,

            "characters":
                len(text)

        })

    except ValueError as error:

        return jsonify({
            "error":
                str(error)
        }), 400

    except Exception as error:

        print(
            "UPLOAD ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "File processing failed.",

            "details":
                str(error)

        }), 500


# =========================================================
# CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["GET"]
)
def get_conversations():

    try:

        conn = get_db()

        rows = conn.execute("""
            SELECT
                c.id,
                c.title,
                c.created_at,
                c.updated_at,
                COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m
                ON m.conversation_id = c.id
            GROUP BY
                c.id,
                c.title,
                c.created_at,
                c.updated_at
            ORDER BY
                c.updated_at DESC,
                c.id DESC
        """).fetchall()

        conn.close()

        return jsonify([

            {
                "id":
                    row["id"],

                "title":
                    row["title"],

                "created_at":
                    row["created_at"],

                "updated_at":
                    row["updated_at"],

                "message_count":
                    row["message_count"]
            }

            for row in rows

        ])

    except Exception as error:

        print(
            "GET CONVERSATIONS ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Failed to load conversations.",

            "details":
                str(error)

        }), 500


# =========================================================
# CREATE CONVERSATION
# =========================================================

@app.route(
    "/conversations",
    methods=["POST"]
)
def create_conversation():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        title = str(
            data.get(
                "title",
                "New Chat"
            )
        ).strip()

        if not title:
            title = "New Chat"

        title = title[
            :MAX_CONVERSATION_TITLE
        ]

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO conversations
            (
                title
            )
            VALUES (?)
            """,
            (title,)
        )

        conversation_id = (
            cursor.lastrowid
        )

        conn.commit()
        conn.close()

        return jsonify({

            "id":
                conversation_id,

            "title":
                title

        })

    except Exception as error:

        print(
            "CREATE CONVERSATION ERROR:",
            repr(error)
        )

        return jsonify({
            "error":
                "Failed to create conversation."
        }), 500


# =========================================================
# RENAME CONVERSATION
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["PATCH"]
)
def rename_conversation(
    conversation_id
):

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        title = str(
            data.get(
                "title",
                ""
            )
        ).strip()

        if not title:

            return jsonify({
                "error":
                    "Title is required."
            }), 400

        title = title[
            :MAX_CONVERSATION_TITLE
        ]

        conn = get_db()

        conn.execute(
            """
            UPDATE conversations
            SET title = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                title,
                conversation_id
            )
        )

        conn.commit()
        conn.close()

        return jsonify({

            "success":
                True,

            "id":
                conversation_id,

            "title":
                title

        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to rename conversation.",

            "details":
                str(error)

        }), 500


# =========================================================
# GET MESSAGES
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/messages",
    methods=["GET"]
)
def get_messages(
    conversation_id
):

    try:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT
                id,
                role,
                content,
                created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (
                conversation_id,
            )
        ).fetchall()

        conn.close()

        return jsonify([

            {
                "id":
                    row["id"],

                "role":
                    row["role"],

                "content":
                    row["content"],

                "created_at":
                    row["created_at"]
            }

            for row in rows

        ])

    except Exception as error:

        print(
            "GET MESSAGES ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Failed to load messages.",

            "details":
                str(error)

        }), 500


# =========================================================
# SEARCH CONVERSATIONS
# =========================================================

@app.route(
    "/conversations/search",
    methods=["GET"]
)
def search_conversations():

    query = str(
        request.args.get(
            "q",
            ""
        )
    ).strip()

    if not query:

        return jsonify([])

    try:

        conn = get_db()

        pattern = "%" + query + "%"

        rows = conn.execute(
            """
            SELECT DISTINCT
                c.id,
                c.title,
                c.created_at,
                c.updated_at
            FROM conversations c
            LEFT JOIN messages m
                ON m.conversation_id = c.id
            WHERE
                c.title LIKE ?
                OR m.content LIKE ?
            ORDER BY
                c.updated_at DESC
            LIMIT 50
            """,
            (
                pattern,
                pattern
            )
        ).fetchall()

        conn.close()

        return jsonify([

            {
                "id":
                    row["id"],

                "title":
                    row["title"],

                "created_at":
                    row["created_at"],

                "updated_at":
                    row["updated_at"]
            }

            for row in rows

        ])

    except Exception as error:

        return jsonify({

            "error":
                "Conversation search failed.",

            "details":
                str(error)

        }), 500


# =========================================================
# MEMORY GET
# =========================================================

@app.route(
    "/memory",
    methods=["GET"]
)
def memory_get():

    try:

        return jsonify({

            "memories":
                get_memories()

        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to load memory.",

            "details":
                str(error)

        }), 500


# =========================================================
# MEMORY ADD
# =========================================================

@app.route(
    "/memory",
    methods=["POST"]
)
def memory_add():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        key = str(
            data.get(
                "key",
                ""
            )
        ).strip()

        value = str(
            data.get(
                "value",
                ""
            )
        ).strip()

        if not key or not value:

            return jsonify({
                "error":
                    "key and value are required."
            }), 400

        save_memory(
            key,
            value
        )

        return jsonify({

            "success":
                True,

            "key":
                key,

            "value":
                value

        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to save memory.",

            "details":
                str(error)

        }), 500


# =========================================================
# MEMORY DELETE
# =========================================================

@app.route(
    "/memory/<path:key>",
    methods=["DELETE"]
)
def memory_delete_route(key):

    try:

        delete_memory(key)

        return jsonify({
            "success":
                True
        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to delete memory.",

            "details":
                str(error)

        }), 500


# =========================================================
# MEMORY DELETE ALL
# =========================================================

@app.route(
    "/memory",
    methods=["DELETE"]
)
def memory_delete_all():

    try:

        clear_memories()

        return jsonify({
            "success":
                True
        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to clear memories.",

            "details":
                str(error)

        }), 500


# =========================================================
# BUILD MODEL MESSAGES
# =========================================================

def build_model_messages(
    conversation_id,
    file_text="",
    mode="auto"
):

    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            role,
            content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            conversation_id,
            MAX_HISTORY_MESSAGES
        )
    ).fetchall()

    conn.close()

    rows = list(
        reversed(rows)
    )

    # -----------------------------------------------------
    # BASE SYSTEM PROMPT
    # -----------------------------------------------------

    system_prompt = """
You are Whale AI, an advanced general-purpose AI assistant.

LANGUAGE:
- Always answer in the same language as the user.
- If the user writes Persian, answer naturally in Persian.
- Do not randomly switch languages.

GENERAL:
- Be accurate, useful and direct.
- Do not repeat the user's question unnecessarily.
- Use Markdown when it improves readability.
- Use headings for long answers.
- Use numbered lists for procedures.
- Use code blocks for code.
- Never claim that you searched the web unless web search was actually performed.
- Never invent sources, links, citations or search results.
- If information may be outdated, say so.
- If you are uncertain, clearly say what is uncertain.
- Never expose API keys, environment variables containing secrets, or private server information.
- Do not reveal private memory unless it is relevant to the user's request.

EDUCATION:
- Explain difficult concepts step by step.
- Adapt explanations to the user's level.
- Give examples when useful.
- Do not make explanations unnecessarily complicated.

CODING:
- Prefer complete working code when the user asks for code.
- Preserve existing functionality when modifying code.
- Explain important changes briefly.

FILES:
- When a file is attached, use its contents when relevant.
- Do not pretend to have read information that was not extracted successfully.

WEB:
- If web-search results are supplied by the system/tool, use them carefully.
- Clearly distinguish current web information from general knowledge.

MEMORY:
- Treat stored memory as user-specific context.
- Do not invent memories.
"""

    # -----------------------------------------------------
    # MODES
    # -----------------------------------------------------

    mode = str(
        mode or "auto"
    ).lower().strip()

    if mode == "study":

        system_prompt += """

MODE: STUDY

Teach rather than merely giving the final answer.
Break complex subjects into understandable parts.
When useful, finish with a short recap or practice question.
"""

    elif mode == "research":

        system_prompt += """

MODE: RESEARCH

Prioritize factual accuracy.
When web search is enabled, use current sources.
Compare relevant information and identify uncertainty.
Do not fabricate references.
"""

    elif mode == "code":

        system_prompt += """

MODE: CODE

Act as a senior software engineer.
Focus on correctness, debugging, architecture and maintainability.
When modifying code, provide the complete required code when requested.
"""

    elif mode == "deep":

        system_prompt += """

MODE: DEEP ANALYSIS

Analyze the problem carefully.
Consider multiple possibilities and edge cases.
Give a structured conclusion.
Do not expose private chain-of-thought.
Provide concise reasoning summaries instead.
"""

    elif mode == "quick":

        system_prompt += """

MODE: QUICK

Answer concisely.
Avoid unnecessary explanation unless requested.
"""

    else:

        system_prompt += """

MODE: AUTO

Choose the appropriate response style automatically.
"""

    # -----------------------------------------------------
    # MEMORY
    # -----------------------------------------------------

    system_prompt += build_memory_context()

    # -----------------------------------------------------
    # FILE
    # -----------------------------------------------------

    if file_text:

        system_prompt += """

ATTACHED FILE CONTENT:

--- BEGIN FILE ---
{}
--- END FILE ---

Use this content when relevant.
""".format(
            file_text[:MAX_FILE_CHARS]
        )

    messages = [

        {
            "role":
                "system",

            "content":
                system_prompt
        }

    ]

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    for row in rows:

        role = row["role"]

        if role not in (
            "user",
            "assistant",
            "system"
        ):
            continue

        content = row["content"]

        messages.append({

            "role":
                role,

            "content":
                content

        })

    return messages


# =========================================================
# REQUEST HEADERS
# =========================================================

def build_headers(api_key):

    return {

        "Authorization":
            "Bearer " + api_key,

        "Content-Type":
            "application/json",

        "HTTP-Referer":
            os.environ.get(
                "SITE_URL",
                "https://whale-ai.local"
            ),

        "X-Title":
            "Whale AI"

    }


# =========================================================
# PAYLOAD
# =========================================================

def build_payload(
    model,
    messages,
    web_search=False,
    stream=False,
    mode="auto"
):

    payload = {

        "model":
            model,

        "messages":
            messages,

        "temperature":
            0.7,

        "stream":
            stream

    }

    # -----------------------------------------------------
    # WEB SEARCH
    # -----------------------------------------------------

    if web_search:

        payload["tools"] = [

            {
                "type":
                    "openrouter:web_search"
            },

            {
                "type":
                    "openrouter:web_fetch"
            }

        ]

        # Give the model permission to decide when
        # web tools are actually useful.
        payload["tool_choice"] = "auto"

    return payload


# =========================================================
# OPENROUTER ERROR
# =========================================================

def openrouter_error_response(
    response,
    model
):

    details = response.text[:5000]

    print()
    print("================================")
    print("OPENROUTER ERROR")
    print("================================")
    print("STATUS:", response.status_code)
    print("MODEL:", model)
    print("DETAILS:", details)
    print("================================")
    print()

    if response.status_code == 401:

        message = (
            "OpenRouter authentication failed. "
            "Check OPENROUTER_API_KEY."
        )

    elif response.status_code == 402:

        message = (
            "OpenRouter requires credits or "
            "payment for this request."
        )

    elif response.status_code == 403:

        message = (
            "OpenRouter rejected access to this model "
            "or tool."
        )

    elif response.status_code == 404:

        message = (
            "The selected OpenRouter model or endpoint "
            "was not found."
        )

    elif response.status_code == 408:

        message = (
            "The request timed out."
        )

    elif response.status_code == 429:

        message = (
            "OpenRouter rate limit reached. "
            "Please try again shortly."
        )

    elif 500 <= response.status_code <= 599:

        message = (
            "OpenRouter or the selected provider "
            "temporarily failed."
        )

    else:

        message = (
            "OpenRouter request failed."
        )

    return jsonify({

        "error":
            message,

        "status":
            response.status_code,

        "details":
            details,

        "model":
            model

    }), response.status_code


# =========================================================
# NORMALIZE RESPONSE
# =========================================================

def normalize_reply(
    result
):

    choices = result.get(
        "choices"
    )

    if not choices:
        return ""

    message_data = choices[0].get(
        "message",
        {}
    )

    reply = message_data.get(
        "content",
        ""
    )

    # Some APIs may return content blocks.
    if isinstance(
        reply,
        list
    ):

        parts = []

        for item in reply:

            if isinstance(
                item,
                dict
            ):

                text = item.get(
                    "text",
                    ""
                )

                if text:
                    parts.append(
                        text
                    )

            elif isinstance(
                item,
                str
            ):

                parts.append(
                    item
                )

        reply = "".join(
            parts
        )

    return str(
        reply
    ).strip()


# =========================================================
# SAVE MESSAGE
# =========================================================

def save_message(
    conversation_id,
    role,
    content
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO messages
        (
            conversation_id,
            role,
            content
        )
        VALUES (?, ?, ?)
        """,
        (
            conversation_id,
            role,
            content
        )
    )

    conn.execute(
        """
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            conversation_id,
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# CHAT REQUEST TO OPENROUTER
# =========================================================

def request_openrouter(
    api_key,
    model,
    payload
):

    headers = build_headers(
        api_key
    )

    last_response = None

    for attempt in range(
        MAX_RETRIES + 1
    ):

        try:

            response = requests.post(

                OPENROUTER_URL,

                headers=headers,

                json=payload,

                timeout=(
                    REQUEST_CONNECT_TIMEOUT,
                    REQUEST_READ_TIMEOUT
                )

            )

            last_response = response

            # Success
            if response.status_code == 200:

                return response

            # Retry temporary failures
            if response.status_code in (
                408,
                429,
                500,
                502,
                503,
                504
            ):

                if attempt < MAX_RETRIES:

                    wait = 1.5 * (
                        attempt + 1
                    )

                    time.sleep(
                        wait
                    )

                    continue

            return response

        except requests.Timeout:

            if attempt < MAX_RETRIES:

                time.sleep(
                    1.5 * (
                        attempt + 1
                    )
                )

                continue

            raise

        except requests.RequestException:

            if attempt < MAX_RETRIES:

                time.sleep(
                    1.5 * (
                        attempt + 1
                    )
                )

                continue

            raise

    return last_response


# =========================================================
# CHAT
# =========================================================

@app.route(
    "/chat",
    methods=["POST"]
)
def chat():

    try:

        # -------------------------------------------------
        # API KEY
        # -------------------------------------------------

        api_key = get_api_key()

        if not api_key:

            return jsonify({

                "error":
                    "OPENROUTER_API_KEY is not configured.",

                "status":
                    500

            }), 500

        # -------------------------------------------------
        # INPUT
        # -------------------------------------------------

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        user_message = str(
            data.get(
                "message",
                ""
            )
        ).strip()

        if len(user_message) > MAX_MESSAGE_CHARS:

            return jsonify({

                "error":
                    "Message is too long."

            }), 400

        file_name = str(
            data.get(
                "file_name",
                ""
            )
        ).strip()

        file_text = str(
            data.get(
                "file_text",
                ""
            )
        )

        if len(file_text) > MAX_FILE_CHARS:

            file_text = file_text[
                :MAX_FILE_CHARS
            ]

        web_search = bool(
            data.get(
                "web_search",
                False
            )
        )

        mode = str(
            data.get(
                "mode",
                "auto"
            )
        ).strip().lower()

        conversation_id = (
            data.get(
                "conversation_id"
            )
        )

        # -------------------------------------------------
        # VALIDATION
        # -------------------------------------------------

        if (
            not user_message
            and not file_text
        ):

            return jsonify({

                "error":
                    "Message is empty."

            }), 400

        # -------------------------------------------------
        # CONVERSATION
        # -------------------------------------------------

        conn = get_db()

        if conversation_id:

            try:

                conversation_id = int(
                    conversation_id
                )

            except (
                TypeError,
                ValueError
            ):

                conversation_id = None

        if conversation_id:

            exists = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = ?
                """,
                (
                    conversation_id,
                )
            ).fetchone()

            if not exists:

                conversation_id = None

        if not conversation_id:

            title_source = (
                user_message
                or file_name
                or "New Chat"
            )

            title_source = (
                title_source
                .replace(
                    "\n",
                    " "
                )
                .strip()
            )

            cursor = conn.execute(
                """
                INSERT INTO conversations
                (
                    title
                )
                VALUES (?)
                """,
                (
                    title_source[
                        :MAX_CONVERSATION_TITLE
                    ],
                )
            )

            conversation_id = (
                cursor.lastrowid
            )

        conn.commit()
        conn.close()

        # -------------------------------------------------
        # DISPLAY MESSAGE
        # -------------------------------------------------

        display_message = user_message

        if file_text:

            display_message += (
                "\n\n"
                "[Attached file: {}]"
                .format(
                    file_name
                    or "file"
                )
            )

        if not display_message:

            display_message = (
                "[Attached file: {}]"
                .format(
                    file_name
                    or "file"
                )
            )

        # -------------------------------------------------
        # SAVE USER
        # -------------------------------------------------

        save_message(
            conversation_id,
            "user",
            display_message
        )

        # -------------------------------------------------
        # BUILD MODEL MESSAGES
        # -------------------------------------------------

        messages = build_model_messages(
            conversation_id,
            file_text,
            mode
        )

        # -------------------------------------------------
        # PAYLOAD
        # -------------------------------------------------

        payload = build_payload(

            DEFAULT_MODEL,

            messages,

            web_search=web_search,

            stream=False,

            mode=mode

        )

        # -------------------------------------------------
        # REQUEST
        # -------------------------------------------------

        print()
        print("================================")
        print("WHALE AI REQUEST")
        print("================================")
        print(
            "Conversation:",
            conversation_id
        )
        print(
            "Model:",
            DEFAULT_MODEL
        )
        print(
            "Mode:",
            mode
        )
        print(
            "Web Search:",
            web_search
        )
        print(
            "API KEY:",
            "FOUND"
        )
        print(
            "Message:",
            user_message[:200]
        )
        print("================================")
        print()

        response = request_openrouter(

            api_key,

            DEFAULT_MODEL,

            payload

        )

        # -------------------------------------------------
        # FALLBACK
        # -------------------------------------------------

        if (
            response.status_code != 200
            and FALLBACK_MODEL
            and FALLBACK_MODEL != DEFAULT_MODEL
            and response.status_code in (
                408,
                429,
                500,
                502,
                503,
                504
            )
        ):

            print(
                "PRIMARY MODEL FAILED."
            )

            print(
                "Trying fallback:",
                FALLBACK_MODEL
            )

            fallback_payload = build_payload(

                FALLBACK_MODEL,

                messages,

                web_search=web_search,

                stream=False,

                mode=mode

            )

            response = request_openrouter(

                api_key,

                FALLBACK_MODEL,

                fallback_payload

            )

            active_model = (
                FALLBACK_MODEL
            )

        else:

            active_model = (
                DEFAULT_MODEL
            )

        # -------------------------------------------------
        # HTTP ERROR
        # -------------------------------------------------

        if response.status_code != 200:

            return openrouter_error_response(

                response,

                active_model

            )

        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:

            result = response.json()

        except ValueError:

            return jsonify({

                "error":
                    "OpenRouter returned invalid JSON.",

                "details":
                    response.text[:3000]

            }), 502

        # -------------------------------------------------
        # API ERROR
        # -------------------------------------------------

        if result.get("error"):

            print(
                "OPENROUTER API ERROR:",
                result["error"]
            )

            return jsonify({

                "error":
                    "OpenRouter returned an error.",

                "details":
                    result["error"],

                "model":
                    active_model

            }), 502

        # -------------------------------------------------
        # REPLY
        # -------------------------------------------------

        reply = normalize_reply(
            result
        )

        if not reply:

            return jsonify({

                "error":
                    "The model returned an empty response.",

                "details":
                    result

            }), 502

        # -------------------------------------------------
        # SAVE ASSISTANT
        # -------------------------------------------------

        save_message(

            conversation_id,

            "assistant",

            reply

        )

        # -------------------------------------------------
        # RESPONSE
        # -------------------------------------------------

        return jsonify({

            "type":
                "done",

            "content":
                reply,

            "conversation_id":
                conversation_id,

            "model":
                active_model,

            "web_search":
                web_search,

            "mode":
                mode

        })

    # =====================================================
    # TIMEOUT
    # =====================================================

    except requests.Timeout:

        print(
            "OPENROUTER TIMEOUT"
        )

        return jsonify({

            "error":
                "The AI request timed out. "
                "Please try again."

        }), 504

    # =====================================================
    # REQUEST ERROR
    # =====================================================

    except requests.RequestException as error:

        print(
            "OPENROUTER REQUEST ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Could not connect to OpenRouter.",

            "details":
                str(error)

        }), 502

    # =====================================================
    # GENERAL ERROR
    # =====================================================

    except Exception as error:

        print(
            "CHAT ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Internal server error.",

            "details":
                str(error)

        }), 500


# =========================================================
# REGENERATE
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>/regenerate",
    methods=["POST"]
)
def regenerate(
    conversation_id
):

    try:

        api_key = get_api_key()

        if not api_key:

            return jsonify({
                "error":
                    "OPENROUTER_API_KEY is not configured."
            }), 500

        conn = get_db()

        rows = conn.execute(
            """
            SELECT
                id,
                role,
                content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (
                conversation_id,
            )
        ).fetchall()

        if not rows:

            conn.close()

            return jsonify({
                "error":
                    "Conversation is empty."
            }), 404

        # Remove last assistant message if present.
        last = rows[-1]

        if last["role"] == "assistant":

            conn.execute(
                """
                DELETE FROM messages
                WHERE id = ?
                """,
                (
                    last["id"],
                )
            )

            conn.commit()

            rows = conn.execute(
                """
                SELECT
                    role,
                    content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (
                    conversation_id,
                )
            ).fetchall()

        conn.close()

        messages = [

            {
                "role":
                    "system",

                "content":
                    """
You are Whale AI.
Answer naturally in the same language as the user.
Regenerate the assistant response with a better,
more accurate and useful answer.
"""
                    + build_memory_context()
            }

        ]

        for row in rows:

            if row["role"] in (
                "user",
                "assistant",
                "system"
            ):

                messages.append({

                    "role":
                        row["role"],

                    "content":
                        row["content"]

                })

        payload = build_payload(

            DEFAULT_MODEL,

            messages,

            web_search=False,

            stream=False

        )

        response = request_openrouter(

            api_key,

            DEFAULT_MODEL,

            payload

        )

        if response.status_code != 200:

            return openrouter_error_response(

                response,

                DEFAULT_MODEL

            )

        result = response.json()

        reply = normalize_reply(
            result
        )

        if not reply:

            return jsonify({

                "error":
                    "The model returned an empty response."

            }), 502

        save_message(

            conversation_id,

            "assistant",

            reply

        )

        return jsonify({

            "type":
                "done",

            "content":
                reply,

            "conversation_id":
                conversation_id,

            "model":
                DEFAULT_MODEL

        })

    except Exception as error:

        print(
            "REGENERATE ERROR:",
            repr(error)
        )

        return jsonify({

            "error":
                "Regeneration failed.",

            "details":
                str(error)

        }), 500


# =========================================================
# DELETE ONE CONVERSATION
# =========================================================

@app.route(
    "/conversations/<int:conversation_id>",
    methods=["DELETE"]
)
def delete_conversation(
    conversation_id
):

    try:

        conn = get_db()

        conn.execute(
            """
            DELETE FROM messages
            WHERE conversation_id = ?
            """,
            (
                conversation_id,
            )
        )

        conn.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
            """,
            (
                conversation_id,
            )
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success":
                True
        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to delete conversation.",

            "details":
                str(error)

        }), 500


# =========================================================
# DELETE ALL CONVERSATIONS
# =========================================================

@app.route(
    "/conversations",
    methods=["DELETE"]
)
def delete_all():

    try:

        conn = get_db()

        conn.execute(
            "DELETE FROM messages"
        )

        conn.execute(
            "DELETE FROM conversations"
        )

        conn.commit()
        conn.close()

        return jsonify({
            "success":
                True
        })

    except Exception as error:

        return jsonify({

            "error":
                "Failed to delete conversations.",

            "details":
                str(error)

        }), 500


# =========================================================
# ROOT API INFO
# =========================================================

@app.route(
    "/api",
    methods=["GET"]
)
def api_info():

    return jsonify({

        "name":
            "Whale AI",

        "version":
            "2.0",

        "model":
            DEFAULT_MODEL,

        "features": {

            "chat":
                True,

            "memory":
                True,

            "conversations":
                True,

            "conversation_search":
                True,

            "file_upload":
                True,

            "pdf":
                PdfReader is not None,

            "docx":
                Document is not None,

            "web_search":
                True,

            "web_fetch":
                True,

            "regenerate":
                True

        }

    })


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    api_key_exists = bool(
        get_api_key()
    )

    print()
    print("========================================")
    print("             WHALE AI 2.0")
    print("========================================")
    print(
        "PORT:",
        port
    )
    print(
        "MODEL:",
        DEFAULT_MODEL
    )
    print(
        "FALLBACK:",
        FALLBACK_MODEL
        or "DISABLED"
    )
    print(
        "API KEY:",
        "FOUND"
        if api_key_exists
        else "MISSING"
    )
    print(
        "MEMORY: ENABLED"
    )
    print(
        "CONVERSATIONS: ENABLED"
    )
    print(
        "WEB SEARCH: ENABLED"
    )
    print(
        "WEB FETCH: ENABLED"
    )
    print(
        "FILE UPLOAD: ENABLED"
    )
    print(
        "REGENERATE: ENABLED"
    )
    print("========================================")
    print()

    app.run(

        host="0.0.0.0",

        port=port,

        debug=False,

        threaded=True

    )
