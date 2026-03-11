from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import uuid
from datetime import datetime
import csv
import io
import json
import os
import sys
import sqlite3
from PIL import Image, ImageDraw
import pystray

app = FastAPI()

# Configuración de CORS para permitir que el HTML local hable con el servidor
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modelo para configuración de motivos
class Motivo(BaseModel):
    nombre: str
    monto: int

# Modelo de datos
class Paciente(BaseModel):
    id: Optional[str] = None
    nombre: str
    motivo: str
    monto: int = 0
    estado: str = "espera" # espera, consulta, finalizado
    hora_creacion: Optional[str] = None
    hora_salida: Optional[str] = None

# --- Base de Datos SQLite ---
DB_SQLITE = "dingdong.db"

def init_db():
    conn = sqlite3.connect(DB_SQLITE)
    cursor = conn.cursor()
    # Tabla de Pacientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pacientes (
            id TEXT PRIMARY KEY,
            nombre TEXT,
            motivo TEXT,
            monto INTEGER,
            estado TEXT,
            hora_creacion TEXT,
            hora_salida TEXT
        )
    ''')
    # Tabla de Motivos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS motivos (
            nombre TEXT PRIMARY KEY,
            monto INTEGER
        )
    ''')
    conn.commit()
    
    # Migración desde JSON si existe
    if os.path.exists("database.json"):
        try:
            with open("database.json", "r", encoding="utf-8") as f:
                datos = json.load(f)
                # Migrar motivos
                for m in datos.get("motivos", []):
                    cursor.execute("INSERT OR IGNORE INTO motivos (nombre, monto) VALUES (?, ?)", (m['nombre'], m['monto']))
                # Migrar pacientes
                for p in datos.get("pacientes", []):
                    cursor.execute("INSERT OR IGNORE INTO pacientes (id, nombre, motivo, monto, estado, hora_creacion, hora_salida) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                 (p['id'], p['nombre'], p['motivo'], p.get('monto', 0), p['estado'], p['hora_creacion'], p['hora_salida']))
            conn.commit()
            print("Migración de JSON a SQLite completada.")
        except Exception as e:
            print(f"Error en migración: {e}")
    
    # Valores por defecto si no hay motivos
    cursor.execute("SELECT COUNT(*) FROM motivos")
    if cursor.fetchone()[0] == 0:
        default_motivos = [("Consulta General", 200), ("Revisión", 100), ("Urgencia", 500)]
        cursor.executemany("INSERT INTO motivos (nombre, monto) VALUES (?, ?)", default_motivos)
        conn.commit()
    
    conn.close()

init_db()

def get_db_conn():
    conn = sqlite3.connect(DB_SQLITE)
    conn.row_factory = sqlite3.Row
    return conn

# --- Manejador de WebSockets ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass # Manejar conexiones muertas silenciosamente

manager = ConnectionManager()

# Función para obtener rutas de recursos (necesario para PyInstaller)
def resource_path(relative_path):
    """ Obtiene la ruta absoluta al recurso, funciona para dev y para PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- Servir Archivos Estáticos y Frontend ---
# Montamos la carpeta static para servir tailwindcss.js localmente
static_path = resource_path("static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

@app.get("/", response_class=HTMLResponse)
def servir_frontend():
    index_path = resource_path("index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/test", response_class=HTMLResponse)
def servir_test():
    test_path = resource_path("test_component.html")
    with open(test_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/publico", response_class=HTMLResponse)
def servir_publico():
    publico_path = resource_path("publico.html")
    with open(publico_path, "r", encoding="utf-8") as f:
        return f.read()

# Favicon mínimo (16x16 azul Iris #4b56ed) generado en memoria
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    favicon_path = resource_path("static/favicon.ico")
    if os.path.exists(favicon_path):
        with open(favicon_path, "rb") as f:
            return Response(content=f.read(), media_type="image/x-icon")
    # ICO minimal 1x1 pixel azul (fallback)
    ico_data = bytes([
        0,0,1,0,1,0,  # ICO header
        1,1,0,0,1,0,32,0,  # image directory
        40,0,0,0,           # size of BITMAPINFOHEADER
        40,0,0,0,           # width=40 -> actually we use 1 but offset needs calc
    ])
    # minimal valid ICO: use a pre-encoded 1x1 blue pixel ICO
    minimal_ico = bytes([
        0x00,0x00,0x01,0x00,0x01,0x00,0x10,0x10,0x00,0x00,0x01,0x00,0x20,0x00,
        0x68,0x04,0x00,0x00,0x16,0x00,0x00,0x00,0x28,0x00,0x00,0x00,0x10,0x00,
        0x00,0x00,0x20,0x00,0x00,0x00,0x01,0x00,0x20,0x00,0x00,0x00,0x00,0x00,
        0x00,0x04,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
        0x00,0x00,0x00,0x00,0x00,0x00
    ] + [0xed,0x56,0x4b,0xff]*256 + [0x00]*64)  # 16x16 Iris pixels + AND mask
    return Response(content=bytes(minimal_ico), media_type="image/x-icon")

@app.get("/turnos", response_model=List[Paciente])
def obtener_turnos():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pacientes")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Mantener conexión viva
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/turnos", response_model=Paciente)
async def crear_turno(paciente: Paciente):
    paciente.id = str(uuid.uuid4())
    paciente.hora_creacion = datetime.now().strftime("%H:%M")
    
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO pacientes (id, nombre, motivo, monto, estado, hora_creacion, hora_salida)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (paciente.id, paciente.nombre, paciente.motivo, paciente.monto, paciente.estado, paciente.hora_creacion, paciente.hora_salida))
    conn.commit()
    conn.close()
    
    await manager.broadcast({"type": "update_turnos"})
    return paciente

@app.put("/turnos/{id_paciente}/{nuevo_estado}")
async def mover_turno(id_paciente: str, nuevo_estado: str):
    hora_salida = None
    if nuevo_estado == "finalizado":
        hora_salida = datetime.now().strftime("%H:%M")
    
    conn = get_db_conn()
    cursor = conn.cursor()
    if hora_salida:
        cursor.execute("UPDATE pacientes SET estado = ?, hora_salida = ? WHERE id = ?", (nuevo_estado, hora_salida, id_paciente))
    else:
        cursor.execute("UPDATE pacientes SET estado = ? WHERE id = ?", (nuevo_estado, id_paciente))
    
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    
    conn.commit()
    # Obtener el objeto actualizado para retornar
    cursor.execute("SELECT * FROM pacientes WHERE id = ?", (id_paciente,))
    updated_p = dict(cursor.fetchone())
    conn.close()
    
    await manager.broadcast({"type": "update_turnos"})
    return updated_p

@app.delete("/turnos/{id_paciente}")
async def eliminar_turno(id_paciente: str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pacientes WHERE id = ?", (id_paciente,))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    conn.commit()
    conn.close()
    
    await manager.broadcast({"type": "update_turnos"})
    return {"mensaje": "Eliminado"}

@app.get("/stats")
def obtener_estadisticas():
    conn = get_db_conn()
    cursor = conn.cursor()
    # Ingresos totales (finalizados)
    cursor.execute("SELECT SUM(monto) FROM pacientes WHERE estado = 'finalizado'")
    total_ingresos = cursor.fetchone()[0] or 0
    # Total pacientes atendidos
    cursor.execute("SELECT COUNT(*) FROM pacientes WHERE estado = 'finalizado'")
    total_atendidos = cursor.fetchone()[0] or 0
    # Pacientes en espera
    cursor.execute("SELECT COUNT(*) FROM pacientes WHERE estado = 'espera'")
    total_espera = cursor.fetchone()[0] or 0
    conn.close()
    return {
        "ingresos": total_ingresos,
        "atendidos": total_atendidos,
        "espera": total_espera
    }

@app.get("/exportar/finalizados")
def exportar_finalizados():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pacientes WHERE estado = 'finalizado'")
    rows = cursor.fetchall()
    conn.close()

    output = io.StringIO()
    output.write('\ufeff') # BOM
    writer = csv.writer(output)
    writer.writerow(["ID", "Nombre", "Motivo", "Monto", "Hora Entrada", "Hora Salida"])
    
    for p in rows:
        writer.writerow([p['id'], p['nombre'], p['motivo'], p['monto'], p['hora_creacion'], p['hora_salida']])
            
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_finalizados.csv"})

# --- Endpoints de Configuración ---
@app.get("/config/motivos", response_model=List[Motivo])
def obtener_motivos():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM motivos")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/config/motivos", response_model=Motivo)
async def crear_motivo(motivo: Motivo):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO motivos (nombre, monto) VALUES (?, ?)", (motivo.nombre, motivo.monto))
    conn.commit()
    conn.close()
    await manager.broadcast({"type": "update_motivos"})
    return motivo

@app.delete("/config/motivos/{nombre}")
async def eliminar_motivo(nombre: str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM motivos WHERE nombre = ?", (nombre,))
    conn.commit()
    conn.close()
    await manager.broadcast({"type": "update_motivos"})
    return {"mensaje": "Eliminado"}

if __name__ == "__main__":
    # Fix para PyInstaller --windowed (evita error de isatty en logging)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    import uvicorn
    import webbrowser
    import threading
    import time

    # --- Funciones del Sistema ---
    def start_server():
        # Ejecutamos uvicorn sin reload para producción
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

    def on_open(icon, item):
        webbrowser.open("http://127.0.0.1:8000")

    def on_exit(icon, item):
        icon.stop()
        os._exit(0) # Forzar cierre de todos los hilos

    def create_icon():
        # Generar un icono simple (cuadrado azul) dinámicamente
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), color=(79, 70, 229)) # Indigo Tailwind
        dc = ImageDraw.Draw(image)
        dc.rectangle((width // 4, height // 4, width * 3 // 4, height * 3 // 4), fill="white")
        return image

    # 1. Iniciar Servidor en hilo secundario
    threading.Thread(target=start_server, daemon=True).start()

    # 2. Abrir navegador automáticamente
    threading.Thread(target=lambda: (time.sleep(1.5), on_open(None, None)), daemon=True).start()

    # 3. Iniciar Icono de Bandeja (Bloquea el hilo principal)
    menu = pystray.Menu(
        pystray.MenuItem("Abrir Tablero", on_open, default=True),
        pystray.MenuItem("Salir", on_exit)
    )
    
    icon = pystray.Icon("DingDong", create_icon(), "DingDong Turnos", menu)
    icon.run()