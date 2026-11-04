

# 김대리 개인비서 에이블의 Streamlit 화면
import json
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText

import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from streamlit_calendar import calendar

def load_local_env():
    if not os.path.exists("api_key.txt"):
        return
    with open("api_key.txt", encoding="utf-8") as f:
        lines = f.read().splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value in ('"""', "'''"):
            quote = value
            chunks = []
            while index < len(lines) and lines[index].strip() != quote:
                chunks.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].strip() == quote:
                index += 1
            value = "\n".join(chunks).strip()
        else:
            value = value.strip('"').strip("'")
        os.environ.setdefault(key, value)


load_local_env()
client = OpenAI()
MODEL = "gpt-6-luna"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
KST = timezone(timedelta(hours=9))
MAX_TURNS = 10

INSTRUCTIONS = """
당신은 김대리의 개인 비서 에이블입니다.
입력에 들어 있는 최근 대화를 이어서 이해합니다.
일상적인 대화는 도구 없이 직접 답합니다.
회의 요약, 미팅 정리, 배정 업무 확인 요청일 때만 summarize_meeting을 호출합니다.
이메일로 보내 달라는 말이 있을 때만 send_email을 호출합니다.
캘린더에 등록해 달라는 말이 있으면 register_calendar_events를 호출합니다.
사용자가 기준일을 말하면 meeting_date에 YYYY-MM-DD로 넣고, 말하지 않으면 빈 문자열을 넘깁니다.
기준일이 없어도 등록 도구를 호출하고, 날짜를 다시 물어보지 않습니다.
사용자가 요청하지 않은 도구는 호출하지 않습니다.
도구 결과가 오기 전에는 요약, 발송 완료, 등록 완료를 말하지 않습니다.
요약 도구가 돌려준 summary만 짧게 말합니다.
"""

