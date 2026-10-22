import json
import requests
import urllib.parse
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from bs4 import BeautifulSoup
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import re

# ================= SERVIDOR DE SAÚDE PARA O RENDER =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def iniciar_servidor_http():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# ================= CONFIGURAÇÕES =================
TELEGRAM_TOKEN = "8934299685:AAFnQc6hBF9Um1uhGatlzLROgI33fndAcfQ"
CHAT_ID_AUTORIZADO = "7496317236"
ARQUIVO_DADOS = "meus_produtos.json"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
fluxo_cadastro = {}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache"
})

def tratar_termo(termo):
    termo_limpo = re.sub(r'[^\w\s]', ' ', termo)
    return " ".join(termo_limpo.split())

def carregar_produtos():
    if not os.path.exists(ARQUIVO_DADOS):
        return []
    with open(ARQUIVO_DADOS, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []

def salvar_produtos(produtos):
    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
        json.dump(produtos, f, ensure_ascii=False, indent=2)

def buscar_mercadolivre(termo_original):
    termo = tratar_termo(termo_original)
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={urllib.parse.quote(termo)}&limit=5"
    try:
        resp = session.get(url, timeout=10)
        print(f"[ML LOG] Status HTTP: {resp.status_code}")
        
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                item = results[0]
                imagem = item.get("thumbnail", "").replace("-I.jpg", "-O.jpg")
                return {
                    "titulo": item["title"],
                    "preco": float(item["price"]),
                    "link": item["permalink"],
                    "imagem": imagem,
                    "loja": "Mercado Livre",
                    "erro": None
                }
            return {"erro": "A API respondeu, mas não encontrou anúncios para esta busca."}
        else:
            return {"erro": f"O servidor bloqueou a conexão (Status HTTP {resp.status_code})."}
    except Exception as e:
        print(f"[ML ERROR] {e}")
        return {"erro": f"Exceção de rede: {e}"}

def buscar_cupons_disponiveis(termo, loja):
    termo_lower = termo.lower()
    if "samsung" in termo_lower or "s25" in termo_lower or "s24" in termo_lower:
        return {
            "codigo": "GALAXY10",
            "descricao": "10% de desconto na linha Galaxy",
            "tipo": "porcentagem",
            "valor": 10,
            "condicoes": "Válido para compras acima de R$ 1.000,00"
        }
    return None

@bot.message_handler(commands=['start', 'ajuda'])
def enviar_boas_vindas(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    texto = (
        "🤖 *Assistente de Monitoramento Ativo!*\n\n"
        "📌 *Como usar:*\n"
        "• Envie o *nome do produto* no chat (ex: `S24 Ultra`, `RTX 4060`).\n"
        "• /lista - Gerencia seus produtos monitorados.\n"
        "• /ofertas - Exibe relatório com foto, preços e cupons."
    )
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['lista'])
def listar_produtos(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    produtos = carregar_produtos()
    if not produtos:
        bot.send_message(message.chat.id, "Sua lista de monitoramento está vazia.")
        return

    bot.send_message(message.chat.id, "📋 *Produtos Monitorados:*", parse_mode="Markdown")
    for idx, p in enumerate(produtos):
        markup = InlineKeyboardMarkup()
        btn_editar = InlineKeyboardButton("✏️ Alterar Preço Máx", callback_data=f"edit_{idx}")
        btn_remover = InlineKeyboardButton("❌ Remover", callback_data=f"del_{idx}")
        markup.add(btn_editar, btn_remover)

        texto = (
            f"📦 *{p['titulo']}*\n"
            f"🎯 Preço Máximo: *R$ {p['preco_maximo']:.2f}*\n"
            f"🏪 Loja: {p['loja']}"
        )
        bot.send_message(message.chat.id, texto, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['ofertas'])
def relatorio_ofertas(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    produtos = carregar_produtos()
    if not produtos:
        bot.send_message(message.chat.id, "Nenhum produto cadastrado.")
        return

    bot.send_message(message.chat.id, "🔍 *Verificando ofertas...*", parse_mode="Markdown")

    for p in produtos:
        res = buscar_mercadolivre(p['titulo'])
        if res and not res.get("erro"):
            preco_encontrado = res['preco']
            link_oferta = res['link']
            imagem = res['imagem']
        else:
            preco_encontrado = p.get('preco_atual', p.get('preco', 0))
            link_oferta = p['link']
            imagem = p.get('imagem', '')

        cupom = buscar_cupons_disponiveis(p['titulo'], p['loja'])
        texto_cupom = "Nenhum cupom ativo no momento."
        if cupom:
            desconto_str = f"{cup
