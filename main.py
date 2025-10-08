
import os
import json

import pandas as pd
import tkinter as tk
from tkinter import filedialog
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
import time
import sys
import threading
import re


# ============ Color Configuration ============
try:
    from colorama import init, Fore, Back, Style
    init(autoreset=True)
    COLORS_ENABLED = True
except ImportError:
    print("Installing colorama for colored output...")
    os.system("pip install colorama")
    from colorama import init, Fore, Back, Style
    init(autoreset=True)
    COLORS_ENABLED = True

owner_instructions = """
owner is the person whose talking style you are learning and mimicking.
user is the person chatting with the owner.

If the user says "Remember that", "Note that", "Always remember that", or "Keep in mind that",
treat it as an instruction to permanently save to memory. And even if these phrases are not used, and you think the user is telling you something important to remember,
you can also decide to save it to memory yourself if you think it's important.

ALSO, if you are talking and you think this is important to remember, you can decide to save it to memory yourself if you think it's important.

If the message starts with "I" or "me", then this refers to the owner.  
If the message contains "he" or "she", then it means the owner is talking about the other person who is chatting with the owner.

When saving to memory, always restate the fact clearly and prepend it with:
/save_to_memory <fact>
so that it gets stored in the user's permanent memory.  
You may also attach a timer if temporary: "/save_to_memory <fact> /for 1h" → expires in 1 hour.  
Use formats like 10m, 1h, 2d, 1w for minutes, hours, days, weeks.

If the fact is about schedule, availability, preferences, habits, or slang,
make sure to normalize it (clean and precise) before saving.

You can also remove memories by:  
- outputting "/delete_from_memory <keyword>"  
- OR by outputting the index number of the memory (e.g. "2") on its own line to delete memory #2.

If you USE any information from the LEARNING/MEMORY section to answer the user's question,
start your response with:
/used_memory
on its own line. This helps track when stored memories are being utilized.


If the user starts with "Explain" or "Describe", provide a detailed explanation.
Always try to keep your replies in the user's style, tone, and vocabulary.

When these keywords are used, it means the owner is speaking and you must use formal language in English:  
keywords: /owner, owner, Remember that, Note that, Always remember that, Keep in mind that, Explain, Describe

ALWAYS TALK FORMALLY TO THE OWNER AND IN ENGLISH, EVEN IF THE USER'S STYLE IS CASUAL LIKE AN AI TALKING TO THE USER.  
BUT IF THE OWNER ASKS FOR A REPLY TO A FRIEND THEN USE THE TONE YOU HAVE LEARNED FROM THE OWNER.

IF THE USER SAYS TO EDIT ANY MEMORY FIRST DELETE THE MEMORY AND THEN SAVE THE NEW ONE.

IF THE MESSAGE STARTS WITH /USER THEN IT MEANS THE OWNER IS ASKING FOR A REPLY TO SOMEONE ELSE, SO IN THAT CASE, USE THE TONE YOU HAVE LEARNED FROM THE OWNER.

- Every saved memory MUST be referenced by a per-user serial ID called SNo.
- When the assistant issues a save directive, it SHOULD output a confirmation line that includes the assigned SNo. Example: "/save_to_memory User likes coffee" followed by "Saved: SNo 12".
- When instructing deletion, the assistant SHOULD prefer to reference SNo. Example: "/delete_from_memory 12" (deletes SNo 12) or explicitly include the SNo in natural language.
- Do NOT assume you can re-use SNo — they are monotonic and unique per user.
- If the assistant suggests changes or edits to a memory, it should reference the memory by SNo and present the exact replacement text, e.g. "/delete_from_memory 12" then "/save_to_memory New text for SNo 12".

CRITICAL MEMORY PRIORITY:
- ALWAYS prioritize information from the LEARNING/MEMORY section over chat history
- If there's a conflict between what's in LEARNING and what's in RECENT_CONVERSATION, trust LEARNING
- Only valid SNos listed in CURRENT_VALID_SNOs are real - ignore any other SNo references in chat history
- If a memory was mentioned in chat history but is NOT in CURRENT_VALID_SNOs, it has been DELETED and should be completely ignored

When saving to memory, always restate the fact clearly and prepend it with:
/save_to_memory <fact>


"""



# Color scheme for the command line interface
class Colors:
    PRIMARY = Fore.CYAN
    SUCCESS = Fore.GREEN
    WARNING = Fore.YELLOW
    ERROR = Fore.RED
    INFO = Fore.BLUE
    ACCENT = Fore.MAGENTA
    AI = Fore.LIGHTCYAN_EX
    USER = Fore.LIGHTGREEN_EX
    SYSTEM = Fore.LIGHTYELLOW_EX
    BOLD = Style.BRIGHT
    DIM = Style.DIM
    RESET = Style.RESET_ALL

# ============ Configuration ============
USERS_DB = "users_db.json"
CHAT_DB_DIR = "chat_histories"
CSV_PREVIEW_MSGS = 300

os.makedirs(CHAT_DB_DIR, exist_ok=True)