TOOLS = [
    {
        "type": "function",
        "name": "summarize_meeting",
        "description": "업로드된 회의록을 요약하고 김대리 업무 체크리스트를 만듭니다. 회의 요약, 미팅 정리, 배정 업무 확인을 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "send_email",
        "description": "저장된 회의 요약을 이메일로 보냅니다. 사용자가 이메일 발송을 명시적으로 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "수신 이메일 주소"},
                "subject": {"type": "string", "description": "이메일 제목"},
            },
            "required": ["to", "subject"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "register_calendar_events",
        "description": "날짜가 확정된 김대리 업무를 구글 캘린더와 앱 캘린더에 함께 등록합니다. 사용자가 캘린더 등록을 명시적으로 요청할 때만 호출합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_date": {
                    "type": "string",
                    "description": "사용자가 말한 회의 기준일. YYYY-MM-DD. 말하지 않았으면 빈 문자열.",
                },
            },
            "required": ["meeting_date"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

st.set_page_config(page_title="에이블", page_icon="🤖", layout="wide")
st.title("김대리의 개인비서 에이블")
st.write("음성이나 텍스트로 요청하면 최근 대화 10개까지 이어서 답합니다.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "tasks" not in st.session_state:
    st.session_state.tasks = []
if "summary" not in st.session_state:
    st.session_state.summary = ""
if "events" not in st.session_state:
    st.session_state.events = []
if "calendar_tasks" not in st.session_state:
    st.session_state.calendar_tasks = []
if "meeting_text" not in st.session_state:
    st.session_state.meeting_text = ""


def meeting_text():
    if st.session_state.meeting_text.strip():
        return st.session_state.meeting_text
    if os.path.exists("meeting.txt"):
        with open("meeting.txt", encoding="utf-8") as f:
            return f.read()
    return ""


def load_google_credentials():
    info = None
    raw = os.getenv("GOOGLE_TOKEN", "").strip()
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = None
    if not info and os.path.exists("token.json"):
        with open("token.json", encoding="utf-8") as f:
            info = json.load(f)
    if not info:
        raise RuntimeError("token.json 또는 GOOGLE_TOKEN 비밀값이 없습니다.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_calendar_service():
    return build("calendar", "v3", credentials=load_google_credentials())


def send_email(to, subject, body):
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to, msg.as_string())
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def sync_checklist(events):
    previous = {task["title"]: task for task in st.session_state.tasks}
    tasks = []
    seen = set()
    for event in events:
        title = (event.get("summary") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        current = previous.get(title, {"title": title, "done": False, "event_id": ""})
        current["due_text"] = (event.get("due_text") or "").strip()
        tasks.append(current)
    st.session_state.tasks = tasks


def summarize_meeting():
    text = meeting_text()
    if not text.strip():
        return {"status": "failed", "error": "회의록 txt를 먼저 업로드하세요."}

    prompt = f"""
다음 회의록을 읽고 JSON만 작성해 주세요.

summary에는 아래 형식의 한국어 요약만 넣습니다.
- 회의록에 명시된 사실만 사용하고, 추측하거나 세부사항을 만들지 마세요.
- 제안이나 의견을 확정된 결정으로 표현하지 마세요.
- 담당자나 기한이 없으면 "명시되지 않음"이라고 적으세요.
- "빨리", "조속히"처럼 날짜가 아닌 표현은 그대로 적고, 구체적인 기한은 미정이라고 덧붙이세요.
- 김대리의 업무와 다른 사람의 업무를 섞지 마세요.

summary 형식:
첫 줄은 회의 주제를 쓰고 "미팅 내용을 요약해 전달드립니다."라고 이어서 작성합니다.
[미팅 요약]
- 주제별로 한 줄씩 작성합니다.
[김대리님께 배정된 업무]
1. 업무 제목
  - 해야 할 일을 회의록 표현 그대로 적습니다.
  - 관련 의견을 정리합니다.
  - 합의된 기한 표현을 적고, 없으면 "별도의 완료 기한은 회의에서 정해지지 않았습니다."라고 적습니다.
김대리 업무가 없으면 [김대리님께 배정된 업무] 아래에 "배정된 업무 없음"이라고 적습니다.

events에는 김대리에게 배정된 업무만 넣습니다. 다른 사람 업무는 넣지 않습니다.
- summary는 업무 제목입니다.
- description은 해야 할 일입니다.
- owner는 김대리로만 적습니다.
- due_text는 회의록의 기한 표현 그대로 적고, 없으면 빈 문자열입니다.
- 회의록에 YYYY-MM-DD 날짜가 있을 때만 start_date와 end_date를 채웁니다.
- 날짜가 없으면 start_date와 end_date는 빈 문자열입니다.
- "다음 주", "빨리"를 날짜로 바꾸지 마세요.
- 시각이 없으면 has_clock_time은 false입니다.

[회의록]
{text}
"""
    response = client.responses.create(
        model=MODEL,
        instructions="당신은 김대리의 개인 비서 에이블입니다. 회의록에 있는 사실만 사용합니다.",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "meeting_pack",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "summary": {"type": "string"},
                                    "description": {"type": "string"},
                                    "owner": {"type": "string"},
                                    "due_text": {"type": "string"},
                                    "has_clock_time": {"type": "boolean"},
                                    "start_date": {"type": "string"},
                                    "end_date": {"type": "string"},
                                },
                                "required": [
                                    "summary", "description", "owner", "due_text",
                                    "has_clock_time", "start_date", "end_date",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["summary", "events"],
                    "additionalProperties": False,
                },
            }
        },
    )
    data = json.loads(response.output_text)
    events = [
        event for event in data["events"]
        if event.get("owner", "").replace(" ", "").removesuffix("님") == "김대리"
    ]
    st.session_state.summary = data["summary"]
    st.session_state.events = events
    sync_checklist(events)
    return {"summary": data["summary"]}


def send_meeting_email(to, subject):
    if not to:
        return {"status": "failed", "error": "수신 이메일 주소가 없습니다."}
    if not st.session_state.summary:
        summarized = summarize_meeting()
        if summarized.get("status") == "failed":
            return summarized
    return send_email(to, subject or "회의 요약", st.session_state.summary)


def next_week_friday(meeting_date):
    base = date.fromisoformat(meeting_date)
    days_until_next_monday = (7 - base.weekday()) % 7 or 7
    friday = base + timedelta(days=days_until_next_monday + 4)
    return friday.isoformat(), (friday + timedelta(days=1)).isoformat()


def event_dates(event, meeting_date):
    due_text = (event.get("due_text") or "").strip()
    if "다음 주" in due_text and not event.get("has_clock_time"):
        return next_week_friday(meeting_date)

    start_date = (event.get("start_date") or "").strip()
    end_date = (event.get("end_date") or "").strip()
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        return None
    try:
        date.fromisoformat(end_date)
    except ValueError:
        end_date = (start + timedelta(days=1)).isoformat()
    return start_date, end_date


def put_app_calendar(title, start_date, event_id=""):
    for event in st.session_state.calendar_tasks:
        if event["title"] == title:
            event["start"] = start_date
            event["allDay"] = True
            if event_id:
                event["event_id"] = event_id
            return
    st.session_state.calendar_tasks.append({
        "title": title,
        "start": start_date,
        "allDay": True,
        "event_id": event_id,
    })


def remember_event_id(title, event_id):
    for task in st.session_state.tasks:
        if task["title"] == title:
            task["event_id"] = event_id


def create_calendar_event(summary, start_date, end_date, description):
    try:
        service = get_calendar_service()
        created = service.events().insert(
            calendarId="primary",
            body={
                "summary": summary,
                "description": description,
                "start": {"date": start_date},
                "end": {"date": end_date},
            },
        ).execute()
        return {
            "status": "success",
            "summary": summary,
            "start_date": start_date,
            "event_id": created["id"],
        }
    except Exception as e:
        return {"status": "failed", "summary": summary, "error": str(e)}


def delete_calendar_event(event_id):
    if not event_id:
        return {"status": "success"}
    try:
        service = get_calendar_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def register_calendar_events(meeting_date=""):
    meeting_date = (meeting_date or "").strip()
    if not meeting_date:
        meeting_date = datetime.now(KST).date().isoformat()
    else:
        try:
            date.fromisoformat(meeting_date)
        except ValueError:
            return {"status": "failed", "error": "기준일은 YYYY-MM-DD 형식이어야 합니다."}
    if not st.session_state.events:
        summarized = summarize_meeting()
        if summarized.get("status") == "failed":
            return summarized

    registered = []
    skipped = []
    for event in st.session_state.events:
        title = event.get("summary", "")
        dates = event_dates(event, meeting_date)
        if not dates:
            skipped.append({"summary": title, "reason": "등록할 날짜가 없습니다."})
            continue
        description = event.get("description", "")
        due_text = (event.get("due_text") or "").strip()
        if due_text:
            description = f"{description}\n기한 원문: {due_text}\n기준일: {meeting_date}"
        put_app_calendar(title, dates[0])
        result = create_calendar_event(title, dates[0], dates[1], description)
        if result["status"] == "success":
            event["event_id"] = result["event_id"]
            put_app_calendar(title, dates[0], result["event_id"])
            remember_event_id(title, result["event_id"])
            registered.append(result)
        else:
            skipped.append({"summary": title, "reason": result["error"]})
    return {"status": "success", "registered": registered, "skipped": skipped, "meeting_date": meeting_date}


def run_tool(name, arguments):
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    arguments = arguments or {}
    if name == "summarize_meeting":
        return summarize_meeting()
    if name == "send_email":
        return send_meeting_email(arguments.get("to", ""), arguments.get("subject", "회의 요약"))
    if name == "register_calendar_events":
        return register_calendar_events(arguments.get("meeting_date", ""))
    return {"error": f"unknown tool: {name}"}


def ask_assistant(user_text):
    items = list(st.session_state.messages[-(MAX_TURNS * 2):])
    items.append({"role": "user", "content": user_text})
    response = client.responses.create(
        model=MODEL,
        instructions=INSTRUCTIONS,
        input=items,
        tools=TOOLS,
    )
    for _ in range(3):
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            result = run_tool(call.name, call.arguments)
            outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
        items = [*items, *response.output, *outputs]
        response = client.responses.create(
            model=MODEL,
            instructions=INSTRUCTIONS,
            input=items,
            tools=TOOLS,
        )
    answer = response.output_text or "요청을 처리하지 못했습니다."
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.session_state.messages = st.session_state.messages[-(MAX_TURNS * 2):]
    return answer


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

with st.form("task_form"):
    request_text = st.text_area("텍스트 요청")
    audio_value = st.audio_input("음성 요청")
    uploaded_file = st.file_uploader("회의록 텍스트", type=["txt"])
    submit = st.form_submit_button("실행")

if st.session_state.meeting_text:
    st.caption("회의록이 저장되어 있습니다. 다음 요청에도 그대로 사용합니다.")
else:
    st.caption("요약을 하려면 회의록 txt를 함께 올려 주세요.")

if submit:
    if uploaded_file is not None:
        st.session_state.meeting_text = uploaded_file.getvalue().decode("utf-8")
    parts = []
    if request_text and request_text.strip():
        parts.append(request_text.strip())
    if audio_value is not None:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.wav", audio_value, "audio/wav"),
        )
        if transcript.text.strip():
            parts.append(transcript.text.strip())
    if not parts:
        st.warning("음성이나 텍스트로 요청해 주세요.")
    else:
        with st.spinner("답변을 준비하고 있습니다..."):
            try:
                ask_assistant("\n".join(parts))
                st.rerun()
            except Exception as e:
                st.error(str(e))

