import os, threading, datetime, io
from flask import Flask, render_template_string, redirect, url_for, request, jsonify, send_file
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError
from openpyxl import Workbook

# --- Config base de datos (Postgres si DATABASE_URL, si no SQLite) ---
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////state.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)
Base = declarative_base()
lock = threading.Lock()

# --- Claves de administración ---
ADMIN_VIEW_KEY = os.environ.get("ADMIN_VIEW_KEY", "")  # ver panel admin
# ADMIN_KEY protege liberar/resetear/exportar (se usa en rutas)

# --- Datos de la rifa ---
RAFFLE_TITLE = os.environ.get("RAFFLE_TITLE", "Rifa Fin de Año")
RAFFLE_PRICE = os.environ.get("RAFFLE_PRICE", "Primer premio: Bolsa de Golf Wilson, Segundo Premio: Termo Stanley, Tercer Premio: Jarra Stanley")
RAFFLE_DATE  = os.environ.get("RAFFLE_DATE",  "Valor 10 Mil pesos, Se sortea al venderse todos los numeros")
BANK_INFO = os.environ.get("BANK_INFO", "").strip()

try:
    PRICE_PER_NUMBER = float(os.environ.get("RAFFLE_PRICE_VALUE", "10"))
except ValueError:
    PRICE_PER_NUMBER = 10.0

