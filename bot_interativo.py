import json
import requests
import urllib.parse
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ================= SERVIDOR DE SAÚDE PARA O RENDER =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

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

def buscar_produto_ml(termo):
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={urllib.parse.quote(termo)}&limit=1"
    try:
        resp = requests.get(url).json()
        results = resp.get("results", [])
        if results:
            item = results[0]
            imagem = item.get("thumbnail", "").replace("-I.jpg", "-O.jpg")
            return {
                "id": item["id"],
                "titulo": item["title"],
                "preco_atual": float(item["price"]),
                "link": item["permalink"],
                "imagem": imagem,
                "loja": "Mercado Livre"
            }
    except Exception as e:
        print(f"Erro ao buscar produto: {e}")
    return None

def buscar_cupons_disponiveis(termo, loja):
    if "samsung" in termo.lower():
        return {
            "codigo": "SAMSUNG10",
            "descricao": "10% de desconto em linha Galaxy",
            "tipo": "porcentagem",
            "valor": 10,
            "condicoes": "Valor mínimo de R$ 1.500,00"
        }
    return None

@bot.message_handler(commands=['start', 'ajuda'])
def enviar_boas_vindas(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    texto = (
        "🤖 *Bem-vindo ao seu Assistente de Compras Interativo!*\n\n"
        "📌 *Como usar:*\n"
        "• Envie o *nome ou link* de um produto para pesquisar e cadastrar.\n"
        "• /lista - Gerencia seus produtos (editar preço máximo ou remover).\n"
        "• /ofertas - Exibe o relatório atualizado com fotos, preços e cupons."
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

    bot.send_message(message.chat.id, "📋 *Seus Produtos Monitorados:*", parse_mode="Markdown")
    for idx, p in enumerate(produtos):
        markup = InlineKeyboardMarkup()
        btn_editar = InlineKeyboardButton("✏️ Alterar Preço", callback_data=f"edit_{idx}")
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
        bot.send_message(message.chat.id, "Nenhum produto cadastrado para verificar.")
        return

    bot.send_message(message.chat.id, "🔍 *Buscando ofertas atualizadas...*", parse_mode="Markdown")

    for p in produtos:
        dados_atualizados = buscar_produto_ml(p['titulo'])
        preco_encontrado = dados_atualizados['preco_atual'] if dados_atualizados else p['preco_atual']
        link_oferta = dados_atualizados['link'] if dados_atualizados else p['link']
        
        cupom = buscar_cupons_disponiveis(p['titulo'], p['loja'])
        texto_cupom = "Nenhum cupom ativo no momento."
        
        if cupom:
            desconto_str = f"{cupom['valor']}%" if cupom['tipo'] == 'porcentagem' else f"R$ {cupom['valor']:.2f}"
            texto_cupom = (
                f"🎟️ *Cupom:* `{cupom['codigo']}`\n"
                f"📝 *Descrição:* {cupom['descricao']} ({desconto_str})\n"
                f"📌 *Condição:* {cupom['condicoes']}"
            )

        caption = (
            f"📱 *{p['titulo']}*\n\n"
            f"💰 *Menor preço encontrado:* R$ {preco_encontrado:.2f}\n"
            f"🎯 *Seu limite:* R$ {p['preco_maximo']:.2f}\n"
            f"🏪 *Loja:* {p['loja']}\n\n"
            f"{texto_cupom}\n\n"
            f"🛒 [Clique aqui para comprar]({link_oferta})"
        )

        if p.get('imagem'):
            bot.send_photo(message.chat.id, p['imagem'], caption=caption, parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, caption, parse_mode="Markdown")

@bot.message_handler(func=lambda msg: not msg.text.startswith('/'))
def processar_busca(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    
    termo = message.text.strip()
    bot.send_message(message.chat.id, f"🔎 Pesquisando por '{termo}'...")
    
    produto = buscar_produto_ml(termo)
    if not produto:
        bot.send_message(message.chat.id, "❌ Nenhum produto encontrado com esse nome.")
        return

    fluxo_cadastro[message.chat.id] = produto

    markup = InlineKeyboardMarkup()
    btn_sim = InlineKeyboardButton("✅ Confirmar Produto", callback_data="conf_sim")
    btn_nao = InlineKeyboardButton("❌ Cancelar", callback_data="conf_nao")
    markup.add(btn_sim, btn_nao)

    caption = (
        f"Encontrei este produto:\n\n"
        f"📦 *{produto['titulo']}*\n"
        f"💵 Preço Atual: *R$ {produto['preco_atual']:.2f}*\n\n"
        f"É este produto que você quer monitorar?"
    )
    
    if produto['imagem']:
        bot.send_photo(message.chat.id, produto['imagem'], caption=caption, reply_markup=markup, parse_mode="Markdown")
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
            produtos = carregar_produtos()
            produtos.append(produto)
            salvar_produtos(produtos)
            fluxo_cadastro.pop(message.chat.id, None)
            bot.send_message(message.chat.id, f"✅ *{produto['titulo']}* adicionado com o valor máximo de R$ {preco_max:.2f}!", parse_mode="Markdown")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Valor inválido. Digite um número válido (ex: 3500.00).")

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
    # Inicia o servidor HTTP em background para o Render não falhar
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    print("Bot Interativo rodando...")
    bot.infinity_polling()
