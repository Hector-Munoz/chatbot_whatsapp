import os
import json
import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
import pypdf
import docx
from dotenv import load_dotenv 

# LIBRERÍAS DE GOOGLE SHEETS
import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv() 
app = Flask(__name__)
API_KEY = os.getenv("GEMINI_API_KEY") 

user_sessions = {} 

# --- CONFIGURACIÓN GOOGLE SHEETS ---
NOMBRE_HOJA_CALCULO = "Historial_CcuBot" # <--- ¡ASEGÚRATE QUE TU HOJA SE LLAME ASÍ!

def guardar_log_sheets(telefono, mensaje_usuario, respuesta_bot, empresa):
    """Función para guardar la conversación en la nube"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        
        # Busca el archivo de credenciales en la misma carpeta
        ruta_creds = os.path.join(os.path.dirname(__file__), 'google_credentials.json')
        
        if os.path.exists(ruta_creds):
            creds = ServiceAccountCredentials.from_json_keyfile_name(ruta_creds, scope)
            client = gspread.authorize(creds)
            
            # Abre la hoja y selecciona la primera pestaña
            sheet = client.open(NOMBRE_HOJA_CALCULO).sheet1
            
            # Datos a guardar
            fecha = datetime.datetime.now().strftime("%Y-%m-%d")
            hora = datetime.datetime.now().strftime("%H:%M:%S")
            
            # Agrega la fila
            sheet.append_row([fecha, hora, telefono, empresa, mensaje_usuario, respuesta_bot])
            print(f"✅ Log guardado en Sheets para {telefono}")
        else:
            print("⚠️ No se encontró google_credentials.json - No se pudo guardar log.")
            
    except Exception as e:
        print(f"❌ Error guardando en Sheets: {e}")

# --- CARGA DE FAQS ---
def cargar_faqs():
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    ruta_json = os.path.join(ruta_base, "faqs.json")
    if os.path.exists(ruta_json):
        with open(ruta_json, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

BASE_DE_FAQS = cargar_faqs()

# --- LECTURA DE MANUALES ---
def extraer_texto_pdf(ruta):
    texto = ""
    try:
        with open(ruta, 'rb') as f:
            reader = pypdf.PdfReader(f)
            for page in reader.pages:
                t = page.extract_text()
                if t: texto += t.replace("\x00", "").replace("\x0c", "") + "\n"
    except Exception: pass
    return texto

def extraer_texto_docx(ruta):
    texto = ""
    try:
        doc = docx.Document(ruta)
        for para in doc.paragraphs: texto += para.text + "\n"
    except Exception: pass
    return texto

def cargar_conocimiento():
    print("--- CARGANDO MANUALES ---")
    ruta_base = os.path.dirname(os.path.abspath(__file__))
    directorio = os.path.join(ruta_base, "conocimiento_ccusafe")
    texto_full = ""
    if not os.path.exists(directorio): return ""

    for f in os.listdir(directorio):
        ruta = os.path.join(directorio, f)
        if f.lower().endswith('.pdf'):
            texto_full += f"\n--- DOC: {f} ---\n{extraer_texto_pdf(ruta)}"
        elif f.lower().endswith('.docx'):
            texto_full += f"\n--- DOC: {f} ---\n{extraer_texto_docx(ruta)}"
    return texto_full

TEXTO_CONOCIMIENTO = cargar_conocimiento()

# --- CONSULTA A GEMINI ---
def consultar_gemini(pregunta, empresa_elegida):
    try:
        client = genai.Client(api_key=API_KEY)
        prompt = f"""
        ACTÚA COMO: Asistente experto en la aplicación móvil: {empresa_elegida}.
        
        CONTEXTO CLAVE:
        SI ES "CCUSAFE": Clave SMS "123456". GPS "Siempre". Estado "Despachado"=Listo.
        SI ES "SAFECARD": Wi-Fi "Safecard Access Wifi Local" (clave safecard). QR cambia cada 5s.
        
        MANUALES:
        {TEXTO_CONOCIMIENTO}
        
        USUARIO: "{pregunta}"
        
        REGLAS:
        1. Responde SOLO sobre {empresa_elegida}.
        2. Sé breve, usa Negritas y Listas.
        3. Si no sabes, deriva a supervisor.
        """
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text
    except Exception as e:
        return "⚠️ Error técnico momentáneo."

# --- RUTAS FLASK ---
@app.route('/bot', methods=['POST'])
def bot():
    incoming_msg = request.values.get('Body', '').strip()
    sender_id = request.values.get('From') 
    
    resp = MessagingResponse()
    msg = resp.message()
    respuesta_final = "" # Variable para guardar lo que enviaremos (para el log)

    if incoming_msg.lower() in ['salir', 'menu', 'inicio', 'hola', 'buenas']:
        if sender_id in user_sessions: del user_sessions[sender_id]
    
    # ETAPA 1: BIENVENIDA
    if sender_id not in user_sessions:
        respuesta_final = (
            "👋 *Soporte Apps CCU*\nSelecciona tu aplicación:\n\n"
            "1️⃣ *CCU SAFE* (Camiones)\n"
            "2️⃣ *SAFECARD* (Accesos)\n"
        )
        msg.body(respuesta_final)
        user_sessions[sender_id] = {"estado": "ELIGIENDO", "empresa": "PENDIENTE"}
        return str(resp)

    estado_actual = user_sessions[sender_id]["estado"]

    # ETAPA 2: MOSTRAR FAQ
    if estado_actual == "ELIGIENDO":
        empresa = ""
        if incoming_msg == "1": empresa = "CCUSAFE"
        elif incoming_msg == "2": empresa = "SAFECARD"
        else:
            msg.body("⚠️ Escribe *1* o *2*.")
            return str(resp)
        
        user_sessions[sender_id]["empresa"] = empresa
        user_sessions[sender_id]["estado"] = "CONVERSANDO"
        
        faqs_empresa = BASE_DE_FAQS.get(empresa, {})
        respuesta_final = f"🔧 *Soporte {empresa}*\n\nEscribe el número de tu problema:\n\n"
        for key, info in faqs_empresa.items():
            respuesta_final += f"*{key}*. {info['pregunta']}\n"
        respuesta_final += "\nO escribe tu duda detallada 👇"
        
        msg.body(respuesta_final)
        
        # Guardamos log de selección de menú
        guardar_log_sheets(sender_id, incoming_msg, "Menú desplegado", empresa)
        return str(resp)

    # ETAPA 3: RESPONDER
    elif estado_actual == "CONVERSANDO":
        empresa_actual = user_sessions[sender_id]["empresa"]
        faqs_empresa = BASE_DE_FAQS.get(empresa_actual, {})
        
        if incoming_msg in faqs_empresa:
            # Respuesta rápida (FAQ)
            texto_faq = faqs_empresa[incoming_msg]["respuesta"]
            respuesta_final = f"💡 *Solución:*\n\n{texto_faq}\n\n_Escribe otra consulta o 'menu'._"
        else:
            # Respuesta Inteligente (Gemini)
            respuesta_final = consultar_gemini(incoming_msg, empresa_actual)
            
        msg.body(respuesta_final)
        
        # --- AQUÍ GUARDAMOS EN SHEETS ---
        guardar_log_sheets(sender_id, incoming_msg, respuesta_final, empresa_actual)
            
    return str(resp)

if __name__ == '__main__':
    app.run(port=5000, debug=True)