# ============ Loading Animation ============
class LoadingSpinner:
    def __init__(self, message="Loading", color=Colors.PRIMARY):
        self.message = message
        self.color = color
        self.running = False
        self.thread = None
        
    def spin(self):
        # Loaders animation will appear in the following sequence
        spinners = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        idx = 0
        while self.running:
            sys.stdout.write(f'\r{self.color}{spinners[idx]} {self.message}...{Colors.RESET}')
            sys.stdout.flush()
            idx = (idx + 1) % len(spinners)
            time.sleep(0.1)
        sys.stdout.write('\r' + ' ' * (len(self.message) + 10) + '\r')
        sys.stdout.flush()
    
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.spin)
        self.thread.daemon = True
        self.thread.start()
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()



def parse_time_string(time_str: str):
   # Parse human time strings like '1h', '30m', '2d' into timedelta.
    match = re.match(r"(\d+)([smhd])", time_str.strip().lower())
    if not match:
        return None
    value, unit = match.groups()
    value = int(value)
    if unit == "s":
        return timedelta(seconds=value)
    elif unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    return None


def clean_expired_memories(username):
    # Remove expired memories for the user.
    users = load_users()
    rec = users.get(username, {})
    learning_facts = rec.get("learning", [])

    now = datetime.now()
    new_facts = []
    expired = []

    for lf in learning_facts:
        text = lf["text"] if isinstance(lf, dict) else str(lf)
        expiry = lf.get("expiry") if isinstance(lf, dict) else None

        if expiry:
            expiry_dt = datetime.fromisoformat(expiry)
            if now >= expiry_dt:
                expired.append(text)
                continue
        new_facts.append(lf)

    if expired:
        users[username]["learning"] = new_facts
        save_users(users)
        print_warning(f"Expired memories removed: {', '.join(expired)}\n")


# ============ UI Helper Functions ============
def print_banner():
    banner = f"""
{Colors.PRIMARY}{Colors.BOLD}
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║     🤖  PERSONAL GEMINI CHAT AI  🤖                      ║
║                                                           ║
║     Your AI-Powered Personal Chat Assistant              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
{Colors.RESET}
"""
    print(banner)

def print_section_header(title):
    print(f"\n{Colors.ACCENT}{Colors.BOLD}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{Colors.RESET}\n")

def print_success(message):
    print(f"{Colors.SUCCESS}✓ {message}{Colors.RESET}")

def print_error(message):
    print(f"{Colors.ERROR}✗ {message}{Colors.RESET}")

def print_info(message):
    print(f"{Colors.INFO}ℹ {message}{Colors.RESET}")

def print_warning(message):
    print(f"{Colors.WARNING}⚠ {message}{Colors.RESET}")

def print_box(text, color=Colors.PRIMARY):
    lines = text.split('\n')
    max_len = max(len(line) for line in lines) if lines else 0
    print(f"{color}┌─{'─' * max_len}─┐")
    for line in lines:
        print(f"│ {line.ljust(max_len)} │")
    print(f"└─{'─' * max_len}─┘{Colors.RESET}")

# ============ Storage Helpers ============
def load_users():
    if os.path.exists(USERS_DB):
        with open(USERS_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_DB, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def get_user_chat_path(username):
    safe = "".join(ch for ch in username if ch.isalnum() or ch in ("_", "-")).strip() or username
    return os.path.join(CHAT_DB_DIR, f"{safe}_chat.json")

def load_chat_history(username):
    path = get_user_chat_path(username)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_chat_history(username, history):
    path = get_user_chat_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# ============ Gemini Client ============
def get_genai_client():
    api_key = "YOUR_GEMINI_API_KEY"
    if not api_key:
        print_error("Set GEMINI_API_KEY environment variable.")
        raise SystemExit(1)
    return genai.Client(api_key=api_key)

def _get_next_sno_for_user(users_rec):
    """Return the next sno (monotonic) for a user's learning list."""
    facts = users_rec.get("learning", [])
    if not facts:
        return 1
    max_sno = 0
    for f in facts:
        if isinstance(f, dict) and f.get("sno"):
            try:
                s = int(f.get("sno"))
                if s > max_sno:
                    max_sno = s
            except Exception:
                pass
    return max_sno + 1


def generate_response_stream(client, model, system_instruction_text, user_prompt_text):
    """
    Fixed version: properly handles None values in streaming chunks
    """
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=user_prompt_text)]
        )
    ]
    config = types.GenerateContentConfig(
        temperature=1.45,
        system_instruction=[types.Part.from_text(text=system_instruction_text)]
    )
    response_text = ""
    try:
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config
        ):
            if hasattr(chunk, 'text') and chunk.text is not None:
                response_text += chunk.text
            elif hasattr(chunk, 'text') and chunk.text == "":
                continue
                
    except Exception as e:
        print_error(f"Error generating response: {e}")
        response_text = "Sorry, I couldn't get a response right now."
    
    return response_text

# ============ CSV Processing ============
def process_csv_upload(csv_path):
    spinner = LoadingSpinner("Processing CSV file", Colors.INFO)
    spinner.start()
    
    try:
        df = pd.read_csv(csv_path)
        chats = []
        timestamp_col = None
        for c in ("timestamp_iso", "timestamp", "time", "date"):
            if c in df.columns:
                timestamp_col = c
                break
        for _, row in df.iterrows():
            ts = str(row.get(timestamp_col, "")) if timestamp_col else ""
            sender = row.get("sender", "") if "sender" in row.index else ""
            text = row.get("text", "") if "text" in row.index else ""
            attachments = row.get("attachments", "") if "attachments" in row.index else ""
            chats.append({
                "timestamp": ts,
                "sender": str(sender),
                "text": str(text),
                "attachments": str(attachments),
            })
        spinner.stop()
        print_success(f"Loaded {len(chats)} messages from CSV")
        return chats
    except Exception as e:
        spinner.stop()
        print_error(f"Failed to read CSV: {e}")
        return []

