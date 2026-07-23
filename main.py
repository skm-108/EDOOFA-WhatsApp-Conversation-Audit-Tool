import os
import re
import json
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import google.generativeai as genai
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Edoofa WhatsApp Conversation Audit Tool")

# Enable CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------
# CHAT PARSER
# ----------------------------------------------------------------

def parse_whatsapp_chat(text: str) -> List[dict]:
    # Regex to match: [date, time] - Sender: Message or [date, time] - System event
    # e.g., "27/03/26, 8:53 am - Career Counselor: Greetings..."
    # Support various space character encodings (like \u202f narrow no-break space)
    pattern = re.compile(
        r'^(\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*(?:[ap]\.m\.|[ap]m|am|pm|AM|PM| am| pm)?)\s*-\s*(.*?)$',
        re.UNICODE
    )
    
    messages = []
    current_message = None
    line_number = 0
    
    for raw_line in text.splitlines():
        line_number += 1
        line = raw_line.strip()
        if not line:
            if current_message:
                current_message["text"] += "\n"
            continue
            
        match = pattern.match(line)
        if match:
            if current_message:
                messages.append(current_message)
            
            timestamp = match.group(1).strip()
            content = match.group(2).strip()
            
            # Differentiate message from system events
            if ":" in content:
                parts = content.split(":", 1)
                sender = parts[0].strip()
                body = parts[1].strip()
                current_message = {
                    "id": len(messages) + 1,
                    "line": line_number,
                    "type": "message",
                    "timestamp": timestamp,
                    "sender": sender,
                    "text": body
                }
            else:
                current_message = {
                    "id": len(messages) + 1,
                    "line": line_number,
                    "type": "event",
                    "timestamp": timestamp,
                    "sender": "System",
                    "text": content
                }
        else:
            if current_message:
                current_message["text"] += "\n" + line
            else:
                # File starts with non-date header line
                current_message = {
                    "id": len(messages) + 1,
                    "line": line_number,
                    "type": "info",
                    "timestamp": "",
                    "sender": "System",
                    "text": line
                }
                
    if current_message:
        messages.append(current_message)
        
    return messages

# ----------------------------------------------------------------
# Pydantic Schemas
# ----------------------------------------------------------------

class SyncRow(BaseModel):
    chatId: str
    category: str
    severity: str
    title: str
    description: str
    evidence: str
    impact: str
    guidance: str

class SyncRequest(BaseModel):
    sheetId: Optional[str] = None
    appsScriptUrl: Optional[str] = None
    useServiceAccount: bool = False
    rows: List[SyncRow]

# ----------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------

from fastapi import Request

