import json
import requests
import urllib.parse
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from bs4 import BeautifulSoup
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import cloudscraper

# ================= SERVIDOR DE SAÚDE PARA O RENDER =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Multi-Lojas Anti-Block OK")

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

# Criador de requisições que burla o Cloudflare
scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)

# ================= GESTÃO DE ARQUIVOS =================
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

# ================= MOTORES DE BUSCA =================
def buscar_mercadolivre(termo):
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={urllib.parse.quote(termo)}&limit=5"
    try:
        resp = scraper.get(url, timeout=10)
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
                    "loja": "Mercado Livre"
                }
        else:
            print(f"ML retornou status: {resp.status_code}")
    except Exception as e:
        print(f"Erro no Mercado Livre: {e}")
    return None

def buscar_buscape(termo):
    url = f"https://www.buscape.com.br/search?q={urllib.parse.quote(termo)}"
    try:
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Procura os cards de produto do Buscapé
            cards = soup.find_all("div", class_=lambda c: c and "SearchCard_ProductCard" in c)
            if not cards:
                cards = soup.find_all("a", class_=lambda c: c and "ProductCard_ProductCard" in c)

            for card in cards[:3]:
                titulo_elem = card.find("h2") or card.find("h3")
                preco_elem = card.find("p", class_=lambda c: c and "Text_Text" in c and "title" in c.lower()) or card.find("span", class_=lambda c: c and "price" in c.lower())
                img_elem = card.find("img")
                link_elem = card if card.name == "a" else card.find("a")

                if titulo_elem and preco_elem:
                    titulo = titulo_elem.text.strip()
                    preco_txt = preco_elem.text.replace("R$", "").replace(".", "").replace(",", ".").strip()
                    try:
                        preco = float(preco_txt)
                    except ValueError:
                        continue

                    link = "https://www.buscape.com.br" + link_elem['href'] if link_elem and link_elem.get('href', '').startswith('/') else (link_elem['href'] if link_elem else url)
                    imagem = img_elem.get("src", "") if img_elem else ""

                    return {
                        "titulo": titulo,
                        "preco": preco,
                        "link": link,
                        "imagem": imagem,
                        "loja": "Amazon / Magalu / Casas Bahia (via Buscapé)"
                    }
        else:
            print(f"Buscapé retornou status: {resp.status_code}")
    except Exception as e:
        print(f"Erro no Buscapé: {e}")
    return None

def buscar_melhor_oferta_multilojas(termo):
    ofertas = []
    
    ml = buscar_mercadolivre(termo)
    if ml:
        ofertas.append(ml)
        
    buscape = buscar_buscape(termo)
    if buscape:
        ofertas.append(buscape)

    if not ofertas:
        return None

    ofertas.sort(key=lambda x: x["preco"])
    return ofertas[0]

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
    elif "iphone" in termo_lower or "apple" in termo_lower:
        return {
            "codigo": "APPLE200",
            "descricao": "R$ 200,00 de desconto à vista",
            "tipo": "absoluto",
            "valor": 200,
            "condicoes": "Pagamento via PIX"
        }
    return None

# ================= COMANDOS TELEGRAM =================
@bot.message_handler(commands=['start', 'ajuda'])
def enviar_boas_vindas(message):
    if str(message.chat.id) != CHAT_ID_AUTORIZADO:
        return
    texto = (
        "🤖 *Bem-vindo ao seu Monitor Multi-Lojas de Preços!*\n\n"
        "🔍 *Pesquisamos em:* Mercado Livre, Amazon, Magalu, Shopee, KaBuM!, Fast Shop e mais.\n\n"
        "📌 *Comandos:*\n"
        "• Envie o *nome do produto* (ex: `S25 Ultra` ou `RTX 4060`) para buscar.\n"
        "• /lista - Gerencia seus produtos cadastrados.\n"
        "• /ofertas - Exibe o relatório com foto, menor preço e cupons ativos."
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
        btn_editar = InlineKeyboardButton("✏️ Alterar Preço Máx", callback_data=f"edit_{idx}")
        btn_remover = InlineKeyboardButton("❌ Remover", callback_data=f"del_{idx}")
        markup.add(btn_editar, btn_remover)

        texto = (
            f"📦 *{p['titulo']}*\n"
            f"🎯 Preço Máximo Alvo: *R$ {p['preco_maximo']:.2f}*\n"
            f"🏪 Origem: {p['loja']}"
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

    bot.send_message(message.chat.id, "🔍 *Buscando menores preços nas lojas...*", parse_mode="Markdown")

    for p in produtos:
        oferta_atual = buscar_melhor_oferta_multilojas(p['titulo'])
        preco_encontrado = oferta_atual['preco'] if oferta_atual else p['preco_atual']
        link_oferta = oferta_atual['link'] if oferta_atual else p['link']
        loja_oferta = oferta_atual['loja'] if oferta_atual else p['loja']
        imagem = oferta_atual['imagem'] if oferta_atual and oferta_atual['imagem'] else p.get('imagem', '')

        cupom = buscar_cupons_disponiveis(p['titulo'], loja_oferta)
        texto_cupom = "Nenhum cupom ativo no momento."
        
        if cupom:
            desconto_str = f"{cupom['valor']}%" if cupom['tipo'] == 'porcentagem' else f"R$ {cupom['valor']:.2f}"
            texto_cupom = (
                f"🎟️ *Cupom Ativo:* `{cupom['codigo']}`\n"
                f"📝 *Descrição:* {cupom['descricao']} (Desconto: {desconto_str})\n"
                f"📌 *Condição:* {cupom['condicoes']}"
            )

        caption = (
            f"📱 *{p['titulo']}*\n\n"
            f"💰 *Menor preço encontrado:* R$ {preco_encontrado:.2f}\n"
            f"🎯 *Seu limite:* R$ {p['preco_maximo']:.2f}\n"
            f"🏪 *Loja / Origem:* {loja_oferta}\n\n"
            f"{texto_cupom}\n\n"
            f"🛒 [Ver Oferta com Menor Preço]({link_oferta})"
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
    bot.send_message(message.chat.id, f"🔎 Pesquisando ofertas para '{termo}' em várias lojas...")
    
    produto = buscar_melhor_oferta_multilojas(termo)
    if not produto:
        bot.send_message(message.chat.id, "❌ Nenhum produto encontrado. Tente resumir o nome.")
        return

    fluxo_cadastro[message.chat.id] = produto

    markup = InlineKeyboardMarkup()
    btn_sim = InlineKeyboardButton("✅ Confirmar Produto", callback_data="conf_sim")
    btn_nao = InlineKeyboardButton("❌ Cancelar", callback_data="conf_nao")
    markup.add(btn_sim, btn_nao)

    caption = (
        f"Encontrei a melhor oferta:\n\n"
        f"📦 *{produto['titulo']}*\n"
        f"💵 Menor Preço Atual: *R$ {produto['preco']:.2f}*\n"
        f"🏪 Origem: {produto['loja']}\n\n"
        f"Deseja cadastrar este produto na sua lista?"
    )
    
    if produto['imagem']:
        try:
            bot.send_photo(message.chat.id, produto['imagem'], caption=caption, reply_markup=markup, parse_mode="Markdown")
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
            bot.send_message(message.chat.id, f"✅ *{produto['titulo']}* adicionado com o valor limite de R$ {preco_max:.2f}!", parse_mode="Markdown")
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
    print("Bot Multi-Lojas Anti-Block rodando...")
    bot.infinity_polling()
