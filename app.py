import hmac
import os
import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel

API_ID = int(os.environ["31034938"])
API_HASH = os.environ["e9cf38cb39ee3f382a2747ee5c466cbc"]
PASSWORD = os.environ["Go"]
SESSION = os.getenv("SESSION", "")

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
login = {"client": None, "phone": None, "hash": None}
app = FastAPI()
LINK = re.compile(r"(?:https?://)?t\.me/(c/)?([A-Za-z0-9_]+)/(\d+)")


def check(key):
    if not hmac.compare_digest(key or "", PASSWORD):
        raise HTTPException(401, "Wrong password.")


@app.on_event("startup")
async def startup():
    await client.connect()


@app.on_event("shutdown")
async def shutdown():
    await client.disconnect()


async def get_msg(link):
    if not await client.is_user_authorized():
        raise HTTPException(503, "Not logged in yet. Open /setup first.")
    m = LINK.search(link.strip())
    if not m:
        raise HTTPException(400, "Not a valid t.me message link.")
    private, who, msg_id = m.groups()
    entity = PeerChannel(int(who)) if private else who
    try:
        msg = await client.get_messages(entity, ids=int(msg_id))
    except Exception as e:
        raise HTTPException(404, f"Could not open that chat: {e}")
    if not msg or not msg.media or not msg.file:
        raise HTTPException(404, "That message has no downloadable file.")
    return msg


def name_of(msg):
    return msg.file.name or f"telegram_{msg.id}{msg.file.ext or ''}"


@app.get("/info")
async def info(link: str = Query(...), key: str = Query("")):
    check(key)
    msg = await get_msg(link)
    return {"name": name_of(msg), "size": msg.file.size}


@app.get("/download")
async def download(link: str = Query(...), key: str = Query("")):
    check(key)
    msg = await get_msg(link)

    async def stream():
        async for chunk in client.iter_download(msg.media, request_size=1024 * 1024):
            yield chunk

    headers = {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name_of(msg))}
    if msg.file.size:
        headers["Content-Length"] = str(msg.file.size)
    return StreamingResponse(
        stream(), media_type=msg.file.mime_type or "application/octet-stream", headers=headers
    )


@app.post("/setup/send")
async def setup_send(request: Request):
    d = await request.json()
    check(d.get("key"))
    c = TelegramClient(StringSession(), API_ID, API_HASH)
    await c.connect()
    sent = await c.send_code_request(d["phone"].strip())
    login.update(client=c, phone=d["phone"].strip(), hash=sent.phone_code_hash)
    return {"ok": True}


@app.post("/setup/verify")
async def setup_verify(request: Request):
    d = await request.json()
    check(d.get("key"))
    c = login["client"]
    if not c:
        raise HTTPException(400, "Start again: send the code first.")
    try:
        await c.sign_in(login["phone"], d.get("code", "").strip(), phone_code_hash=login["hash"])
    except SessionPasswordNeededError:
        if not d.get("password"):
            return {"need_password": True}
        await c.sign_in(password=d["password"])
    return {"session": c.session.save()}


SETUP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Setup</title>
<style>body{font:17px system-ui;max-width:480px;margin:0 auto;padding:24px}
input,button{width:100%;padding:14px;margin:6px 0;font:inherit;box-sizing:border-box}
textarea{width:100%;height:140px;font:14px monospace}#m{color:#b00}</style></head><body>
<h2>One-time Telegram login</h2>
<input id="key" type="password" placeholder="Your APP_PASSWORD">
<div id="s1"><input id="phone" placeholder="Phone with country code, e.g. +91..."><button onclick="send()">Send code</button></div>
<div id="s2" hidden><input id="code" placeholder="Code from Telegram app">
<input id="pw" type="password" placeholder="Telegram 2-step password (only if you have one)">
<button onclick="verify()">Log in</button></div>
<div id="s3" hidden><p>Copy this whole text. Add it on Render as the variable SESSION. Keep it secret.</p><textarea id="out" readonly></textarea></div>
<p id="m"></p>
<script>
const $=id=>document.getElementById(id);
async function post(u,b){const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
const j=await r.json();if(!r.ok)throw new Error(j.detail||"Error");return j;}
async function send(){try{$("m").textContent="";await post("/setup/send",{key:$("key").value,phone:$("phone").value});
$("s1").hidden=true;$("s2").hidden=false;}catch(e){$("m").textContent=e.message}}
async function verify(){try{$("m").textContent="";const j=await post("/setup/verify",{key:$("key").value,code:$("code").value,password:$("pw").value});
if(j.need_password){$("m").textContent="Enter your Telegram 2-step password above, then tap Log in again.";return}
$("s2").hidden=true;$("s3").hidden=false;$("out").value=j.session;}catch(e){$("m").textContent=e.message}}
</script></body></html>"""


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return SETUP_PAGE


@app.get("/")
async def home():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))
