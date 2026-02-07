from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uuid
from datetime import datetime
import csv
import io
import json
import os
import sys
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

# "Base de datos" en memoria
db: List[Paciente] = []
motivos_db: List[Motivo] = []

# Función para obtener rutas de recursos (necesario para PyInstaller)
def resource_path(relative_path):
    """ Obtiene la ruta absoluta al recurso, funciona para dev y para PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

DB_FILE = "database.json" # La DB se guarda junto al ejecutable, no dentro

def guardar_datos():
    datos = {
        "pacientes": [p.dict() for p in db],
        "motivos": [m.dict() for m in motivos_db]
    }
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=4, ensure_ascii=False)

def cargar_datos():
    global db, motivos_db
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            datos = json.load(f)
            db = [Paciente(**p) for p in datos.get("pacientes", [])]
            motivos_db = [Motivo(**m) for m in datos.get("motivos", [])]
    
    if not motivos_db:
        motivos_db = [
            Motivo(nombre="Consulta General", monto=200),
            Motivo(nombre="Revisión", monto=100),
            Motivo(nombre="Urgencia", monto=500)
        ]
        guardar_datos()

cargar_datos()

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

@app.get("/turnos", response_model=List[Paciente])
def obtener_turnos():
    return db

@app.post("/turnos", response_model=Paciente)
def crear_turno(paciente: Paciente):
    paciente.id = str(uuid.uuid4())
    paciente.hora_creacion = datetime.now().strftime("%H:%M")
    db.append(paciente)
    guardar_datos()
    return paciente

@app.put("/turnos/{id_paciente}/{nuevo_estado}")
def mover_turno(id_paciente: str, nuevo_estado: str):
    for p in db:
        if p.id == id_paciente:
            p.estado = nuevo_estado
            if nuevo_estado == "finalizado":
                p.hora_salida = datetime.now().strftime("%H:%M")
            guardar_datos()
            return p
    raise HTTPException(status_code=404, detail="Paciente no encontrado")

@app.delete("/turnos/{id_paciente}")
def eliminar_turno(id_paciente: str):
    for i, p in enumerate(db):
        if p.id == id_paciente:
            db.pop(i)
            guardar_datos()
            return {"mensaje": "Eliminado"}
    raise HTTPException(status_code=404, detail="Paciente no encontrado")

@app.get("/exportar/finalizados")
def exportar_finalizados():
    # Crear un buffer en memoria para el archivo
    output = io.StringIO()
    output.write('\ufeff') # BOM para que Excel reconozca acentos (UTF-8)
    writer = csv.writer(output)
    
    # Escribir encabezados
    writer.writerow(["ID", "Nombre", "Motivo", "Monto", "Hora Entrada", "Hora Salida"])
    
    # Escribir datos filtrados
    for p in db:
        if p.estado == "finalizado":
            writer.writerow([p.id, p.nombre, p.motivo, p.monto, p.hora_creacion, p.hora_salida])
            
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=reporte_finalizados.csv"})

# --- Endpoints de Configuración ---
@app.get("/config/motivos", response_model=List[Motivo])
def obtener_motivos():
    return motivos_db

@app.post("/config/motivos", response_model=Motivo)
def crear_motivo(motivo: Motivo):
    motivos_db.append(motivo)
    guardar_datos()
    return motivo

@app.delete("/config/motivos/{nombre}")
def eliminar_motivo(nombre: str):
    global motivos_db
    motivos_db = [m for m in motivos_db if m.nombre != nombre]
    guardar_datos()
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