@app.post("/api/upload")
async def upload_chat(request: Request):
    try:
        form = await request.form()
        file_field = form.get("file")
        filename_field = form.get("filename", "Uploaded Chat")
        
        filename = filename_field
        text = ""
        
        if file_field is not None:
            if isinstance(file_field, str):
                # Handle case where client sent mock string or stringified [object Object]
                if file_field == "[object Object]" or not file_field.strip():
                    import os
                    # Locate local chat log copy
                    paths_to_try = [filename_field, f"static/{filename_field}"]
                    fallback_text = None
                    for path in paths_to_try:
                        if os.path.exists(path):
                            with open(path, "r", encoding="utf-8") as f:
                                fallback_text = f.read()
                            break
                    
                    if fallback_text is not None:
                        text = fallback_text
                    else:
                        raise HTTPException(status_code=400, detail="Corrupted file uploaded and local demo files could not be found.")
                else:
                    text = file_field
            else:
                # Normal UploadFile case
                filename = file_field.filename or filename_field
                content_bytes = await file_field.read()
                text = content_bytes.decode("utf-8", errors="ignore")
        else:
            raise HTTPException(status_code=400, detail="No file payload detected in request form.")

        messages = parse_whatsapp_chat(text)
        return {
            "filename": filename,
            "message_count": len(messages),
            "messages": messages
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error parsing chat: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse chat log: {str(e)}")


@app.post("/api/analyze")
async def analyze_chat(
    payload: dict,
    x_gemini_key: Optional[str] = Header(None)
):
    messages = payload.get("messages", [])
    filename = payload.get("filename", "Unknown Chat")
    
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided for analysis")
        
    # Get Gemini key from headers or env
    api_key = x_gemini_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API Key is missing. Please provide it in settings or set GEMINI_API_KEY environment variable."
        )
        
    try:
        genai.configure(api_key=api_key)
        
        # Prepare context by truncating/formatting messages
        chat_transcript = ""
        for msg in messages:
            if msg["type"] == "message":
                chat_transcript += f"[{msg['timestamp']}] Line {msg['line']} - {msg['sender']}: {msg['text']}\n"
            else:
                chat_transcript += f"[{msg['timestamp']}] Line {msg['line']} - SYSTEM: {msg['text']}\n"
                
        # Define the system prompt detailing the framework
        system_prompt = """You are a senior auditor at Edoofa. Your role is to evaluate WhatsApp chat transcripts between Career Counselors and prospective students/families.
Edoofa's mission is to guide families towards higher education in India through scholarships and mentorship. We pride ourselves on trust, transparency, and deep empathy.

Analyze the provided chat transcript against our 5 Audit Framework categories:
1. Compassion, Empathy & Family Care (CEFC) - Measures sensitivity to bereavement, illness, financial hardships, holidays, or pauses.
2. Urgency, Pressure & Coercion Tactics (UPMT) - Measures artificial scarcity, pushy/rapid follow-ups, arbitrary deadlines (e.g. submit by midnight), or threats of application closure.
3. Financial Transparency & Fee Disclosure (FTFD) - Measures upfront transparency of fees, bank charges, and locking in rates vs evasiveness and dividing fees into parts just to secure a sign-up.
4. Tone Shift & Professional Boundaries (TSPB) - Measures shifts from warm/friendly to cold, defensive, guilt-tripping, or argumentative behavior when met with objections.
5. Multi-Stakeholder Alignment & Sponsor Bypassing (MSAB) - Measures counselor alignment with key financial sponsors (parents, grandparents) vs bypass attempts to coerce the student alone.

Examine the entire conversation to identify specific findings. Do not list every minor detail; focus on significant issues and patterns that span multiple messages or indicate deep counseling violations.

You must output a JSON object matching this schema exactly:
{
  "summary": {
    "student_name": "Name/Role of student in the chat",
    "counselor_name": "Counselor role/name",
    "duration": "E.g., 27/03/26 to 10/04/26",
    "outcome": "Brief summary of how the conversation ended (e.g., application closed due to high pressure, successfully reserved, pending, etc.)"
  },
  "macro_patterns": [
    {
      "title": "Descriptive title of macro pattern",
      "description": "Analysis of how this pattern spans the conversation."
    }
  ],
  "findings": [
    {
      "category": "CEFC" | "UPMT" | "FTFD" | "TSPB" | "MSAB",
      "severity": "Critical" | "Major" | "Minor",
      "title": "Clear, concise title representing the issue",
      "description": "Detailed explanation of what the counselor did, how it violated the framework, and why it is a problem.",
      "evidence": [
        {
          "timestamp": "Timestamp of the message",
          "quote": "Exact message quote of interest",
          "line": 123
        }
      ],
      "impact": "Why this matters for Edoofa specifically (business reputation, enrollment dropouts, trust damage)",
      "guidance": "Actionable feedback on how the counselor should have behaved instead (e.g. expressing condolences, agreeing to a pause, offering transparent payment timelines, respecting boundaries)"
    }
  ]
}

Only return the JSON structure. Do not wrap it in markdown code blocks like ```json ... ```. Do not add any text before or after the JSON. Verify that your JSON is syntactically valid.
"""

        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            system_instruction=system_prompt
        )
        response = model.generate_content(
            contents=[
                {"role": "user", "parts": [f"Chat Transcript from file: {filename}\n\n{chat_transcript}"]},
            ],
            generation_config={"response_mime_type": "application/json"}
        )
        
        result_text = response.text.strip()
        # Parse JSON to confirm it is valid
        report = json.loads(result_text)
        return report
        
    except json.JSONDecodeError as je:
        logger.error(f"Gemini returned invalid JSON: {result_text}")
        raise HTTPException(status_code=500, detail="Gemini API returned an invalid JSON report.")
    except Exception as e:
        logger.error(f"Error calling Gemini: {e}")
        raise HTTPException(status_code=500, detail=f"LLM Analysis failed: {str(e)}")


