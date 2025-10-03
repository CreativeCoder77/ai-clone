


import json, os, sys, csv
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:
    from backports.zoneinfo import ZoneInfo

# ---------- EDIT THIS (already set to the path you gave) ----------
ROOT_FOLDER = r"C:\Users\HP\Documents\instagram-dhairya._.779-2025-09-30-Q1hi4wYq\your_instagram_activity\messages\inbox\dhairya_17848610517479048"
# -----------------------------------------------------------------

def find_message_jsons(folder):
    """Return list of message json files (message_*.json or any .json containing 'messages' key)."""
    candidates = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(".json"):
            # quick check: does it contain "messages" ?
            try:
                with open(path, "r", encoding="utf-8") as f:
                    head = f.read(2048)  # sample head
                    if "messages" in head.lower():
                        candidates.append(path)
                    else:
                        # still add generic jsons (some exports have different structure)
                        candidates.append(path)
            except Exception:
                # still append — we'll try to load later
                candidates.append(path)
    # prefer files named message_*.json (common IG export)
    message_named = [p for p in candidates if os.path.basename(p).lower().startswith("message")]
    return message_named or candidates

def safe_load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_text_from_msg(msg):
    if "content" in msg and isinstance(msg["content"], str):
        return msg["content"].strip()
    if "content" in msg and isinstance(msg["content"], list):
        parts = []
        for p in msg["content"]:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("text"):
                parts.append(p["text"])
        return " ".join(parts).strip()
    # some files use 'text' or 'message'
    for k in ("text", "message", "body"):
        if k in msg and isinstance(msg[k], str):
            return msg[k].strip()
    # attachments-only fallback
    if any(k in msg for k in ("photos","videos","attachments","share","files")):
        return ""
    return ""

def normalize_sender(msg):
    for k in ("sender_name","sender","from","author"):
        if k in msg and isinstance(msg[k], str):
            return msg[k]
    return "unknown"

def get_timestamp_ms(msg):
    if "timestamp_ms" in msg:
        try:
            return int(msg["timestamp_ms"])
        except:
            pass
    if "timestamp" in msg:
        try:
            t = int(msg["timestamp"])
            return t if t > 1e12 else t*1000
        except:
            pass
    return None

