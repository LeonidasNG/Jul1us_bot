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
        self.wfile.write(b"Bot Diagnostic OK")

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

# Criar sessão persistente simulando um navegador Chrome real
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
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
        print(f"[ML LOG] Status: {resp.status_code}")
        
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
            return {"erro": "A API respondeu (200 OK), mas não encontrou anúncios para esta busca."}
        else:
            return {"erro": f"O servidor do Mercado Livre bloqueou a ligação (Status HTTP {resp.status_code})."}
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
        "Envie o nome de qualquer produto (ex: `S24 Ultra`, `RTX 4060`) para iniciar a pesquisa."
    )
    bot.reply_to(message, texto, parse_mode="Markdown")

@bot.message_handler(commands=['lista'])
def listar_produtos(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    produtos = carregar_produtos()
    if not produtos:
        bot.send_message(message.chat.id, "Sua lista está vazia.")
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
            desconto_str = f"{cupom['valor']}%" if cupom['tipo'] == 'porcentagem' else f"R$ {cupom['valor']:.2f}"
            texto_cupom = f"🎟️ *Cupom:* `{cupom['codigo']}` ({desconto_str})"

        caption = (
            f"📱 *{p['titulo']}*\n\n"
            f"💰 *Preço Atual:* R$ {preco_encontrado:.2f}\n"
            f"🎯 *Preço Alvo:* R$ {p['preco_maximo']:.2f}\n\n"
            f"{texto_cupom}\n\n"
            f"🛒 [Ver Oferta]({link_oferta})"
        )

        if imagem:
            try:
                bot.send_photo(message.chat.id, imagem, caption=caption, parse_mode="Markdown")
            except Exception:
                bot.send_message(message.chat.id, caption, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, caption, parse_mode="Markdown")

@bot.message_handler(func=lambda msg: not msg.text.startswith('/'))
def processar_busca(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    
    termo = message.text.strip()
    bot.send_message(message.chat.id, f"🔎 Pesquisando por '{termo}'...")
    
    res = buscar_mercadolivre(termo)
    
    if not res or res.get("erro"):
        msg_erro = res.get("erro") if res else "Erro desconhecido na busca."
        bot.send_message(message.chat.id, f"⚠️ *Resultado do Diagnóstico:*\n{msg_erro}", parse_mode="Markdown")
        return

    fluxo_cadastro[message.chat.id] = res

    markup = InlineKeyboardMarkup()
    btn_sim = InlineKeyboardButton("✅ Confirmar Produto", callback_data="conf_sim")
    btn_nao = InlineKeyboardButton("❌ Cancelar", callback_data="conf_nao")
    markup.add(btn_sim, btn_nao)

    caption = (
        f"Encontrei a seguinte oferta:\n\n"
        f"📦 *{res['titulo']}*\n"
        f"💵 Preço Atual: *R$ {res['preco']:.2f}*\n"
        f"🏪 Origem: {res['loja']}\n\n"
        f"Deseja cadastrar este produto na sua lista?"
    )
    
    if res['imagem']:
        try:
            bot.send_photo(message.chat.id, res['imagem'], caption=caption, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            bot.send_message(message.chat.id, caption, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, caption, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def tratar_callbacks(call):
    chat_id = call.message.chat.id
    data = call.data

    if data == "conf_sim":
        msg = bot.send_message(chat_id, "Qual o *preço máximo* que você aceita pagar? (Ex: 4500.00)", parse_mode="Markdown")
        bot.register_next_step_handler(msg, salvar_preco_maximo)
        bot.answer_callback_query(call.id)

    elif data == "conf_nao":
        fluxo_cadastro.pop(chat_id, None)
        bot.send_message(chat_id, "Cadastro cancelado.")
        bot.answer_callback_query(call.id)

    elif data.startswith("del_"):
        idx = int(data.split("_")[1])
        produtos = carregar_produtos()
        if idx < len(produtos):
            removido = produtos.pop(idx)
            salvar_produtos(produtos)
            bot.send_message(chat_id, f"🗑️ Produto *{removido['titulo']}* removido!", parse_mode="Markdown")
        bot.answer_callback_query(call.id)

    elif data.startswith("edit_"):
        idx = int(data.split("_")[1])
        msg = bot.send_message(chat_id, "Digite o novo *preço máximo* desejado:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda m: atualizar_preco_produto(m, idx))
        bot.answer_callback_query(call.id)

def salvar_preco_maximo(message):
    try:
        preco_max = float(message.text.replace("R$", "").replace(".", "").replace(",", ".").strip())
        produto = fluxo_cadastro.get(message.chat.id)
        if produto:
            produto["preco_maximo"] = preco_max
            produto["preco_atual"] = produto["preco"]
            produtos = carregar_produtos()
            produtos.append(produto)
            salvar_produtos(produtos)
            fluxo_cadastro.pop(message.chat.id, None)
            bot.send_message(message.chat.id, f"✅ *{produto['titulo']}* adicionado com valor limite de R$ {preco_max:.2f}!", parse_mode="Markdown")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Valor inválido. Digite um número (ex: 3500.00).")

def atualizar_preco_produto(message, idx):
    try:
        novo_preco = float(message.text.replace("R$", "").replace(".", "").replace(",", ".").strip())
        produtos = carregar_produtos()
        if idx < len(produtos):
            produtos[idx]["preco_maximo"] = novo_preco
            salvar_produtos(produtos)
            bot.send_message(message.chat.id, f"✏️ Preço máximo alterado para R$ {novo_preco:.2f} com sucesso!", parse_mode="Markdown")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Valor inválido. Ação cancelada.")

if __name__ == "__main__":
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    print("Bot com Diagnóstico Ativo...")
    bot.infinity_polling()