@app.post("/api/sync")
async def sync_sheet(payload: SyncRequest):
    # Syncs findings to Google Sheets
    if payload.appsScriptUrl:
        url = payload.appsScriptUrl.strip()
        # Verify it is a valid deployed Web App URL
        if "/macros/s/" not in url or "/exec" not in url:
            raise HTTPException(
                status_code=400, 
                detail="Invalid Google Apps Script URL. You pasted the Script Editor URL. Please click 'Deploy' -> 'New Deployment' -> choose 'Web App' -> set access to 'Anyone', then copy the deployment URL containing '/macros/s/.../exec'."
            )
        
        # Use Google Apps Script (Webhook)
        try:
            # Transform pydantic rows to serializable dicts
            rows_data = [row.model_dump() for row in payload.rows]
            resp = requests.post(
                url,
                json={
                    "sheetId": payload.sheetId,
                    "rows": rows_data
                },
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                raise HTTPException(status_code=resp.status_code, detail=f"Apps Script error: {resp.text}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error calling Apps Script Webhook: {e}")
            raise HTTPException(status_code=500, detail=f"Webhook sync failed: {str(e)}")
            
    elif payload.useServiceAccount:
        # Use local service account key
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            
            # Check credentials.json exists
            creds_path = "credentials.json"
            if not os.path.exists(creds_path):
                raise HTTPException(status_code=400, detail="credentials.json file was not found in project directory.")
                
            scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
                     "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            client = gspread.authorize(creds)
            
            if not payload.sheetId:
                raise HTTPException(status_code=400, detail="Spreadsheet ID is required for direct Service Account sync.")
                
            sh = client.open_by_key(payload.sheetId)
            
            # Find or create sheet
            sheet_name = "Audit Findings"
            try:
                worksheet = sh.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sh.add_worksheet(title=sheet_name, rows="100", cols="9")
                # Format headers
                worksheet.append_row([
                    "Chat ID / Student ID",
                    "Date Audited",
                    "Category",
                    "Severity",
                    "Finding Title",
                    "Finding Description",
                    "Evidence (Quotes & Timestamps)",
                    "Edoofa Business Impact",
                    "Actionable Guidance"
                ])
                worksheet.format("A1:I1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.88, "green": 0.91, "blue": 0.94}})
                
            # Prepare rows
            import datetime
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            
            rows_to_append = []
            for row in payload.rows:
                rows_to_append.append([
                    row.chatId,
                    today,
                    row.category,
                    row.severity,
                    row.title,
                    row.description,
                    row.evidence,
                    row.impact,
                    row.guidance
                ])
                
            worksheet.append_rows(rows_to_append)
            return {"status": "success", "count": len(rows_to_append)}
            
        except Exception as e:
            logger.error(f"Error direct sync to Google Sheets: {e}")
            raise HTTPException(status_code=500, detail=f"Service Account sync failed: {str(e)}")
            
    else:
        raise HTTPException(status_code=400, detail="Either Google Apps Script URL or Service Account must be enabled to sync.")

# Serve frontend static assets from a static folder
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # If this is run directly, start uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