def ms_to_ist_iso(ms):
    if not ms:
        return ""
    sec = ms / 1000.0
    dt = datetime.fromtimestamp(sec, tz=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    return dt.isoformat()

def looks_like_system(text):
    if not text:
        return False
    lower = text.lower()
    patterns = ["unsent", "removed a message", "you unsent", "removed", "missed call", "video call", "was removed"]
    return any(p in lower for p in patterns)

def resolve_media_attachments(msg, root_folder):
    out = []
    # typical keys
    for key in ("photos","videos","files","attachments"):
        if key in msg and isinstance(msg[key], list):
            for it in msg[key]:
                if isinstance(it, dict):
                    uri = it.get("uri") or it.get("filename") or it.get("uri_ms") or it.get("uri_path")
                    if not uri:
                        # some objects use 'uri' nested differently
                        for kk in it.keys():
                            if isinstance(it[kk], str) and (kk.lower().endswith(".jpg") or kk.lower().endswith(".mp4")):
                                uri = it[kk]
                                break
                    if uri:
                        basename = os.path.basename(uri)
                        # try common locations: .../photos/..., .../videos/..., root_folder
                        tried = []
                        candidate = os.path.join(root_folder, "photos", basename)
                        tried.append(candidate)
                        if os.path.exists(candidate):
                            out.append(candidate); continue
                        candidate = os.path.join(root_folder, "videos", basename)
                        tried.append(candidate)
                        if os.path.exists(candidate):
                            out.append(candidate); continue
                        candidate = os.path.join(root_folder, basename)
                        tried.append(candidate)
                        if os.path.exists(candidate):
                            out.append(candidate); continue
                        # fallback: include original uri so user can locate manually
                        out.append(uri)
    # share objects
    if "share" in msg and isinstance(msg["share"], dict):
        s = msg["share"]
        if s.get("link"):
            out.append("[share_url] " + s["link"])
        if s.get("share_text"):
            out.append("[share_text] " + s["share_text"][:300])
    return out

def get_reactions(msg):
    if "reactions" in msg and isinstance(msg["reactions"], list):
        parts = []
        for r in msg["reactions"]:
            actor = r.get("actor")
            reaction = r.get("reaction")
            if actor and reaction:
                parts.append(f"{actor}:{reaction}")
        return ";".join(parts)
    return ""

def extract_messages_from_json(path, root_folder):
    data = safe_load_json(path)
    # find messages array
    if isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
    elif isinstance(data, list):
        messages = data
    else:
        # walk for nested 'messages'
        def walk(o):
            if isinstance(o, dict):
                for k,v in o.items():
                    if k=="messages" and isinstance(v, list):
                        return v
                    r = walk(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for i in o:
                    r = walk(i)
                    if r:
                        return r
            return None
        messages = walk(data)
    if messages is None:
        return []
    cleaned = []
    for m in messages:
        text = get_text_from_msg(m)
        # skip empty system messages unless they include media/share
        if looks_like_system(text) and not any(k in m for k in ("photos","videos","share")):
            continue
        sender = normalize_sender(m)
        ms = get_timestamp_ms(m)
        iso = ms_to_ist_iso(ms)
        attachments = resolve_media_attachments(m, root_folder)
        reactions = get_reactions(m)
        cleaned.append({
            "timestamp_ms": ms,
            "timestamp_iso": iso,
            "sender": sender,
            "text": text,
            "attachments": attachments,
            "reactions": reactions,
            "raw": m
        })
    # sort by timestamp
    cleaned = sorted(cleaned, key=lambda x: x["timestamp_ms"] if x["timestamp_ms"] is not None else 0)
    return cleaned

def write_outputs(cleaned, out_dir, base_name="dhairya_chat"):
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, base_name + "_clean.json")
    csv_path = os.path.join(out_dir, base_name + "_clean.csv")
    jsonl_path = os.path.join(out_dir, base_name + "_fewshots.jsonl")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso","sender","text","attachments","reactions"])
        for c in cleaned:
            w.writerow([c["timestamp_iso"], c["sender"], c["text"], ";".join(c["attachments"]), c["reactions"]])
    # build few-shot pairs (heuristic)
    senders_count = {}
    for c in cleaned:
        senders_count[c["sender"]] = senders_count.get(c["sender"], 0) + 1
    you = max(senders_count.items(), key=lambda kv: kv[1])[0] if senders_count else None
    pairs = []
    for i in range(len(cleaned)-1):
        a = cleaned[i]
        b = cleaned[i+1]
        if a["sender"] != you and b["sender"] == you:
            if a["timestamp_ms"] and b["timestamp_ms"]:
                diff = (b["timestamp_ms"] - a["timestamp_ms"]) / 1000.0
            else:
                diff = None
            if diff is None or diff <= 48*3600:
                prompt = f"{a['sender']}: {a['text']}"
                response = b["text"]
                if prompt.strip() and response.strip():
                    pairs.append({"prompt": prompt, "response": response})
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return json_path, csv_path, jsonl_path, you, len(pairs)

def main():
    root = ROOT_FOLDER
    if not os.path.exists(root):
        print("ROOT_FOLDER not found:", root)
        sys.exit(1)
    print("Scanning folder:", root)
    jsons = find_message_jsons(root)
    if not jsons:
        print("No JSON files found in folder.")
        sys.exit(1)
    # prefer message_1.json if present
    preferred = None
    for p in jsons:
        if os.path.basename(p).lower().startswith("message"):
            preferred = p; break
    if not preferred:
        preferred = jsons[0]
    print("Using JSON:", preferred)
    cleaned = extract_messages_from_json(preferred, root)
    out_json, out_csv, out_jsonl, you, pair_count = write_outputs(cleaned, root)
    print("WROTE:\n ", out_json, "\n ", out_csv, "\n ", out_jsonl)
    print("Detected your sender name (most messages):", you)
    print("Total cleaned messages:", len(cleaned), " - fewshot pairs:", pair_count)

if __name__ == "__main__":
    main()