if st.session_state.summary:
    st.divider()
    st.subheader("회의 요약")
    st.write(st.session_state.summary)

st.divider()
st.subheader("김대리 업무 체크리스트")
st.write("요약을 요청하면 김대리 업무가 여기에 생깁니다. 완료를 체크하면 연결된 캘린더 일정을 지웁니다.")

completed = 0
for task in st.session_state.tasks:
    label = task["title"]
    if task.get("due_text"):
        label = f"{label} ({task['due_text']})"
    checked = st.checkbox(label, key=f"done_{task['title']}")
    if checked:
        completed += 1
    if checked == task["done"]:
        continue
    task["done"] = checked
    if checked and task.get("event_id"):
        delete_calendar_event(task["event_id"])
        event_id = task["event_id"]
        task["event_id"] = ""
        st.session_state.calendar_tasks = [
            event for event in st.session_state.calendar_tasks
            if event.get("event_id") != event_id and event.get("title") != task["title"]
        ]
    st.rerun()

total = len(st.session_state.tasks)
st.progress(completed / total if total else 0)
st.write(f"완료한 업무: {completed}/{total}개")

st.divider()
st.subheader("업무 캘린더")
with st.form("calendar_form"):
    titles = [task["title"] for task in st.session_state.tasks]
    task_name = st.selectbox("앱 캘린더에 표시할 업무", options=titles) if titles else None
    deadline = st.date_input("마감일", value=date.today())
    submitted = st.form_submit_button("앱 캘린더에 등록/날짜 수정")
    if submitted:
        if task_name:
            put_app_calendar(task_name, deadline.isoformat())
            st.success("앱 캘린더에 반영했습니다.")
        else:
            st.warning("먼저 요약을 요청해 업무를 만들어 주세요.")

calendar(
    events=st.session_state.calendar_tasks,
    options={
        "initialView": "dayGridMonth",
        "locale": "ko",
        "height": 550,
    },
    key="work_calendar",
)
