from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import json, sqlite3, re, urllib.request, urllib.error

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'inboxpilot.db'
HOST, PORT = '0.0.0.0', int(__import__('os').environ.get('PORT', '10000'))
# Public demo: Gmail credentials and mailbox integration are intentionally disabled.


def load_gmail_config():
    """Public demo never loads or accepts Gmail credentials."""
    return None


def fetch_gmail_messages():
    raise RuntimeError('Gmail integration is disabled in the public demo.')


def sync_gmail_to_database():
    raise RuntimeError('Gmail sync is disabled in the public demo. Use fictional sample data.')

SAMPLES = [
    {
        'id': 'sample-001', 'sender': 'Dr. Maya Iyer · Sample Research Partner',
        'email': 'maya@example.test', 'subject': 'Research collaboration meeting',
        'snippet': 'Could you share your preferred time for a short discussion about the study timeline?',
        'body': "Hello,\n\nWe are coordinating the next discussion for the research collaboration and would like to review the study timeline. Could you share your preferred day and time for a short meeting?\n\nRegards,\nMaya",
        'category': 'Needs reply', 'is_demo': True,
        'reply': "Hello Maya,\n\nThank you for reaching out. I would be happy to discuss the study timeline. My availability is [add your preferred day and time, including time zone].\n\nBest regards"
    },
    {
        'id': 'sample-002', 'sender': 'Research Updates · Sample Notification',
        'email': 'updates@example.test', 'subject': 'New publication alert',
        'snippet': 'An automated research publication notification for demonstration purposes.',
        'body': 'This is a fictional automated publication alert used to demonstrate email classification. No real publication or personal data is represented.',
        'category': 'Automated', 'is_demo': True, 'reply': ''
    },
    {
        'id': 'sample-003', 'sender': 'Clinical Research Forum · Sample',
        'email': 'events@example.test', 'subject': 'Conference registration confirmation',
        'snippet': 'A fictional conference registration confirmation used as a transactional example.',
        'body': 'This is a fictional conference registration confirmation. No actual registration or personal information is represented.',
        'category': 'Receipt', 'is_demo': True, 'reply': ''
    }
]

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY, sender TEXT, email TEXT, subject TEXT, snippet TEXT,
            body TEXT, category TEXT, is_demo INTEGER, reply TEXT, status TEXT DEFAULT 'Awaiting review', confidence REAL DEFAULT 0, reason TEXT DEFAULT ''
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, action TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
        # Lightweight schema migration for existing local databases.
        cols = {r['name'] for r in db.execute('PRAGMA table_info(emails)').fetchall()}
        if 'confidence' not in cols:
            db.execute("ALTER TABLE emails ADD COLUMN confidence REAL DEFAULT 0")
        if 'reason' not in cols:
            db.execute("ALTER TABLE emails ADD COLUMN reason TEXT DEFAULT ''")
        count = db.execute('SELECT COUNT(*) FROM emails').fetchone()[0]
        if count == 0:
            for item in SAMPLES:
                db.execute('''INSERT INTO emails
                    (id,sender,email,subject,snippet,body,category,is_demo,reply,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (item['id'], item['sender'], item['email'], item['subject'], item['snippet'],
                     item['body'], item['category'], 1, item['reply'], 'Awaiting review'))

def classify_email(item):
    """Transparent heuristic classifier; intentionally not represented as an LLM."""
    sender = (item.get('sender') or '').lower()
    address = (item.get('email') or '').lower()
    subject = (item.get('subject') or '').lower()
    body = (item.get('body') or '').lower()
    text = f"{sender} {address} {subject} {body}"

    automated = ['no-reply', 'noreply', 'do-not-reply', 'automated', 'unsubscribe', 'notification']
    receipts = ['receipt', 'booking confirmation', 'order confirmation', 'payment received', 'invoice']
    action_phrases = ['would you', 'could you', 'please let me know', 'please confirm', 'are you available',
                      'can you', 'what do you think', 'please reply', 'send me', 'let me know']
    if any(x in text for x in automated):
        return 'Automated', 0.96, 'Sender or message contains an automated/no-reply signal.'
    if any(x in text for x in receipts):
        return 'Receipt', 0.94, 'Subject or message matches a transactional confirmation pattern.'
    has_direct_question = '?' in text and any(x in text for x in ['would ', 'could ', 'can ', 'when ', 'what ', 'which ', 'how '])
    if (any(x in text for x in action_phrases) or has_direct_question) and not any(x in text for x in ['do not reply', 'no reply needed']):
        return 'Needs reply', 0.82, 'The message contains a direct question or request for a response.'
    return 'FYI', 0.66, 'No clear reply request or automated/receipt pattern was detected; review manually.'


def build_reply(item, tone='professional', length='balanced'):
    """Build a context-aware draft from the sample message using local rules only.

    This is not an LLM. It only uses facts present in the email and explicit placeholders
    where the user must supply information.
    """
    category, _, _ = classify_email(item)
    if category != 'Needs reply':
        raise ValueError('A reply suggestion is only available for messages classified as Needs reply.')

    allowed_tones = {'professional', 'friendly', 'concise'}
    allowed_lengths = {'short', 'balanced'}
    if tone not in allowed_tones or length not in allowed_lengths:
        raise ValueError('Choose a supported tone and length.')

    sender = (item.get('sender') or '').split('·')[0].strip()
    first_name = re.split(r'\s+', sender)[0] if sender else 'there'
    subject = (item.get('subject') or '').lower()
    body = item.get('body') or ''
    body_l = body.lower()
    combined = subject + ' ' + body_l

    # Identify the concrete request without making up an answer to it.
    if any(k in combined for k in ['interview', 'meeting', 'availability', 'schedule', 'call']):
        request = 'The sender is asking you to share your availability for a conversation.'
        missing = ['Your preferred day', 'Your available time and time zone']
        if tone == 'friendly':
            core = 'I’d be happy to arrange a time. My availability is [add your preferred day and time, including time zone]. Let me know what works best.'
        elif tone == 'concise':
            core = 'I’d be happy to speak. I’m available [add your preferred day and time, including time zone]. Please let me know what suits you.'
        else:
            core = 'Thank you for reaching out. I would be happy to arrange a time. My availability is [add your preferred day and time, including time zone]. Please let me know which option works best.'
    elif any(k in combined for k in ['confirm', 'confirmation', 'please confirm']) or '?' in body:
        request = 'The sender appears to be asking for a confirmation or an answer.'
        missing = ['Your answer or confirmation']
        if tone == 'friendly':
            core = 'Thanks for checking with me. [Add your answer or confirmation here]. Happy to clarify anything else.'
        elif tone == 'concise':
            core = 'Thanks for your message. [Add your answer or confirmation here].'
        else:
            core = 'Thank you for your message. [Add your answer or confirmation here]. Please let me know if you need any further information.'
    else:
        request = 'The message may need a response, but the specific answer is not clear from the available text.'
        missing = ['The specific information you want to provide']
        if tone == 'friendly':
            core = 'Thanks for reaching out. [Add the information you want to share].'
        elif tone == 'concise':
            core = 'Thanks for your message. [Add your response here].'
        else:
            core = 'Thank you for getting in touch. [Add the information you want to share in response].'

    if length == 'short':
        opening = 'Hi ' + first_name + ','
        closing = 'Best regards,\n[Your name]'
        reply = f'{opening}\n\n{core}\n\n{closing}'
    else:
        if tone == 'friendly':
            intro = 'Thanks for your message.'
            close_line = 'Looking forward to hearing from you.'
        elif tone == 'concise':
            intro = 'Thank you for reaching out.'
            close_line = ''
        else:
            intro = 'Thank you for your message.'
            close_line = 'I look forward to hearing from you.'
        parts = [f'Hi {first_name},', '', intro + ' ' + core]
        if close_line:
            parts += ['', close_line]
        parts += ['', 'Best regards,', '[Your name]']
        reply = '\n'.join(parts)

    return {
        'reply': reply,
        'context': request,
        'missing': missing,
        'tone': tone,
        'length': length,
        'method': 'Local context rules and templates; no external AI model used.'
    }



def analyze_intelligence(items):
    """Explainable local heuristics for priority, deadlines and duplicates."""
    results=[]
    seen={}
    for item in items:
        subject=(item.get('subject') or '').strip().lower()
        body=(item.get('body') or '').lower()
        sender=(item.get('sender') or '').lower()
        text=subject+' '+body
        # Normalize subject for simple duplicate grouping.
        key=re.sub(r'^(re|fw|fwd):\s*','',subject)
        key=re.sub(r'[^a-z0-9 ]','',key)
        duplicate_of=seen.get(key) if key else None
        if key and key not in seen: seen[key]=item.get('id')
        urgent_terms=['urgent','asap','immediately','today','by end of day','time-sensitive','deadline today']
        high_terms=['by tomorrow','within 24 hours','final reminder','last chance']
        if any(t in text for t in urgent_terms): priority='High'; priority_reason='Contains an explicit urgency phrase.'
        elif any(t in text for t in high_terms): priority='High'; priority_reason='Contains a near-term deadline or final reminder.'
        elif any(t in text for t in ['interview','availability','schedule','meeting','please confirm','would you']): priority='Normal'; priority_reason='Contains a direct request or scheduling question.'
        else: priority='Low'; priority_reason='No strong urgency or action signal detected.'
        deadline=None
        for phrase,label in [('today','Today (verify exact time)'),('tomorrow','Tomorrow (verify exact time)'),('within 24 hours','Within 24 hours'),('by friday','By Friday (date depends on receipt date)'),('by monday','By Monday (date depends on receipt date)')]:
            if phrase in text: deadline=label; break
        if not deadline and re.search(r'\bby\s+(\d{1,2})(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',text): deadline='Specific calendar date mentioned; verify year.'
        if not deadline and priority=='Normal' and any(t in text for t in ['interview','availability','schedule']): deadline='No explicit deadline found.'
        if duplicate_of and duplicate_of != item.get('id'): duplicate='Possible duplicate of '+duplicate_of
        else: duplicate='No matching subject found in this sample inbox.'
        results.append({'id':item.get('id'),'subject':item.get('subject'),'priority':priority,'priority_reason':priority_reason,'deadline':deadline or 'No deadline detected.','duplicate':duplicate,'needs_followup':item.get('category')=='Needs reply','category':item.get('category')})
    return results


def check_reply_quality(reply):
    """Rule-based pre-approval checks; advisory only, not a factual guarantee."""
    reply=reply or ''
    checks=[]
    placeholders=re.findall(r'\[[^\]]+\]',reply)
    if placeholders: checks.append({'label':'Unfinished placeholders','status':'Review','detail':f'{len(placeholders)} placeholder(s): '+', '.join(placeholders)})
    else: checks.append({'label':'Unfinished placeholders','status':'Clear','detail':'No bracketed placeholders detected.'})
    risky=[r'\bI guarantee\b',r'\bI promise\b',r'\bI will definitely\b',r'\bI can attend\b',r'\bI am available\b']
    hits=[pat for pat in risky if re.search(pat,reply,re.I)]
    if hits: checks.append({'label':'Commitments / availability','status':'Review','detail':'Check that any commitment or availability statement is accurate.'})
    else: checks.append({'label':'Commitments / availability','status':'Clear','detail':'No obvious fixed commitment phrase detected; verify factual accuracy manually.'})
    if len(reply.strip())<25: checks.append({'label':'Reply completeness','status':'Review','detail':'The reply is very short; check that it answers the sender.'})
    else: checks.append({'label':'Reply completeness','status':'Clear','detail':'Reply has enough text for a basic review; relevance is not guaranteed.'})
    return {'checks':checks,'ready_for_human_review':not placeholders,'notice':'Heuristic checks only. They do not verify facts, calendar availability or recipient identity.'}


LMSTUDIO_BASE = "http://127.0.0.1:1235/v1"

def lmstudio_request(path, payload=None, timeout=120):
    url = LMSTUDIO_BASE + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LM Studio API returned HTTP {exc.code}: {detail}") from exc

def generate_reply_with_local_ai(item, tone="friendly", length="short"):
    """Generate a draft through the LM Studio localhost API. Demo emails only."""
    # Email content is sent only to the user's LM Studio localhost API.
    if item.get("category") != "Needs reply":
        raise ValueError("A reply suggestion is only available for messages classified as Needs reply.")
    if tone not in {"professional", "friendly", "concise"} or length not in {"short", "balanced"}:
        raise ValueError("Choose a supported tone and length.")

    try:
        models = lmstudio_request("/models", timeout=8)
        model_list = models.get("data", [])
        if not model_list:
            raise RuntimeError("LM Studio is running, but no model is available. Load Qwen2.5 3B in LM Studio and try again.")
        model_id = model_list[0].get("id")
        if not model_id:
            raise RuntimeError("LM Studio did not report a loaded model.")
        system_prompt = (
            "You are InboxPilot, an email reply drafting assistant. "
            "Write only the email reply, without explanations or markdown fences. "
            "Never invent facts, availability, dates, times, promises, attachments, or personal details. "
            "If the sender asks for availability, include this exact placeholder: "
            "[add your preferred day and time, including time zone]. "
            "Use the sender's first name when available. Sign off as Kapilan. "
            "Keep any missing information as a clear bracketed placeholder."
        )
        user_prompt = (
            f"Tone: {tone}. Length: {length}.\n"
            f"Sender: {item.get('sender','')}\n"
            f"Subject: {item.get('subject','')}\n"
            f"Email:\n{item.get('body','')}\n\n"
            "Draft a natural reply. Do not answer questions using information not present in the email or instructions."
        )
        result = lmstudio_request("/chat/completions", {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 220,
            "stream": False
        }, timeout=150)
        reply = (result.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not reply:
            raise RuntimeError("The local model returned an empty reply. Try again.")
        # Deterministic safety override: when a message asks the user to choose
        # a meeting/interview time, never trust model-generated availability.
        source_text = (item.get("subject", "") + " " + item.get("body", "")).lower()
        asks_schedule = any(k in source_text for k in [
            "interview", "availability", "schedule", "call", "meeting",
            "what time", "which day", "work for you"
        ])
        safety_override = False
        if asks_schedule:
            sender = item.get("sender", "")
            sender = sender.split("·", 1)[0].strip()
            first_name = re.split(r"\s+", sender)[0].strip("·,-") if sender else "there"
            reply = (
                f"Hi {first_name},\n\n"
                "Thanks for reaching out. I'd be happy to arrange a time "
                "for a short introductory conversation.\n\n"
                "[add your preferred day and time, including time zone]\n\n"
                "Best,\nKapilan"
            )
            safety_override = True
        return {
            "reply": reply,
            "context": (
                "Qwen generated a draft, then InboxPilot applied a deterministic "
                "availability safeguard. Confirm the placeholder before use."
                if safety_override else
                "Local Qwen model drafted a reply from this fictional sample email. Verify all details."
            ),
            "missing": ["Your preferred day and time, including time zone"] if asks_schedule else [],
            "tone": tone,
            "length": length,
            "method": "LM Studio local API; Qwen model running on this computer.",
            "safety_override": safety_override
        }
    except urllib.error.URLError as exc:
        raise RuntimeError("Cannot reach LM Studio at http://127.0.0.1:1235/v1. Keep its Local Model API server running and ensure Qwen is loaded.") from exc
    except TimeoutError as exc:
        raise RuntimeError("The local model took too long to respond. Try again or use the rules-based generator.") from exc

def row_dict(row):
    return dict(row) if row else None

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print('[InboxPilot] ' + fmt % args)

    def send_json(self, data, status=200):
        raw = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length > 100_000:
            raise ValueError('Request too large')
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.send_json({'ok': True, 'mode': 'public-demo', 'gmail_connected': False,
                                   'sending_enabled': False, 'local_ai_available': False})
        if path == '/api/local-ai/status':
            return self.send_json({'available': False, 'message': 'Local Qwen/LM Studio runs on the owner’s computer and is not connected to this public demo.'})

        if path == '/api/gmail/status':
            configured = bool(load_gmail_config())
            with connect() as db:
                count = db.execute('SELECT COUNT(*) FROM emails WHERE is_demo=0').fetchone()[0]
            return self.send_json({'configured': configured, 'cached_messages': count,
                                   'message': 'Gmail integration is intentionally disabled in this public demo.'})
        if path == '/api/emails':
            with connect() as db:
                rows = db.execute('SELECT * FROM emails ORDER BY is_demo DESC, id DESC').fetchall()
            return self.send_json({'emails': [row_dict(r) for r in rows],
                                   'source': 'PUBLIC DEMO DATA'})
        if path == '/api/intelligence':
            with connect() as db:
                rows = [row_dict(r) for r in db.execute('SELECT * FROM emails ORDER BY id').fetchall()]
            return self.send_json({'results': analyze_intelligence(rows), 'method': 'Local heuristic analysis; not an AI model.'})
        if path == '/api/activity':
            with connect() as db:
                rows = db.execute('''SELECT activity.id, activity.action, activity.created_at,
                    emails.sender, emails.subject FROM activity LEFT JOIN emails ON emails.id=activity.email_id
                    ORDER BY activity.id DESC LIMIT 100''').fetchall()
            return self.send_json({'activity': [row_dict(r) for r in rows]})
        if path.startswith('/api/'):
            return self.send_json({'error': 'Not found'}, 404)
        if path == '/':
            self.path = '/index.html'
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
        except Exception as exc:
            return self.send_json({'error': str(exc)}, 400)

        if path == '/api/gmail/sync':
            return self.send_json({'error': 'Gmail sync is disabled in this public demo. These are fictional sample emails.'}, 403)
        if path == '/api/reply-quality':
            return self.send_json(check_reply_quality(payload.get('reply', '')))

        if path == '/api/classify':
            with connect() as db:
                rows = db.execute('SELECT * FROM emails').fetchall()
                for row in rows:
                    item = row_dict(row)
                    category, confidence, reason = classify_email(item)
                    db.execute('UPDATE emails SET category=?, confidence=?, reason=? WHERE id=?',
                               (category, confidence, reason, item['id']))
                db.execute("INSERT INTO activity(email_id,action) VALUES (NULL, 'Inbox classification run (local rules)')")
            return self.send_json({'ok': True, 'message': 'Sample emails classified using transparent local rules.'})

        if path.startswith('/api/emails/') and path.endswith('/generate-reply-ai'):
            return self.send_json({'error': 'Local Qwen AI is available in the desktop version only. Try the demo reply generator.'}, 503)

        if path.startswith('/api/emails/') and path.endswith('/generate-reply'):
            email_id = path.split('/')[-2]
            with connect() as db:
                row = db.execute('SELECT * FROM emails WHERE id=?', (email_id,)).fetchone()
                if not row:
                    return self.send_json({'error': 'Email not found'}, 404)
                item = row_dict(row)
                try:
                    result = build_reply(item, payload.get('tone', 'professional'), payload.get('length', 'balanced'))
                except ValueError as exc:
                    return self.send_json({'error': str(exc)}, 400)
                db.execute('UPDATE emails SET reply=? WHERE id=?', (result['reply'], email_id))
                db.execute('INSERT INTO activity(email_id,action) VALUES (?,?)', (email_id, 'Context-aware local reply suggestion generated'))
            return self.send_json({'ok': True, **result,
                                   'message': 'A context-aware draft was generated locally. Review and edit it before approval.'})

        if path.startswith('/api/emails/') and path.endswith('/reply'):
            email_id = path.split('/')[-2]
            reply = payload.get('reply', '')
            if not isinstance(reply, str) or len(reply) > 12000:
                return self.send_json({'error': 'Reply must be text under 12,000 characters'}, 400)
            with connect() as db:
                cur = db.execute('UPDATE emails SET reply=? WHERE id=?', (reply, email_id))
                if not cur.rowcount:
                    return self.send_json({'error': 'Email not found'}, 404)
                db.execute('INSERT INTO activity(email_id,action) VALUES (?,?)', (email_id, 'Sample reply edited'))
            return self.send_json({'ok': True, 'message': 'Saved locally. No email was sent.'})

        if path.startswith('/api/emails/') and path.endswith('/decision'):
            email_id = path.split('/')[-2]
            decision = payload.get('decision')
            allowed = {'Approved wording', 'Rejected', 'Awaiting review'}
            if decision not in allowed:
                return self.send_json({'error': 'Unsupported decision'}, 400)
            with connect() as db:
                cur = db.execute('UPDATE emails SET status=? WHERE id=?', (decision, email_id))
                if not cur.rowcount:
                    return self.send_json({'error': 'Email not found'}, 404)
                db.execute('INSERT INTO activity(email_id,action) VALUES (?,?)', (email_id, 'Sample ' + decision.lower()))
            return self.send_json({'ok': True, 'status': decision, 'message': 'Recorded locally. No email was sent or drafted.'})

        if path == '/api/reset':
            with connect() as db:
                for item in SAMPLES:
                    db.execute('UPDATE emails SET reply=?, status=? WHERE id=?',
                               (item['reply'], 'Awaiting review', item['id']))
                db.execute('DELETE FROM activity')
            return self.send_json({'ok': True, 'message': 'Sample workspace reset.'})
        return self.send_json({'error': 'Not found'}, 404)

def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print('\nInboxPilot AI public demo server is running.')
    print(f'Listening on {HOST}:{PORT}')
    print('Fictional demo data only | Gmail disconnected | Cloud AI disabled | Email sending disabled')
    print('Press Ctrl+C to stop the server.\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping InboxPilot AI...')
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