# ============ File Selector ============
def select_csv_file():
    print_info("Opening file dialog...")
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Select Instagram CSV",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    try:
        root.destroy()
    except Exception:
        pass
    return file_path

# ============ Pretty Print Analysis ============
def pretty_print_analysis(analysis_text):
    if not analysis_text:
        print_warning("No analysis available.")
        return
    
    print(f"\n{Colors.AI}{Colors.BOLD}╔{'═'*58}╗")
    print(f"║{' '*18}AI ANALYSIS REPORT{' '*20}║")
    print(f"╚{'═'*58}╝{Colors.RESET}\n")
    
    lines = analysis_text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("###") or "example phrases" in stripped.lower():
            print(f"\n{Colors.ACCENT}{Colors.BOLD}{stripped.upper()}{Colors.RESET}")
        elif stripped.startswith("*") or stripped.startswith("-"):
            print(f"{Colors.PRIMARY}  {stripped}{Colors.RESET}")
        elif ":" in stripped:
            key, sep, value = stripped.partition(":")
            print(f"{Colors.SUCCESS}{Colors.BOLD}{key.strip()}:{Colors.RESET} {Colors.INFO}{value.strip()}{Colors.RESET}")
        else:
            print(f"{Colors.INFO}{stripped}{Colors.RESET}")
    print()

# ============ Memory Helpers ============
def add_learning(username, fact, expiry=None):
    """
    Save a learning fact to user's memory with a serial number `sno`.
    Returns the assigned sno (int) on success, or False on duplicate/error.
    expiry: string (ISO format) or None
    """
    users = load_users()
    if username not in users:
        users[username] = {}

    if "learning" not in users[username]:
        users[username]["learning"] = []

    incoming_text = fact.strip()
    existing_texts = [
        (lf["text"] if isinstance(lf, dict) else str(lf)).strip().lower()
        for lf in users[username]["learning"]
    ]

    if incoming_text.lower() in existing_texts:
        print_warning(f"Memory already exists: {incoming_text}")
        return False

    # assign sno
    next_sno = _get_next_sno_for_user(users[username])
    fact_entry = {"sno": next_sno, "text": incoming_text}
    if expiry is not None:
        fact_entry["expiry"] = expiry  # ISO string

    users[username]["learning"].append(fact_entry)
    save_users(users)
    return next_sno


def forget_learning(username, fact_substring_or_sno: str):
    """
    Delete learning by sno (exact integer) or by substring match.
    fact_substring_or_sno may be '3' (sno) or 'gym' (substring).
    """
    users = load_users()
    if username not in users:
        return False

    facts = users[username].get("learning", [])
    if not facts:
        return False

    target = fact_substring_or_sno.strip().lower()
    new_facts = []
    deleted = []

    # if target is integer -> delete by sno
    if target.isdigit():
        sno_target = int(target)
        for f in facts:
            f_text = f["text"] if isinstance(f, dict) else str(f)
            f_sno = f.get("sno") if isinstance(f, dict) else None
            if f_sno == sno_target:
                deleted.append(f_text)
            else:
                new_facts.append(f)
    else:
        # substring match
        for f in facts:
            f_text = f["text"] if isinstance(f, dict) else str(f)
            if target in f_text.lower():
                deleted.append(f_text)
            else:
                new_facts.append(f)

    users[username]["learning"] = new_facts
    save_users(users)
    return deleted  # list of deleted texts (empty if nothing)

def migrate_existing_memories_add_sno(username):
    """
    Optional: convert older memory entries that were plain strings into dicts
    and ensure each memory has an `sno`. Safe to call multiple times.
    """
    users = load_users()
    if username not in users:
        return False
    rec = users[username]
    facts = rec.get("learning", [])
    migrated = []
    next_sno = _get_next_sno_for_user(rec)
    for f in facts:
        if isinstance(f, dict):
            # ensure sno exists
            if not f.get("sno"):
                f["sno"] = next_sno
                next_sno += 1
            migrated.append(f)
        else:
            # string -> dict
            migrated.append({"sno": next_sno, "text": str(f)})
            next_sno += 1
    users[username]["learning"] = migrated
    save_users(users)
    return True


# ============ Login ============
def login():
    """
    Simple login by username only (no password).
    Returns the username on success, or None if the user does not exist.
    """


    for attempt in range(3):
        print_section_header(f"USER LOGIN ({3-attempt} left)")
        users = load_users()
        username = input(f"{Colors.PRIMARY}Enter username: {Colors.RESET}").strip()

        if not username:
            print_error("Username cannot be empty.")
            continue

        if username not in users:
            print_error("User not found. Please try again.")
            continue

        print_success(f"Welcome back, {username}!")
        return username

    users = load_users()

    if not username:
        return None

    if username not in users:
        return None

    print_success(f"Welcome back, {username}!")
    return username


# ============ Signup ============

