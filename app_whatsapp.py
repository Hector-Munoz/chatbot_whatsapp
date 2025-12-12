import os
import json
import base64
import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
import pypdf
import docx
from dotenv import load_dotenv

# Librerías de Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURACIÓN INICIAL ---
load_dotenv()
app = Flask(__name__)
API_KEY = os.getenv("GEMINI_API_KEY")

# --- CONFIGURACIÓN DE TU EXCEL ---
NOMBRE_HOJA_CALCULO = "Historial_CcuBot" # Nombre del archivo general
NOMBRE_PESTANA = "log_chat_wsp"             # <--- ¡NUEVO! Nombre exacto de la pestaña/hoja inferior

# Memoria temporal de usuarios
user_sessions = {}

# --- 1. CONEXIÓN A GOOGLE SHEETS (ESPECÍFICA) ---
def guardar_log_sheets(telefono, mensaje_usuario, respuesta_bot, empresa):
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        b64_creds = os.getenv("CREDENTIALS_B64") # Leemos del .env

        if b64_creds:
            # Decodificamos la clave en memoria
            creds_json = base64.b64decode(b64_creds).decode("utf-8")
            creds_dict = json.loads(creds_json)
            
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            
            # Abrimos el archivo
            archivo = client.open(NOMBRE_HOJA_CALCULO)
            
            # --- CAMBIO AQUÍ: Seleccionamos la pestaña por nombre ---
            try:
                sheet = archivo.worksheet(NOMBRE_PESTANA)
            except gspread.exceptions.WorksheetNotFound:
                # Si no encuentra la pestaña, avisa y usa la primera por defecto para no perder el dato
                print(f"⚠️ NO ENCONTRÉ LA PESTAÑA '{NOMBRE_PESTANA}'. Usando la primera hoja.")
                sheet = archivo.sheet1
            
            fecha = datetime.datetime.now().strftime("%Y-%m-%d")
            hora = datetime.datetime.now().strftime("%H:%M:%S")
            
            sheet.append_row([fecha, hora, telefono, empresa, mensaje_usuario, respuesta_bot])
            print(f"✅ Log guardado en pestaña '{sheet.title}' para {telefono}")
        else:
            print("⚠️ Error: No se encontró CREDENTIALS_B64 en .env")
    except Exception as e:
        print(f"❌ Error Sheets: {e}")

# --- 2. CARGA DE MANUALES Y FAQS ---
def cargar_faqs():
    if os.path.exists("faqs.json"):
        with open("faqs.json", 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

BASE_DE_FAQS = cargar_faqs()

def cargar_conocimiento():
    print("--- CARGANDO MANUALES ---")
    texto_full = ""
    directorio = os.path.join(os.path.dirname(__file__), "conocimiento_ccusafe")
    
    if not os.path.exists(directorio):
        print("⚠️ Carpeta 'conocimiento_ccusafe' no encontrada.")
        return ""

    for f in os.listdir(directorio):
        ruta = os.path.join(directorio, f)
        contenido = ""
        try:
            if f.endswith('.pdf'):
                reader = pypdf.PdfReader(ruta)
                for page in reader.pages:
                    contenido += page.extract_text() or ""
            elif f.endswith('.docx'):
                doc = docx.Document(ruta)
                for para in doc.paragraphs:
                    contenido += para.text + "\n"
            
            if contenido:
                texto_full += f"\n--- DOC: {f} ---\n{contenido}"
                print(f"   📄 Leído: {f}")
        except Exception as e:
            print(f"   ❌ Error leyendo {f}: {e}")
            
    return texto_full

TEXTO_CONOCIMIENTO = cargar_conocimiento()

# --- 3. CEREBRO IA (GEMINI) ---
def consultar_gemini(pregunta, empresa_elegida):
    try:
        client = genai.Client(api_key=API_KEY)
        prompt = f"""
        ACTÚA COMO: Asistente experto en la app: {empresa_elegida}.
        
        DATOS CLAVE MANUALES:
        - CCUSAFE: Clave SMS "123456". GPS "Siempre". Estados: Despachado/Recepcionado.
        - SAFECARD: Wi-Fi "Safecard Access Wifi Local" (clave: safecard). QR cambia cada 5s.
        
        CONTEXTO:
        {TEXTO_CONOCIMIENTO}
        
        CONSULTA: "{pregunta}"
        
        REGLAS:
        1. Responde SOLO sobre {empresa_elegida}.
        2. Sé breve, usa emojis y negritas.
        """
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text
    except Exception as e:
        return "⚠️ Error técnico en IA."

# --- 4. RUTAS DEL BOT (FLASK) ---
@app.route('/bot', methods=['POST'])
def bot():
    incoming_msg = request.values.get('Body', '').strip()
    sender_id = request.values.get('From')
    
    resp = MessagingResponse()
    msg = resp.message()
    respuesta_final = ""

    # Comando de reinicio
    if incoming_msg.lower() in ['salir', 'menu', 'inicio', 'hola']:
        if sender_id in user_sessions: del user_sessions[sender_id]

    # ETAPA 1: BIENVENIDA
    if sender_id not in user_sessions:
        respuesta_final = (
            "👋 *Soporte CCU*\nElige tu App:\n\n"
            "1️⃣ *CCU SAFE* (Camiones)\n"
            "2️⃣ *SAFECARD* (Accesos)"
        )
        msg.body(respuesta_final)
        user_sessions[sender_id] = {"estado": "ELIGIENDO", "empresa": "PENDIENTE"}
        return str(resp)

    estado = user_sessions[sender_id]["estado"]

    # ETAPA 2: SELECCIÓN Y MENÚ DE FALLAS
    if estado == "ELIGIENDO":
        if incoming_msg == "1": empresa = "CCUSAFE"
        elif incoming_msg == "2": empresa = "SAFECARD"
        else:
            msg.body("⚠️ Por favor escribe *1* o *2*.")
            return str(resp)
        
        user_sessions[sender_id]["empresa"] = empresa
        user_sessions[sender_id]["estado"] = "CONVERSANDO"
        
        # Mostrar menú desde JSON
        faqs = BASE_DE_FAQS.get(empresa, {})
        respuesta_final = f"🔧 *Menú {empresa}*\n\n"
        for k, v in faqs.items():
            respuesta_final += f"*{k}*. {v['pregunta']}\n"
        respuesta_final += "\nO escribe tu duda 👇"
        
        msg.body(respuesta_final)
        guardar_log_sheets(sender_id, incoming_msg, "Menú mostrado", empresa)
        return str(resp)

    # ETAPA 3: RESPUESTA (FAQ O IA)
    elif estado == "CONVERSANDO":
        empresa = user_sessions[sender_id]["empresa"]
        faqs = BASE_DE_FAQS.get(empresa, {})
        
        # Opción A: Es número del menú
        if incoming_msg in faqs:
            texto = faqs[incoming_msg]["respuesta"]
            respuesta_final = f"💡 *Solución:*\n{texto}\n\n_Escribe otra duda o 'menu'._"
        # Opción B: Pregunta a Gemini
        else:
            respuesta_final = consultar_gemini(incoming_msg, empresa)
            
        msg.body(respuesta_final)
        guardar_log_sheets(sender_id, incoming_msg, respuesta_final, empresa)

    return str(resp)

if __name__ == '__main__':
    app.run(port=5000, debug=True)