class NumberPick(Base):
    __tablename__ = "number_picks"
    id = Column(Integer, primary_key=True)      # 0..99
    taken = Column(Boolean, default=False, nullable=False)
    name = Column(String(80), default="", nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

def init_db():
    Base.metadata.create_all(engine)
    s = Session()
    try:
        if s.query(NumberPick).count() == 0:
            for i in range(100):
                s.add(NumberPick(id=i, taken=False, name=""))
            s.commit()
    finally:
        s.close()

app = Flask(__name__)
init_db()

HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ raffle_title }}</title>
<style>
  :root{ --primary:#14ae5c; --muted:#555; --bgfree:#f6fff6; --bgtaken:#fff4f4; }
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Helvetica,Arial,sans-serif;margin:20px}
  .wrap{max-width:920px;margin:auto}
  h1{margin:0 0 4px}
  .meta{color:var(--muted);margin-bottom:16px}
  .banner{border:1px solid #e5e5e5; border-radius:14px; padding:12px 14px; margin:8px 0 12px; display:flex; gap:10px; align-items:center; background:#fafafa;}
  .badge{background:var(--primary);color:#fff;padding:4px 10px;border-radius:999px;font-weight:600}
  .bank-btns{display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 14px}
  .bank-inline{background:#e8f3ff;border:1px solid #cfe4ff;color:#0b3d91;border-radius:12px;padding:8px 10px}
  .grid{display:grid;grid-template-columns:repeat(10,1fr);gap:8px}
  .cell{padding:10px;border-radius:10px;text-align:center;border:1px solid #ddd}
  .free{background:var(--bgfree)}
  .taken{background:var(--bgtaken);color:#555}
  .cell small{display:block;font-size:12px;color:#666;margin-top:4px}
  .topbar{display:flex;gap:8px;align-items:center;margin:12px 0 16px; flex-wrap:wrap}
  input[type=text]{padding:8px;border:1px solid #ccc;border-radius:8px;min-width:180px}
  button{padding:8px 10px;border:0;border-radius:10px;cursor:pointer}
  .pick{background:var(--primary);color:white}
  .admin-dl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .admin-dl input{flex:0 0 200px}
  .disabled{opacity:.6;cursor:not-allowed}
  details{margin-top:24px}
  .row{display:flex;gap:8px;align-items:center;margin:6px 0}
  .mono{font-variant-numeric:tabular-nums}

  /* Modal bancario */
  .modal-backdrop{
    position:fixed; inset:0; background:rgba(0,0,0,.45); display:none; align-items:center; justify-content:center; z-index:9999;
  }
  .modal{background:#fff; border-radius:16px; max-width:560px; width:92%; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,.2)}
  .modal h2{margin:0 0 8px}
  .modal p{margin:8px 0 0; word-break:break-word}
  .modal .actions{display:flex; gap:8px; justify-content:flex-end; margin-top:14px; flex-wrap:wrap}
  .modal .ghost{background:#f2f2f2}
</style>
</head>
<body>
<div class="wrap">
  <h1>{{ raffle_title }}</h1>
  <div class="banner">
    <span class="badge">Rifa</span>
    <div>
      <div><strong>{{ raffle_price }}</strong></div>
      <div>{{ raffle_date }}</div>
    </div>
  </div>

  {% if bank_info %}
    <div class="bank-btns">
      <span class="bank-inline"><strong>Datos bancarios disponibles</strong></span>
      <button type="button" onclick="openBankModal()">Ver datos bancarios</button>
      <button type="button" onclick="copyBankInfo()">Copiar</button>
    </div>
  {% endif %}

  {% if error_msg %}
    <div class="bank-inline" style="background:#fff3cd;border-color:#ffeeba;color:#856404;">
      {{ error_msg }}
    </div>
  {% endif %}

  <div class="meta">Números libres: <strong id="free-count">{{ free_count }}</strong> / 100</div>

  <div class="topbar">
    <input id="nombre" type="text" placeholder="Tu nombre (obligatorio)" />
    <button onclick="share()">Compartir enlace</button>

    <!-- Descarga para organizador por clave (ADMIN_KEY) -->
    <div class="admin-dl">
      <input id="adminKeyForDownload" type="text" placeholder="ADMIN_KEY para descargar Excel" />
      <button onclick="downloadExcel()">Exportar Excel</button>
      <button onclick="downloadExcelOcupados()">Exportar ocupados + total</button>
    </div>
  </div>

  <div class="grid" id="grid">
    {% for n in numbers %}
      <div class="cell {% if n.taken %}taken{% else %}free{% endif %}" 
           id="cell-{{'%02d' % n.id}}" 
           data-num="{{'%02d' % n.id}}" 
           data-taken="{{ 1 if n.taken else 0 }}" 
           data-name="{{ n.name|e }}">
        <div class="mono"><strong>{{ "%02d" % n.id }}</strong></div>
        {% if not n.taken %}
          <button class="pick" type="button" onclick="pickNumber('{{ '%02d' % n.id }}', this)">Elegir</button>
        {% else %}
          <small>Ocupado por: {{ n.name }}</small>
        {% endif %}
      </div>
    {% endfor %}
  </div>

  {% if show_admin %}
  <details open>
    <summary>Administración</summary>
    <p>Para liberar o reiniciar necesitás la clave de admin (<code>ADMIN_KEY</code>).</p>
    <form class="row" method="post" action="{{ url_for('release', num='00') }}" onsubmit="this.action=this.action.replace('00', document.getElementById('numlib').value);">
      <input id="numlib" type="text" placeholder="Número (00–99)" pattern="\\d\\d" maxlength="2">
      <input name="key" type="text" placeholder="ADMIN_KEY">
      <button type="submit">Liberar</button>
    </form>
    <form class="row" method="post" action="{{ url_for('reset') }}">
      <input name="key" type="text" placeholder="ADMIN_KEY">
      <button type="submit">Reiniciar todo</button>
    </form>
    <div class="row"><a href="{{ url_for('api_state') }}">Ver estado (JSON)</a></div>
    <div class="row"><a href="{{ url_for('export_excel') }}">Exportar a Excel</a></div>
    <div class="row"><a href="{{ url_for('export_occupied_excel') }}">Exportar ocupados + total</a></div>
    <div class="row"><a href="{{ url_for('admin_logout') }}">Cerrar panel</a></div>
  </details>
  {% endif %}
</div>

{% if bank_info %}
<!-- Modal de datos bancarios -->
<div id="bankModal" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="bankTitle">
  <div class="modal">
    <h2 id="bankTitle">Datos bancarios</h2>
    <p id="bankText">{{ bank_info }}</p>
    <div class="actions">
      <button class="ghost" type="button" onclick="closeBankModal()">Cerrar</button>
      <button type="button" onclick="copyBankInfo()">Copiar</button>
    </div>
  </div>
</div>
{% endif %}

<script>
function share(){
  if (navigator.share){ navigator.share({title:document.title, url: window.location.href}); }
  else { navigator.clipboard.writeText(window.location.href); alert("Enlace copiado. Pegalo en el grupo de WhatsApp."); }
}

// Botones de descarga con clave
function downloadExcel(){
  const k = (document.getElementById('adminKeyForDownload') || {}).value || "";
  if(!k){ alert("Ingresá la ADMIN_KEY para descargar."); return; }
  window.location.href = `/export.xlsx?key=${encodeURIComponent(k)}`;
}
function downloadExcelOcupados(){
  const k = (document.getElementById('adminKeyForDownload') || {}).value || "";
  if(!k){ alert("Ingresá la ADMIN_KEY para descargar."); return; }
  window.location.href = `/export-ocupados.xlsx?key=${encodeURIComponent(k)}`;
}

// Modal bancario
function openBankModal(){
  const m = document.getElementById('bankModal'); if(m){ m.style.display='flex'; }
}
function closeBankModal(){
  const m = document.getElementById('bankModal'); if(m){ m.style.display='none'; }
}
async function copyBankInfo(){
  const el = document.getElementById('bankText');
  if(!el) return;
  try{
    await navigator.clipboard.writeText(el.textContent);
    alert("Datos bancarios copiados.");
  }catch(e){
    // Fallback para navegadores antiguos
    const ta = document.createElement('textarea');
    ta.value = el.textContent;
    document.body.appendChild(ta);
    ta.select(); document.execCommand('copy');
    document.body.removeChild(ta);
    alert("Datos bancarios copiados.");
  }
}
// Abrir modal automáticamente si ?bank=1
(function(){
  const params = new URLSearchParams(window.location.search);
  if(params.get('bank') === '1'){ openBankModal(); }
})();

// Render helpers
function renderFreeCell(num){
  return `
    <div class="mono"><strong>${num}</strong></div>
    <button class="pick" type="button" onclick="pickNumber('${num}', this)">Elegir</button>
  `;
}
function renderTakenCell(num, name){
  return `
    <div class="mono"><strong>${num}</strong></div>
    <small>Ocupado por: ${name ? name.replace(/</g,"&lt;").replace(/>/g,"&gt;") : ""}</small>
  `;
}

// Elegir número (validación + confirmación)
async function pickNumber(num, btn){
  const nameInput = document.getElementById('nombre');
  const name = nameInput ? nameInput.value.trim() : "";
  if(!name){
    alert("Escribí tu nombre para poder elegir.");
    if(nameInput) nameInput.focus();
    return;
  }
  if(!confirm(`¿Confirmás elegir el número ${num} a nombre de "${name}"?`)){
    return;
  }
  if(btn){ btn.disabled = true; }
  try{
    const fd = new FormData();
    fd.append('name', name);
    const res = await fetch(`/pick/${num}`, {
      method:'POST',
      headers: {'X-Requested-With':'XMLHttpRequest'},
      body: fd
    });
    if(!res.ok){
      const txt = await res.text();
      alert(txt || "No se pudo completar la reserva.");
      return;
    }
    await refreshState();
  }catch(e){
    alert("No se pudo completar la reserva. Revisá tu conexión e intentá de nuevo.");
  }finally{
    if(btn){ btn.disabled = false; }
  }
}

// Polling cada 5s
async function refreshState(){
  try{
    const res = await fetch('/api/state', {cache:'no-store'});
    if(!res.ok) return;
    const data = await res.json();
    let freeCount = 0;
    for(const item of data){
      const id = 'cell-' + item.num;
      const el = document.getElementById(id);
      if(!el) continue;
      const prevTaken = el.getAttribute('data-taken') === '1';
      if(item.taken){
        el.classList.remove('free'); el.classList.add('taken');
        el.setAttribute('data-taken','1');
        el.setAttribute('data-name', item.name || "");
        if(!prevTaken){ el.innerHTML = renderTakenCell(item.num, item.name || ""); }
      }else{
        freeCount++;
        el.classList.remove('taken'); el.classList.add('free');
        el.setAttribute('data-taken','0');
        el.setAttribute('data-name','');
        if(prevTaken){ el.innerHTML = renderFreeCell(item.num); }
      }
    }
    const fc = document.getElementById('free-count');
    if(fc) fc.textContent = freeCount.toString();
  }catch(e){}
}
setInterval(refreshState, 5000);
</script>
</body>
</html>
"""

@app.get("/")
def index():
    s = Session()
    try:
        numbers = s.query(NumberPick).order_by(NumberPick.id.asc()).all()
        free_count = sum(1 for n in numbers if not n.taken)
        show_admin = (
            (request.args.get("admin", "") == ADMIN_VIEW_KEY and ADMIN_VIEW_KEY != "")
            or (request.cookies.get("is_admin") == "1")
        )
        error_msg = "Escribí tu nombre para poder elegir." if request.args.get("err") == "noname" else ""
        return render_template_string(
            HTML,
            numbers=numbers,
            free_count=free_count,
            show_admin=show_admin,
            raffle_title=RAFFLE_TITLE,
            raffle_price=RAFFLE_PRICE,
            raffle_date=RAFFLE_DATE,
            bank_info=BANK_INFO,
            error_msg=error_msg
        )
    finally:
        s.close()

@app.post("/pick/<num>")
def pick(num):
    name = (request.form.get("name") or "").strip()
    if not (len(num)==2 and num.isdigit()):
        return redirect(url_for("index"))
    if not name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return ("NOMBRE_REQUERIDO", 400)
        return redirect(url_for("index", err="noname"))

    idx = int(num)
    with lock:
        s = Session()
        try:
            row = s.get(NumberPick, idx)
            if row and not row.taken:
                row.taken = True
                row.name = name[:80]
                row.updated_at = datetime.datetime.utcnow()
                s.commit()
        except OperationalError:
            s.rollback()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return ("ERROR_DB", 500)
        finally:
            s.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return ("OK", 200)
    return redirect(url_for("index"))

@app.post("/release/<num>")
def release(num):
    key = request.form.get("key") or ""
    if key != os.environ.get("ADMIN_KEY",""):
        return ("No autorizado", 401)
    if not (len(num)==2 and num.isdigit()):
        return redirect(url_for("index"))
    idx = int(num)
    with lock:
        s = Session()
        try:
            row = s.get(NumberPick, idx)
            if row:
                row.taken = False
                row.name = ""
                row.updated_at = datetime.datetime.utcnow()
                s.commit()
        finally:
            s.close()
    return redirect(url_for("index"))

@app.post("/reset")
def reset():
    key = request.form.get("key") or ""
    if key != os.environ.get("ADMIN_KEY",""):
        return ("No autorizado", 401)
    with lock:
        s = Session()
        try:
            for i in range(100):
                row = s.get(NumberPick, i)
                if row:
                    row.taken = False
                    row.name = ""
                    row.updated_at = datetime.datetime.utcnow()
            s.commit()
        finally:
            s.close()
    return redirect(url_for("index"))

@app.get("/api/state")
def api_state():
    s = Session()
    try:
        data = [
            {"num": f"{n.id:02d}", "taken": n.taken, "name": n.name}
            for n in s.query(NumberPick).order_by(NumberPick.id.asc()).all()
        ]
        return jsonify(data)
    finally:
        s.close()

# --- Exportar a Excel (.xlsx) general ---
@app.get("/export.xlsx")
def export_excel():
    key = request.args.get("key", "")
    is_admin_cookie = (request.cookies.get("is_admin") == "1")
    if not (is_admin_cookie or (key and key == os.environ.get("ADMIN_KEY",""))):
        return ("No autorizado", 401)

    s = Session()
    try:
        rows = s.query(NumberPick).order_by(NumberPick.id.asc()).all()
        wb = Workbook()
        ws = wb.active
        ws.title = "Rifa 00-99"
        ws.append([RAFFLE_TITLE])
        ws.append([RAFFLE_PRICE, RAFFLE_DATE])
        if BANK_INFO:
            ws.append([f"Datos bancarios: {BANK_INFO}"])
        ws.append([])

        ws.append(["Número", "Estado", "Nombre", "Actualizado"])
        for r in rows:
            ws.append([
                f"{r.id:02d}",
                "Ocupado" if r.taken else "Libre",
                r.name,
                r.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            ])
        for col in ["A","B","C","D"]:
            ws.column_dimensions[col].width = 20

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        fname = f"rifa_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(
            bio,
            as_attachment=True,
            download_name=fname,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    finally:
        s.close()

# --- Exportar SOLO ocupados + total recaudado ---
@app.get("/export-ocupados.xlsx")
def export_occupied_excel():
    key = request.args.get("key", "")
    is_admin_cookie = (request.cookies.get("is_admin") == "1")
    if not (is_admin_cookie or (key and key == os.environ.get("ADMIN_KEY",""))):
        return ("No autorizado", 401)

    s = Session()
    try:
        rows = s.query(NumberPick).filter(NumberPick.taken == True).order_by(NumberPick.id.asc()).all()
        count = len(rows)
        total = count * PRICE_PER_NUMBER

        wb = Workbook()
        ws = wb.active
        ws.title = "Participantes"

        ws.append([RAFFLE_TITLE])
        ws.append([RAFFLE_PRICE, RAFFLE_DATE])
        if BANK_INFO:
            ws.append([f"Datos bancarios: {BANK_INFO}"])
        ws.append([f"Precio por número (valor numérico): {PRICE_PER_NUMBER}"])
        ws.append([])

        ws.append(["#", "Número", "Nombre", "Fecha/Hora (UTC)"])
        for i, r in enumerate(rows, start=1):
            ws.append([
                i,
                f"{r.id:02d}",
                r.name,
                r.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            ])

        ws.append([])
        ws.append(["Total ocupados", count])
        ws.append(["Precio por número", PRICE_PER_NUMBER])
        ws.append(["Total recaudado", total])

        for col in ["A","B","C","D","E"]:
            if col in ws.column_dimensions:
                ws.column_dimensions[col].width = 22

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        fname = f"rifa_ocupados_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(
            bio,
            as_attachment=True,
            download_name=fname,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    finally:
        s.close()

# --- Login/Logout de panel admin por cookie ---
@app.get("/admin-login")
def admin_login():
    key = request.args.get("key", "")
    resp = redirect(url_for("index"))
    if key == ADMIN_VIEW_KEY and ADMIN_VIEW_KEY:
        resp.set_cookie("is_admin", "1", max_age=86400, secure=True, httponly=True, samesite="Lax")
    return resp

@app.get("/admin-logout")
def admin_logout():
    resp = redirect(url_for("index"))
    resp.delete_cookie("is_admin")
    return resp

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))












