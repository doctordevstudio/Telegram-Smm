import os
from fastapi import FastAPI, Request, File, UploadFile, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import config, database

app = FastAPI(title="Telegram SMM Admin")
templates = Jinja2Templates(directory="templates")

def check_auth(request: Request):
    if request.cookies.get("auth") != "authed": raise HTTPException(status_code=302, headers={"Location": "/"})

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request): return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    if username == config.ADMIN_USER and password == config.ADMIN_PASS:
        res = RedirectResponse(url="/dashboard", status_code=302)
        res.set_cookie(key="auth", value="authed")
        return res
    return RedirectResponse(url="/?error=1", status_code=302)

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/", status_code=302); res.delete_cookie("auth"); return res

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    check_auth(request)
    users = database.get_all_users()
    orders = database.get_all_orders()
    stats = {
        "total_users": len(users), "total_orders": len(orders),
        "total_earnings": sum(o.get('price', 0) for o in orders.values() if o.get('status') == 'completed'),
        "sessions": len(glob.glob(os.path.join(config.SESSION_DIR, "*.session"))) if os.path.exists(config.SESSION_DIR) else 0
    }
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats, "users": users})

@app.get("/plans", response_class=HTMLResponse)
async def plans(request: Request):
    check_auth(request)
    return templates.TemplateResponse("plans.html", {"request": request, "plans": database.db.reference('plans').get() or {}})

@app.post("/api/update_plan")
async def update_plan(request: Request, service: str = Form(...), price: float = Form(...)):
    check_auth(request)
    database.db.reference(f'plans/{service}/unit_price').set(price)
    return RedirectResponse(url="/plans", status_code=302)

@app.get("/sessions", response_class=HTMLResponse)
async def sessions(request: Request):
    check_auth(request)
    files = os.listdir(config.SESSION_DIR) if os.path.exists(config.SESSION_DIR) else []
    return templates.TemplateResponse("sessions.html", {"request": request, "sessions": files})

@app.post("/api/upload_sessions")
async def upload_ss(request: Request, files: list[UploadFile] = File(...)):
    check_auth(request)
    for f in files:
        if f.filename.endswith(".session"):
            with open(os.path.join(config.SESSION_DIR, f.filename), "wb") as buffer: buffer.write(await f.read())
    return {"status": "ok"}

@app.post("/api/delete_session/{fname}")
async def del_ss(request: Request, fname: str):
    check_auth(request)
    try: os.remove(os.path.join(config.SESSION_DIR, fname))
    except: pass
    return RedirectResponse(url="/sessions", status_code=302)

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    check_auth(request)
    return templates.TemplateResponse("settings.html", {"request": request, "settings": database.get_settings()})

@app.post("/api/update_settings")
async def up_settings(request: Request, upi_id: str = Form(...)):
    check_auth(request)
    database.update_settings("upi_id", upi_id)
    return RedirectResponse(url="/settings", status_code=302)

@app.post("/api/add_api")
async def add_api(request: Request, api_id: int = Form(...), api_hash: str = Form(...)):
    check_auth(request)
    creds = database.get_settings().get('api_credentials', [])
    creds.append({"api_id": api_id, "api_hash": api_hash})
    database.update_settings("api_credentials", creds)
    return RedirectResponse(url="/settings", status_code=302)

@app.get("/payments", response_class=HTMLResponse)
async def payments(request: Request):
    check_auth(request)
    return templates.TemplateResponse("payments.html", {"request": request, "payments": database.get_all_payments()})

@app.get("/ping")
async def ping(): return {"status": "alive"} # For UptimeRobot