def signup():
    """
    User provides username, selects an Instagram CSV (required),
    and the system generates using ai an analysis from the CSV.
    Returns the created username on success, or None on failure.
    """
    print_section_header("NEW USER SIGNUP (No password required)")

    users = load_users()
    username = input(f"{Colors.PRIMARY}Enter username: {Colors.RESET}").strip()

    if not username:
        print_error("Username cannot be empty.")
        return None
    if username in users:
        print_error("Username already exists. Please login instead.")
        return None

    # Ask for CSV file (required)
    print_info("Select your Instagram CSV file (required)...")
    csv_path = select_csv_file()

    if not csv_path or not os.path.exists(csv_path):
        print_error("No file selected or file not found. Signup aborted.")
        return None

    print_success(f"Selected: {os.path.basename(csv_path)}")

    starting_command = input(f"{Colors.PRIMARY}Enter starting command (or press Enter for default): {Colors.RESET}").strip()
    if not starting_command:
        starting_command = "Analyze my chat style, tone, language, and reply patterns."

    chat_data = process_csv_upload(csv_path)
    save_chat_history(username, chat_data)

    spinner = LoadingSpinner("Creating account", Colors.SUCCESS)
    spinner.start()
    time.sleep(1)

    # Create user record (no password fields)
    users[username] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "starting_command": starting_command,
        "analysis": None,
        "learning": []
    }
    save_users(users)
    spinner.stop()

    print_success(f"Signup complete! {len(chat_data)} messages loaded.")

    # Run AI analysis on the uploaded CSV preview
    print_section_header("AI ANALYSIS IN PROGRESS")
    client = get_genai_client()

    preview_msgs = chat_data[-CSV_PREVIEW_MSGS:] if chat_data else []
    context_preview = "\n".join(f"{m['sender']}: {m['text']}" for m in preview_msgs if m.get("text"))

    if not context_preview:
        context_preview = "(no previewable messages found in CSV)"

    analysis_prompt = f"""
You are an assistant whose job is to analyze the style, tone, language, and reply patterns of the target user,
based on the chat preview that follows. The user asked: "{starting_command}"

Please output a clear structured summary that includes:
- A short natural-language summary of overall tone & attitude.
- Typical vocabulary choices (swearing, slang, emojis, punctuation, formality).
- Reply rhythm / timing patterns you can infer.
- Use of attachments / media.
- Typical reply length and structure.
- A small list of example phrases or reply patterns the user uses.
- Identification of context:
    * Determine when the user is speaking to the AI directly.
    * Determine when the user is asking for a reply to someone else.
- Lessons or knowledge the user is teaching that could be used for future replies.
- A short "Instructions for assistant" section describing how you (the AI) should answer to mimic this user.
- What kind of language the user uses 
- Tone of the language 
- Frequency of the messages
- Frequency of each slangs, emojis, swearing, punctuation, formality etc 
- Even spelling patterns

Here is the chat preview (most recent messages, up to {CSV_PREVIEW_MSGS} lines):
{context_preview}

Be concise but thorough.
"""

    spinner = LoadingSpinner("AI analyzing your chat style", Colors.AI)
    spinner.start()

    try:
        analysis_text = generate_response_stream(
            client,
            model="gemini-2.5-flash",
            system_instruction_text="",
            user_prompt_text=analysis_prompt
        )
        spinner.stop()
    except Exception as e:
        spinner.stop()
        print_error(f"Failed to get analysis from Gemini: {e}")
        analysis_text = "ERROR: analysis failed."

    # Save analysis to user record and append to chat history
    users = load_users()
    if username in users:
        users[username]["analysis"] = analysis_text
        users[username]["analysis_generated_at"] = datetime.now(timezone.utc).isoformat()
        save_users(users)

    history = load_chat_history(username)
    history.append({"sender": "AI", "text": analysis_text, "meta": {"analysis_result": True}})
    save_chat_history(username, history)

    pretty_print_analysis(analysis_text)

    return username


# ============ Delete Account ============
def delete_account():
    """
    Delete account by username. No password required.
    Prompts for a typed confirmation ('DELETE') for safety.
    Returns True if deleted, False otherwise.
    """
    print_section_header("DELETE ACCOUNT")

    users = load_users()
    username = input(f"{Colors.WARNING}Enter username to delete: {Colors.RESET}").strip()

    if not username:
        print_error("Username cannot be empty.")
        return False

    if username not in users:
        print_error("User not found.")
        return False

    rec = users[username]

    print_warning(f"\n⚠️  WARNING: This will permanently delete:")
    print(f"  - User account: {username}")
    print(f"  - All stored memories ({len(rec.get('learning', []))} memories)")
    print(f"  - Chat history")
    print(f"  - Analysis data")

    confirm = input(f"\n{Colors.ERROR}Type 'DELETE' (in capitals) to confirm: {Colors.RESET}").strip()

    if confirm != "DELETE":
        print_info("Account deletion cancelled.")
        return False

    spinner = LoadingSpinner("Deleting account", Colors.ERROR)
    spinner.start()

    # Delete chat history file
    chat_path = get_user_chat_path(username)
    if os.path.exists(chat_path):
        try:
            os.remove(chat_path)
        except Exception as e:
            spinner.stop()
            print_error(f"Failed to delete chat history: {e}")
            return False

    # Remove user record
    del users[username]
    save_users(users)

    time.sleep(1)
    spinner.stop()

    print_success(f"Account '{username}' has been permanently deleted.")
    return True



