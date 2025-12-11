import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
import pypdf
import docx
from dotenv import load_dotenv 

load_dotenv() 
app = Flask(__name__)
API_KEY = os.getenv("GEMINI_API_KEY") 

user_sessions = {} 

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

# --- CONSULTA A GEMINI (PROMPT ACTUALIZADO CON TUS ARCHIVOS) ---
def consultar_gemini(pregunta, empresa_elegida):
    try:
        client = genai.Client(api_key=API_KEY)
        
        # Prompt Ultra-Específico basado en tus manuales
        prompt = f"""
        ACTÚA COMO: Asistente experto en la aplicación móvil: {empresa_elegida}.
        
        CONTEXTO CLAVE SEGÚN MANUALES:
        
        SI ES "CCUSAFE" (Logística/Camiones):
        - Es para conductores y gestión de viajes (Acarreo/Porteo).
        - Clave maestra SMS si falla: "123456".
        - Requiere GPS "Permitir siempre".
        - Estado "Despachado" = Listo para salir. "Recepcionado" = Llegada OK.
        
        SI ES "SAFECARD" (Accesos/Oficinas):
        - Es para peatones, visitas y control remoto de barreras.
        - Wi-Fi de emergencia en portería: Red "Safecard Access Wifi Local", Clave "safecard".
        - QR dinámico cambia cada 5 segundos.
        
        [CONTENIDO DE LOS MANUALES CARGADOS]
        {TEXTO_CONOCIMIENTO}
        
        --------------------------------------------------
        CONSULTA DEL USUARIO: "{pregunta}"
        --------------------------------------------------
        
        REGLAS DE RESPUESTA:
        1. Responde EXCLUSIVAMENTE sobre {empresa_elegida}.
        2. Usa *Negritas* para botones o códigos (ej: *123456*).
        3. Sé breve y usa listas.
        4. Si preguntan por conexión a internet en portería, da los datos del Wi-Fi Local.
        5.  
        6. Si no sabes, di: "🚫 Consulta a tu supervisor o guardia."
        """
        
        response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=prompt
        )
        return response.text
    except Exception as e:
        return "⚠️ Error técnico momentáneo."

# --- RUTAS FLASK (LÓGICA DEL MENÚ) ---
@app.route('/bot', methods=['POST'])
def bot():
    incoming_msg = request.values.get('Body', '').strip()
    sender_id = request.values.get('From') 
    
    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.lower() in ['salir', 'menu', 'inicio', 'hola', 'buenas']:
        if sender_id in user_sessions: del user_sessions[sender_id]
    
    # ETAPA 1: BIENVENIDA
    if sender_id not in user_sessions:
        bienvenida = (
            "👋 *Soporte Apps CCU*\nSelecciona tu aplicación:\n\n"
            "1️⃣ *CCU SAFE* (Camiones/Logística)\n"
            "2️⃣ *SAFECARD* (Accesos/Visitas)\n"
        )
        msg.body(bienvenida)
        user_sessions[sender_id] = {"estado": "ELIGIENDO", "empresa": None}
        return str(resp)

    estado_actual = user_sessions[sender_id]["estado"]

    # ETAPA 2: MOSTRAR FAQ SEGÚN EMPRESA
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
        menu_texto = f"🔧 *Soporte {empresa}*\n\nEscribe el número de tu problema:\n\n"
        
        for key, info in faqs_empresa.items():
            menu_texto += f"*{key}*. {info['pregunta']}\n"
            
        menu_texto += "\nO escribe tu duda detallada 👇"
        msg.body(menu_texto)
        return str(resp)

    # ETAPA 3: RESPONDER (FAQ O IA)
    elif estado_actual == "CONVERSANDO":
        empresa_actual = user_sessions[sender_id]["empresa"]
        faqs_empresa = BASE_DE_FAQS.get(empresa_actual, {})
        
        # Opción A: Es un número del menú
        if incoming_msg in faqs_empresa:
            respuesta_faq = faqs_empresa[incoming_msg]["respuesta"]
            msg.body(f"💡 *Solución:*\n\n{respuesta_faq}\n\n_Escribe otra consulta o 'menu' para salir._")
        
        # Opción B: Es texto libre (Gemini)
        else:
            respuesta_ia = consultar_gemini(incoming_msg, empresa_actual)
            msg.body(respuesta_ia)
            
    return str(resp)

if __name__ == '__main__':
    app.run(port=5000, debug=True)