# ============ Input Helper ============
def multiline_input(prompt=""):
    if prompt:
        print(prompt)
    else:
        print(f"{Colors.USER}{Colors.BOLD}You:{Colors.RESET} (type your message, press Enter twice to send)")
    
    lines = []
    empty_count = 0
    
    while True:
        line = input()
        if line.strip() == "":
            empty_count += 1
            if empty_count >= 1:
                break
        else:
            empty_count = 0
            lines.append(line)
    
    return "\n".join(lines).strip()

def print_commands():
    """Prints the comprehensive list of chat assistant commands."""

    commands = [
        {
            "category": "Session and Data Management",
            "items": [
                {"command": f"{Colors.SUCCESS}exit{Colors.RESET} or {Colors.SUCCESS}quit{Colors.RESET}", "purpose": "Ends the current chat session."},
                {"command": f"{Colors.SUCCESS}/upload_new_chat_data{Colors.RESET}", "purpose": "Uploads a new Instagram CSV file to update the AI's style profile."},
                {"command": f"{Colors.SUCCESS}/owner <command>{Colors.RESET}", "purpose": "Explicitly instructs the AI that the command (e.g., /save_to_memory) is being issued by the owner, ensuring the AI maintains the owner's learned style and identity."},
            ]
        },
        {
            "category": "Permanent Memory Saving (Any of these trigger a save)",
            "items": [
                {"command": f"{Colors.SUCCESS}Remember that...{Colors.RESET}", "purpose": "Instructs the AI to save the following text as a permanent memory."},
                {"command": f"{Colors.SUCCESS}Note that...{Colors.RESET}", "purpose": "Instructs the AI to save the following text as a permanent memory."},
                {"command": f"{Colors.SUCCESS}Always remember that...{Colors.RESET}", "purpose": "Instructs the AI to save the following text as a permanent memory."},
                {"command": f"{Colors.SUCCESS}Keep in mind that...{Colors.RESET}", "purpose": "Instructs the AI to save the following text as a permanent memory."},
                {"command": f"{Colors.SUCCESS}/owner remember...{Colors.RESET}", "purpose": "Explicit command prefix for permanent memory saving."},
                {"command": f"{Colors.SUCCESS}/save_to_memory...{Colors.RESET}", "purpose": "Explicit command prefix for permanent memory saving."},
            ]
        },
        {
            "category": "Timed Memory Saving (Expiring Facts)",
            "items": [
                {"command": f"{Colors.SUCCESS}/save_to_memory fact /for <time_str>{Colors.RESET}", "purpose": f"Saves a fact that expires after a set time. Time strings include {Colors.INFO}10m{Colors.RESET}, {Colors.INFO}1h{Colors.RESET}, {Colors.INFO}2d{Colors.RESET}, or {Colors.INFO}1w{Colors.RESET} (minutes, hours, days, weeks)."},
                {"command": f"Example: {Colors.DIM}/save_to_memory appointment /for 3d{Colors.RESET}", "purpose": f"Saves 'appointment' which expires in 3 days."},
            ]
        },
        {
            "category": "Memory Deletion",
            "items": [
                {"command": f"{Colors.ERROR}/delete_memory{Colors.RESET}", "purpose": "Lists all stored memories with their SNo (Serial Number) and text."},
                {"command": f"{Colors.ERROR}/delete_memory <SNo>{Colors.RESET}", "purpose": "Deletes the memory with the specified Serial Number (e.g., /delete_memory 5)."},
                {"command": f"{Colors.ERROR}/delete_memory <keyword>{Colors.RESET}", "purpose": "Deletes any memory whose text contains the specified keyword (e.g., /delete_memory gym)."},
            ]
        },
    ]

    # Print Header
    print(f"\n{Colors.BOLD}{Colors.PRIMARY}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.PRIMARY}{' ' * 20}PERSONAL AI CHAT ASSISTANT COMMANDS{' ' * 20}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.PRIMARY}{'='*80}{Colors.RESET}\n")

    # Calculate padding length by finding the longest command (excluding color codes)
    max_cmd_len = 0
    for category in commands:
        for item in category["items"]:
            # Strip all escape codes for accurate length calculation
            clean_cmd = item['command'].replace(Colors.SUCCESS, '').replace(Colors.ERROR, '').replace(Colors.DIM, '').replace(Colors.RESET, '').replace(Colors.INFO, '')
            max_cmd_len = max(max_cmd_len, len(clean_cmd))

    # Print the command list
    for category in commands:
        print(f"\n{Colors.BOLD}{Colors.INFO}--- {category['category']} ---{Colors.RESET}")
        
        # Print Table Header
        header_padding = max_cmd_len + 5
        print(f"{Colors.BOLD}{'COMMAND'.ljust(header_padding)} | {'PURPOSE'}{Colors.RESET}")
        print(f"{Colors.DIM}{'-' * header_padding} + {'-' * 60}{Colors.RESET}")

        # Print Items
        for item in category["items"]:
            # Calculate required padding for the current command
            clean_cmd = item['command'].replace(Colors.SUCCESS, '').replace(Colors.ERROR, '').replace(Colors.DIM, '').replace(Colors.RESET, '').replace(Colors.INFO, '')
            padding = header_padding - len(clean_cmd)
            
            # Print the command and purpose
            print(f"{item['command']}{' ' * padding}| {item['purpose']}")
        
    print(f"\n{Colors.DIM}{'─'*80}{Colors.RESET}\n")


# ============ Chat Loop ============
def chat_loop(username):
    print_section_header(f"CHAT SESSION - {username}")
    
    client = get_genai_client()
    users = load_users()
    rec = users.get(username, {})

    system_instruction = rec.get("analysis") or rec.get("starting_command") or "Mimic the user's style as best as possible."
    history = load_chat_history(username)

    custom_instructions = owner_instructions

    print_commands()

    # Helper functions for processing AI directive lines
    def _process_save_command(cmd_text):
        payload = cmd_text.strip()
        if payload.lower().startswith("/save_to_memory"):
            payload = payload[len("/save_to_memory"):].strip()
        else:
            return False, None, None

        if "/for" in payload:
            fact_text, _, time_str = payload.partition("/for")
            fact_text = fact_text.strip()
            time_str = time_str.strip()
            return True, fact_text, time_str
        else:
            return True, payload.strip(), None

    def _process_delete_command(cmd_text):
        payload = cmd_text.strip()
        if payload.lower().startswith("/delete_from_memory"):
            payload = payload[len("/delete_from_memory"):].strip()
            return payload
        return None

    while True:
        # Clean expired memories before each turn
        clean_expired_memories(username)

        user_input = multiline_input()
        
        if not user_input:
            continue
        
        if user_input.lower() in ("exit", "quit"):
            print_success("Goodbye! Chat session ended.")
            break

        # Handle CSV upload
        if user_input.strip() == "/upload_new_chat_data":
            print_info("Select a new CSV file to upload...")
            csv_path = select_csv_file()
            
            if not csv_path or not os.path.exists(csv_path):
                print_error("No file selected or file not found. Aborting upload.\n")
                continue

            new_chats = process_csv_upload(csv_path)
            if new_chats:
                print_success(f"{len(new_chats)} messages loaded from new CSV.\n")
                history.extend(new_chats)
                save_chat_history(username, history)

                print_info("AI is analyzing the new chat data...")
                preview_msgs = new_chats[-CSV_PREVIEW_MSGS:] if new_chats else []
                context_preview = "\n".join(f"{m['sender']}: {m['text']}" for m in preview_msgs if m.get("text"))
                if not context_preview:
                    context_preview = "(no previewable messages found in CSV)"

                analysis_prompt = f"""
You are an assistant whose job is to analyze the style, tone, language, and reply patterns of the target user,
based on the new chat preview that follows. Do not wait for any user instructions — generate analysis automatically.

Here is the new chat preview (most recent messages, up to {CSV_PREVIEW_MSGS} lines):
{context_preview}

Be concise but thorough. Include:
- Overall tone & attitude
- Typical vocabulary (slang, emojis, swearing, punctuation, formality)
- Reply rhythm/timing patterns
- Use of attachments/media
- Typical reply length and structure
- Example phrases or reply patterns
- Instructions for assistant on how to mimic this user
"""

                spinner = LoadingSpinner("Analyzing new chat data", Colors.AI)
                spinner.start()

                try:
                    new_analysis = generate_response_stream(
                        client,model="gemini-2.5-flash",
                        system_instruction_text="",
                        user_prompt_text=analysis_prompt
                    )
                    spinner.stop()
                    pretty_print_analysis(new_analysis)

                    users = load_users()
                    if username in users:
                        users[username]["analysis"] = new_analysis
                        users[username]["analysis_generated_at"] = datetime.now(timezone.utc).isoformat()
                        save_users(users)

                except Exception as e:
                    spinner.stop()
                    print_error(f"Failed to generate analysis for new chats: {e}")
            else:
                print_warning("No messages were loaded from the CSV.\n")

            continue

        # Handle memory save from user input (manual save commands)
        lowered = user_input.lower()
        if any(lowered.startswith(x) for x in (
            "remember that",
            "note that",
            "always remember that",
            "keep in mind that",
            "/owner remember",
            "/save_to_memory"
        )):
            # Extract the fact text
            fact_line = user_input
            for prefix in ["remember that", "note that", "always remember that", "keep in mind that", "/owner remember", "/save_to_memory"]:
                if lowered.startswith(prefix):
                    fact_line = user_input[len(prefix):].strip()
                    break
            
            expiry = None
            if "/for" in fact_line:
                fact_text, _, time_str = fact_line.partition("/for")
                fact_text = fact_text.strip()
                delta = parse_time_string(time_str.strip())
                if delta:
                    expiry = (datetime.now() + delta).isoformat()
                    sno = add_learning(username, fact_text, expiry=expiry)
                    if sno:
                        print_success(f"Saved timed memory: SNo {sno}: {fact_text} (expires in {time_str.strip()})\n")
                    else:
                        print_warning(f"Memory already exists: {fact_text}\n")
                else:
                    print_warning("Invalid time format. Use like '1h', '30m', '2d'.\n")
            else:
                sno = add_learning(username, fact_line)
                if sno:
                    print_success(f"Saved permanent memory: SNo {sno}: {fact_line}\n")
                else:
                    print_warning(f"Memory already exists: {fact_line}\n")
            continue

        # Handle memory delete from user input
        if lowered.startswith("/delete_memory"):
            parts = user_input.split(maxsplit=1)
            users = load_users()
            learning_facts = users.get(username, {}).get("learning", [])

            if not learning_facts:
                print_warning("No memories stored yet.\n")
                continue

            if len(parts) == 1:
                print_info("Stored memories:")
                for lf in learning_facts:
                    if isinstance(lf, dict):
                        sno = lf.get("sno")
                        text = lf.get("text", "")
                        expiry = lf.get("expiry")
                        expiry_str = f" (expires {expiry})" if expiry else " (permanent)"
                        print(f"  SNo {sno}. {text}{expiry_str}")
                    else:
                        # legacy fallback
                        print(f"  {lf}")
                print("\nUse `/delete_memory <SNo>` or `/delete_memory <keyword>` to remove.\n")
            else:
                target = parts[1].strip()
                deleted = forget_learning(username, target)
                if deleted:
                    if isinstance(deleted, list):
                        print_success(f"Deleted memories: {', '.join(deleted)}\n")
                    else:
                        print_success("Deleted matching memories.\n")
                else:
                    print_warning("No matching memory found.\n")
            continue

        # Build time context
        current_dt = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        time_context = f"Today is {current_dt}. Consider the current day and time while answering.\n\n"

        # Reload users to get fresh memory data
        users = load_users()
        learning_facts = users.get(username, {}).get("learning", [])
        
        # Build memory context with PRIORITY EMPHASIS and SNo tracking
        learning_text_parts = []
        current_snos = []
        for lf in learning_facts:
            if isinstance(lf, dict):
                sno = lf.get("sno")
                current_snos.append(str(sno))
                text = lf.get("text", "")
                expiry = lf.get("expiry")
                expiry_str = f" (expires {expiry})" if expiry else " (permanent)"
                learning_text_parts.append(f"SNo {sno}: {text}{expiry_str}")
            else:
                learning_text_parts.append(f"- {lf}")

        learning_text = "\n".join(learning_text_parts) if learning_text_parts else "(no additional learning yet)"

        # Build conversation context with PRIORITY given to memories
        context_parts = []
        context_parts.append("=" * 80 + "\n")
        context_parts.append("🔴 CRITICAL: MEMORY PRIORITY SYSTEM 🔴\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append("YOU MUST PRIORITIZE INFORMATION FROM LEARNING/MEMORY OVER CHAT HISTORY.\n")
        context_parts.append("If there is ANY conflict between LEARNING and RECENT_CONVERSATION, ALWAYS trust LEARNING.\n")
        context_parts.append("=" * 80 + "\n\n")
        
        context_parts.append("=" * 80 + "\n")
        context_parts.append("📋 CURRENT VALID MEMORY SNos\n")
        context_parts.append("=" * 80 + "\n")
        if current_snos:
            context_parts.append(f"Valid SNos: {', '.join(current_snos)}\n")
            context_parts.append("\n⚠️  IMPORTANT: Only these SNos exist. Any other SNo mentioned in chat history has been DELETED.\n")
            context_parts.append("If you see a reference to a SNo that is NOT in the list above, IGNORE IT COMPLETELY.\n")
        else:
            context_parts.append("No memories stored yet.\n")
        context_parts.append("=" * 80 + "\n\n")

        context_parts.append("=" * 80 + "\n")
        context_parts.append("🧠 LEARNING/MEMORY (HIGHEST PRIORITY - TRUST THIS OVER EVERYTHING)\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append(learning_text + "\n")
        context_parts.append("\n⚠️  THIS IS THE AUTHORITATIVE SOURCE OF TRUTH. Use this information with HIGHEST PRIORITY.\n")
        context_parts.append("=" * 80 + "\n\n")

        context_parts.append("=" * 80 + "\n")
        context_parts.append("👤 USER STYLE ANALYSIS (Reference for tone/style only)\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append(system_instruction + "\n")
        context_parts.append("=" * 80 + "\n\n")

        context_parts.append("=" * 80 + "\n")
        context_parts.append("📜 CUSTOM INSTRUCTIONS\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append(custom_instructions + "\n")
        context_parts.append("=" * 80 + "\n\n")

        context_parts.append("=" * 80 + "\n")
        context_parts.append("🕐 TIME CONTEXT\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append(time_context)
        context_parts.append("=" * 80 + "\n\n")

        context_parts.append("=" * 80 + "\n")
        context_parts.append("💬 RECENT CONVERSATION (Lower priority - for context only)\n")
        context_parts.append("=" * 80 + "\n")
        context_parts.append("Note: This is just conversational context. If it conflicts with LEARNING, ignore it.\n")
        context_parts.append("Filtering out memory operation messages to reduce confusion...\n\n")
        
        # Filter chat history to exclude memory operation messages
        recent_msgs = history[-40:]
        included_count = 0
        for msg in recent_msgs:
            sender = msg.get("sender", "Unknown")
            text = msg.get("text", "")
            
            # Skip AI messages that contain memory operations or SNo references
            if sender == "AI":
                lower_text = text.lower()
                # Skip if it's a memory operation message
                if any(keyword in lower_text for keyword in [
                    "/save_to_memory", 
                    "/delete_from_memory", 
                    "saved: sno",
                    "saved permanent memory",
                    "saved timed memory",
                    "deleted memories:",
                    "auto-saved",
                    "auto-deleted"
                ]):
                    continue
                # Skip if it's just talking about memory numbers without substance
                if re.search(r'\bsno\s+\d+\b', lower_text) and len(text.strip()) < 100:
                    continue
            
            context_parts.append(f"{sender}: {text}\n")
            included_count += 1
            
        if included_count == 0:
            context_parts.append("(No recent conversational messages)\n")
            
        context_parts.append("=" * 80 + "\n\n")
        context_parts.append(f"Current User Message: {user_input}\n\n")
        context_parts.append("Your Response (remember: LEARNING has highest priority):\n")

        combined_system_prompt = "\n".join(context_parts)

        # Generate AI reply
        spinner = LoadingSpinner("AI is thinking", Colors.AI)
        spinner.start()
        reply = generate_response_stream(
            client,
            model="gemini-2.5-flash",
            system_instruction_text=combined_system_prompt,
            user_prompt_text=user_input
        )
        spinner.stop()
        # ---------- Process AI directives (/save_to_memory, /delete_from_memory, /used_memory, numeric deletion) ----------
        new_lines = []
        memory_was_used = False
        
        for line in reply.splitlines():
            low = line.lower().strip()

            # Check for memory usage indicator
            if low == "/used_memory":
                memory_was_used = True
                continue

            # 1) Embedded explicit save command
            if "/save_to_memory" in low:
                idx = low.find("/save_to_memory")
                cmd_text = line[idx:]
                saved, fact_text, time_str = _process_save_command(cmd_text)
                if saved and fact_text:
                    if time_str:
                        delta = parse_time_string(time_str)
                        if delta:
                            expiry = (datetime.now() + delta).isoformat()
                            sno = add_learning(username, fact_text, expiry=expiry)
                            if sno:
                                print_success(f"AI auto-saved timed memory: SNo {sno}: {fact_text} (expires {time_str})")
                            else:
                                print_warning(f"AI attempted to save duplicate timed memory: {fact_text}")
                        else:
                            print_warning(f"AI tried saving with invalid time format: '{time_str}'")
                    else:
                        sno = add_learning(username, fact_text, expiry=None)
                        if sno:
                            print_success(f"AI auto-saved permanent memory: SNo {sno}: {fact_text}")
                        else:
                            print_warning(f"AI attempted to save duplicate memory: {fact_text}")
                continue

            # 2) Embedded explicit delete command
            if "/delete_from_memory" in low:
                idx = low.find("/delete_from_memory")
                cmd_text = line[idx:]
                target = _process_delete_command(cmd_text)
                if target:
                    deleted = forget_learning(username, target)
                    if deleted:
                        print_success(f"AI auto-deleted from memory: {', '.join(deleted)}")
                    else:
                        print_warning("AI attempted to delete from memory but found no match.")
                continue

            # 3) Numeric-only line: treat as "delete memory #n"
            if low.isdigit():
                sno_num = int(low)
                deleted = forget_learning(username, str(sno_num))
                if deleted:
                    print_success(f"AI requested deletion: removed memory SNo {sno_num} -> {', '.join(deleted)}")
                else:
                    print_warning(f"AI requested deletion of memory SNo {sno_num}, but that SNo does not exist.")
                continue

            # otherwise keep the line for final display
            new_lines.append(line)

        reply = "\n".join(new_lines).strip()
        # -------------------------------------------------------------------------------

        # After processing directives, run a final expired-clean check
        clean_expired_memories(username)

        # Print AI reply with memory usage indicator
        if memory_was_used:
            print(f"\n{Colors.WARNING}📌 Memory Used{Colors.RESET}")

        # Print AI reply (without directive lines)
        print(f"\n{Colors.AI}{Colors.BOLD}AI:{Colors.RESET} {reply}\n")
        print(f"{Colors.DIM}{'─'*60}{Colors.RESET}\n")

        # Save chat history
        history.append({"sender": "You", "text": user_input, "timestamp": datetime.now(timezone.utc).isoformat()})
        history.append({"sender": "AI", "text": reply, "timestamp": datetime.now(timezone.utc).isoformat()})
        save_chat_history(username, history)



# ============ Main ============
def main():
    print_banner()
    
    print(f"{Colors.PRIMARY}Welcome to your Personal AI Chat Assistant!{Colors.RESET}\n")
    print(f"{Colors.INFO}Please choose an option:{Colors.RESET}\n")
    print(f"  {Colors.SUCCESS}[1]{Colors.RESET} Signup (New User)")
    print(f"  {Colors.SUCCESS}[2]{Colors.RESET} Login (Existing User)")
    print(f"  {Colors.ERROR}[3]{Colors.RESET} Delete Account")
    print()
    
    choice = input(f"{Colors.PRIMARY}Enter your choice (1, 2, or 3): {Colors.RESET}").strip()
    
    username = None
    if choice in ["1", "signup"]:
        username = signup()
    elif choice  in ["2", "login"]:
        username = login()
    elif choice in ["3", "delete", "delete account"]:
        if delete_account():
            print_info("\nReturning to main menu...\n")
            time.sleep(2)
            main()  # Return to main menu after deletion
        return
    else:
        print_error("Invalid choice. Please restart and select 1, 2, or 3.")
        return
    
    if username:
        chat_loop(username)

if __name__ == "__main__":

    main()
