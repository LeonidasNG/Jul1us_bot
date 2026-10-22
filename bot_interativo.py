# -*- coding: utf-8 -*-
"""
Jul1us_bot - Monitor de preços do Mercado Livre no Telegram.

Variáveis de ambiente (configure no Render > Environment):
  TELEGRAM_TOKEN  (obrigatória) token do bot gerado no @BotFather
  CHAT_ID         (opcional)    chat autorizado; padrão = 7496317236
  GITHUB_TOKEN    (recomendada) token GitHub com escopo "gist"
  GIST_ID         (recomendada) ID do Gist secreto que guarda os produtos
  INTERVALO_MIN   (opcional)    minutos entre verificações automáticas (padrão 30)
"""
import html
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import telebot
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

# ======================= CONFIGURAÇÕES =======================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID_AUTORIZADO = os.environ.get("CHAT_ID", "7496317236")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GIST_ID = os.environ.get("GIST_ID")
INTERVALO_MIN = int(os.environ.get("INTERVALO_MIN", "30"))
ARQUIVO_DADOS = "meus_produtos.json"

# Cupons editáveis manualmente. Confira se o código realmente existe antes de manter.
CUPONS = [
    {
        "palavras": ["samsung", "galaxy", "s24", "s25"],
        "codigo": "GALAXY10",
        "descricao": "10% de desconto na linha Galaxy",
        "tipo": "porcentagem",
        "valor": 10,
    },
]

bot = telebot.TeleBot(TELEGRAM_TOKEN)
fluxo_cadastro = {}

# Sessão que imita um navegador Chrome real (contorna bloqueio de datacenter)
session = curl_requests.Session(impersonate="chrome120")

_lock = threading.RLock()
_cache = None
_gist_ok = False


# ================= SERVIDOR DE SAÚDE PARA O RENDER =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # evita poluir os logs do Render


def iniciar_servidor_http():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# ================= UTILITÁRIOS =================
def esc(texto):
    return html.escape(str(texto), quote=True)


def brl(valor):
    s = f"{valor:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def parse_preco(txt):
    """Aceita: 4500 | 4500.00 | 4500,00 | 4.500,00 | R$ 4.500 | 4.500"""
    t = (txt or "").upper().replace("R$", "").replace(" ", "").strip()
    if not t:
        raise ValueError("vazio")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    valor = float(t)
    if valor <= 0:
        raise ValueError("valor inválido")
    return valor


def tratar_termo(termo):
    termo_limpo = re.sub(r"[^\w\s]", " ", termo)
    return " ".join(termo_limpo.split())


def autorizado(chat_id):
    return str(chat_id) == str(CHAT_ID_AUTORIZADO)


# ================= ARMAZENAMENTO (Gist + arquivo local) =================
def usa_gist():
    return bool(GITHUB_TOKEN and GIST_ID)


def _headers_gist():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "jul1us-bot",
    }


def _ler_gist():
    r = requests.get(
        f"https://api.github.com/gists/{GIST_ID}", headers=_headers_gist(), timeout=15
    )
    r.raise_for_status()
    arq = r.json().get("files", {}).get(ARQUIVO_DADOS)
    if not arq:
        return []
    conteudo = arq.get("content")
    if arq.get("truncated") and arq.get("raw_url"):
        conteudo = requests.get(arq["raw_url"], headers=_headers_gist(), timeout=15).text
    return json.loads(conteudo or "[]")


def _escrever_gist(produtos):
    payload = {
        "files": {
            ARQUIVO_DADOS: {"content": json.dumps(produtos, ensure_ascii=False, indent=2)}
        }
    }
    r = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=_headers_gist(),
        json=payload,
        timeout=20,
    )
    r.raise_for_status()


def _ler_armazenamento():
    global _gist_ok
    produtos = None
    if usa_gist():
        try:
            produtos = _ler_gist()
            _gist_ok = True
            print("[STORAGE] Dados carregados do Gist.")
        except Exception as e:
            print(f"[STORAGE] Falha ao ler o Gist: {e}")
    if produtos is None and os.path.exists(ARQUIVO_DADOS):
        try:
            with open(ARQUIVO_DADOS, "r", encoding="utf-8") as f:
                produtos = json.load(f)
            print("[STORAGE] Dados carregados do arquivo local.")
        except Exception:
            produtos = None
    if not isinstance(produtos, list):
        produtos = []
    for p in produtos:
        p.setdefault("id", uuid.uuid4().hex[:8])
    return produtos


def carregar_produtos():
    global _cache
    with _lock:
        if _cache is None:
            _cache = _ler_armazenamento()
        return _cache


def salvar_produtos(produtos):
    global _cache
    with _lock:
        _cache = produtos
        try:
            with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
                json.dump(produtos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[STORAGE] Falha ao salvar arquivo local: {e}")
        if usa_gist():
            if not _gist_ok:
                print("[STORAGE] Gist não foi lido com sucesso; não vou sobrescrevê-lo.")
                return
            try:
                _escrever_gist(produtos)
            except Exception as e:
                print(f"[STORAGE] Falha ao salvar no Gist: {e}")


def achar_produto(produtos, pid):
    for p in produtos:
        if p.get("id") == pid:
            return p
    return None


# ================= BUSCA NO MERCADO LIVRE =================
def _novo_item(titulo, preco, link, imagem):
    return {
        "titulo": titulo,
        "preco": float(preco),
        "link": link,
        "imagem": imagem or "",
        "loja": "Mercado Livre",
    }


def candidatos_api(termo):
    url = (
        "https://api.mercadolibre.com/sites/MLB/search?q="
        f"{urllib.parse.quote(termo)}&limit=10"
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    resp = session.get(url, headers=headers, timeout=12)
    print(f"[ML API] HTTP {resp.status_code}")
    if resp.status_code != 200:
        return []
    lista = []
    for item in resp.json().get("results", []):
        if not item.get("price") or not item.get("permalink"):
            continue
        img = (item.get("thumbnail") or "").replace("-I.jpg", "-O.jpg")
        img = img.replace("http://", "https://")
        lista.append(_novo_item(item["title"], item["price"], item["permalink"], img))
    return lista


def candidatos_web(termo):
    url = f"https://lista.mercadolivre.com.br/{urllib.parse.quote(termo)}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.mercadolivre.com.br/",
    }
    resp = session.get(url, headers=headers, timeout=15)
    print(f"[ML WEB] HTTP {resp.status_code}")
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items = (
        soup.select(".poly-card")
        or soup.select(".ui-search-layout__item")
        or soup.select(".ui-search-result__content")
    )
    lista = []
    for item in items[:12]:
        el_titulo = (
            item.select_one(".poly-component__title")
            or item.select_one(".ui-search-item__title")
            or item.select_one("h2")
        )
        if not el_titulo:
            continue
        titulo = el_titulo.get_text().strip()

        el_link = (
            item.select_one("a.poly-component__title")
            or item.select_one("a.ui-search-link")
            or item.select_one("a")
        )
        link = el_link["href"] if el_link and "href" in el_link.attrs else None

        el_preco = item.select_one(
            ".poly-price__current .andes-money-amount__fraction"
        ) or item.select_one(".andes-money-amount__fraction")
        if not el_preco:
            continue
        preco_str = el_preco.get_text().replace(".", "").strip()
        el_cent = item.select_one(".andes-money-amount__cents")
        if el_cent:
            preco_str += "." + el_cent.get_text().strip()
        try:
            preco = float(preco_str)
        except ValueError:
            continue

        el_img = (
            item.select_one("img.poly-component__picture")
            or item.select_one("img.ui-search-result-image__element")
            or item.select_one("img")
        )
        imagem = ""
        if el_img:
            imagem = el_img.get("data-src") or el_img.get("src") or ""
            if imagem.startswith("data:"):
                imagem = ""

        if titulo and link:
            lista.append(_novo_item(titulo, preco, link, imagem))
    return lista


def buscar_candidatos(termo_original):
    """Tenta a API; se falhar, cai para o scraping da página web."""
    termo = tratar_termo(termo_original)
    erro = "Nenhum produto encontrado para essa busca."
    for nome, funcao in (("API", candidatos_api), ("WEB", candidatos_web)):
        try:
            lista = funcao(termo)
            if lista:
                return lista, None
        except Exception as e:
            print(f"[ML {nome} ERRO] {e}")
            erro = f"Falha na busca ({nome}): {e}"
    return [], erro


def melhor_oferta(termo, preco_ref=None):
    """Menor preço entre os resultados que contêm todas as palavras do termo."""
    candidatos, erro = buscar_candidatos(termo)
    if not candidatos:
        return None, erro
    palavras = tratar_termo(termo).lower().split()
    filtrados = [
        c for c in candidatos if all(p in c["titulo"].lower() for p in palavras)
    ] or candidatos
    if preco_ref:
        # ignora itens muito baratos (capinhas, películas, peças)
        sem_acessorios = [c for c in filtrados if c["preco"] >= preco_ref * 0.4]
        filtrados = sem_acessorios or filtrados
    return min(filtrados, key=lambda c: c["preco"]), None


def buscar_cupom(termo):
    termo_lower = termo.lower()
    for c in CUPONS:
        if any(p in termo_lower for p in c["palavras"]):
            return c
    return None


def texto_cupom(termo):
    cupom = buscar_cupom(termo)
    if not cupom:
        return "Nenhum cupom ativo no momento."
    if cupom["tipo"] == "porcentagem":
        desc = f"{cupom['valor']}%"
    else:
        desc = brl(cupom["valor"])
    return f"🎟️ <b>Cupom:</b> <code>{esc(cupom['codigo'])}</code> ({desc})"


# ================= ENVIO DE MENSAGENS =================
def enviar(chat_id, texto, imagem=None, markup=None):
    if imagem:
        try:
            return bot.send_photo(
                chat_id, imagem, caption=texto, parse_mode="HTML", reply_markup=markup
            )
        except Exception as e:
            print(f"[ENVIO] Falha ao enviar foto, enviando só texto: {e}")
    return bot.send_message(chat_id, texto, parse_mode="HTML", reply_markup=markup)


# ================= COMANDOS =================
@bot.message_handler(commands=["start", "ajuda"])
def enviar_boas_vindas(message):
    if not autorizado(message.chat.id):
        return
    texto = (
        "🤖 <b>Assistente de Monitoramento Ativo!</b>\n\n"
        "📌 <b>Como usar:</b>\n"
        "• Envie o <b>nome do produto</b> no chat (ex: <code>S24 Ultra</code>, <code>RTX 4060</code>).\n"
        "• /lista - Gerencia seus produtos monitorados.\n"
        "• /ofertas - Relatório com foto, preços e cupons.\n\n"
        f"🔔 Verifico os preços automaticamente a cada {INTERVALO_MIN} min e aviso "
        "quando algum produto atingir o seu preço máximo."
    )
    bot.reply_to(message, texto, parse_mode="HTML")


@bot.message_handler(commands=["lista"])
def listar_produtos(message):
    if not autorizado(message.chat.id):
        return
    produtos = carregar_produtos()
    if not produtos:
        bot.send_message(message.chat.id, "Sua lista de monitoramento está vazia.")
        return

    bot.send_message(
        message.chat.id, "📋 <b>Produtos Monitorados:</b>", parse_mode="HTML"
    )
    for p in list(produtos):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✏️ Alterar Preço Máx", callback_data=f"edit_{p['id']}"),
            InlineKeyboardButton("❌ Remover", callback_data=f"del_{p['id']}"),
        )
        atual = p.get("preco_atual", p.get("preco"))
        linha_atual = f"💵 Preço atual: <b>{brl(atual)}</b>\n" if atual else ""
        texto = (
            f"📦 <b>{esc(p['titulo'])}</b>\n"
            f"{linha_atual}"
            f"🎯 Preço Máximo: <b>{brl(p['preco_maximo'])}</b>\n"
            f"🏪 Loja: {esc(p.get('loja', 'Mercado Livre'))}"
        )
        bot.send_message(
            message.chat.id, texto, reply_markup=markup, parse_mode="HTML"
        )


@bot.message_handler(commands=["ofertas"])
def relatorio_ofertas(message):
    if not autorizado(message.chat.id):
        return
    produtos = [dict(p) for p in carregar_produtos()]
    if not produtos:
        bot.send_message(message.chat.id, "Nenhum produto cadastrado.")
        return

    bot.send_message(
        message.chat.id, "🔍 <b>Verificando ofertas...</b>", parse_mode="HTML"
    )

    for p in produtos:
        termo = p.get("termo") or p["titulo"]
        res, _ = melhor_oferta(termo, p.get("preco_ref") or p.get("preco_atual"))
        if res:
            preco = res["preco"]
            link = res["link"]
            imagem = res["imagem"] or p.get("imagem", "")
        else:
            preco = p.get("preco_atual", p.get("preco", 0))
            link = p.get("link_atual") or p.get("link", "")
            imagem = p.get("imagem", "")

        status = "✅ <b>Abaixo do seu preço máximo!</b>\n\n" if preco <= p["preco_maximo"] else ""
        caption = (
            f"📱 <b>{esc(p['titulo'])}</b>\n\n"
            f"{status}"
            f"💰 <b>Preço Atual:</b> {brl(preco)}\n"
            f"🎯 <b>Preço Alvo:</b> {brl(p['preco_maximo'])}\n\n"
            f"{texto_cupom(p['titulo'])}\n\n"
            f'🛒 <a href="{esc(link)}">Ver Oferta</a>'
        )
        enviar(message.chat.id, caption, imagem)


# ================= CADASTRO DE PRODUTOS =================
@bot.message_handler(
    content_types=["text"], func=lambda m: bool(m.text) and not m.text.startswith("/")
)
def processar_busca(message):
    if not autorizado(message.chat.id):
        return

    termo = message.text.strip()
    bot.send_message(message.chat.id, f"🔎 Pesquisando por '{termo}'...")

    candidatos, erro = buscar_candidatos(termo)
    if not candidatos:
        bot.send_message(
            message.chat.id,
            f"⚠️ <b>Resultado da Pesquisa:</b>\n{esc(erro)}",
            parse_mode="HTML",
        )
        return

    res = dict(candidatos[0])
    res["termo"] = termo
    fluxo_cadastro[message.chat.id] = res

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Confirmar Produto", callback_data="conf_sim"),
        InlineKeyboardButton("❌ Cancelar", callback_data="conf_nao"),
    )
    caption = (
        "Encontrei a seguinte oferta:\n\n"
        f"📦 <b>{esc(res['titulo'])}</b>\n"
        f"💵 Preço Atual: <b>{brl(res['preco'])}</b>\n"
        f"🏪 Origem: {esc(res['loja'])}\n\n"
        "Deseja cadastrar este produto na sua lista?"
    )
    enviar(message.chat.id, caption, res["imagem"], markup)


@bot.callback_query_handler(func=lambda call: True)
def tratar_callbacks(call):
    chat_id = call.message.chat.id
    if not autorizado(chat_id):
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    data = call.data or ""

    if data == "conf_sim":
        if chat_id not in fluxo_cadastro:
            bot.send_message(chat_id, "⚠️ Essa busca expirou. Envie o nome do produto novamente.")
            return
        msg = bot.send_message(
            chat_id,
            "Qual o <b>preço máximo</b> que você aceita pagar? (Ex: 4500 ou 4.500,00)",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, salvar_preco_maximo)

    elif data == "conf_nao":
        fluxo_cadastro.pop(chat_id, None)
        bot.send_message(chat_id, "Cadastro cancelado.")

    elif data.startswith("del_"):
        pid = data[4:]
        with _lock:
            produtos = carregar_produtos()
            produto = achar_produto(produtos, pid)
            if produto:
                produtos.remove(produto)
                salvar_produtos(produtos)
        if produto:
            bot.send_message(
                chat_id,
                f"🗑️ Produto <b>{esc(produto['titulo'])}</b> removido!",
                parse_mode="HTML",
            )
        else:
            bot.send_message(chat_id, "Produto não encontrado (talvez já tenha sido removido).")

    elif data.startswith("edit_"):
        pid = data[5:]
        msg = bot.send_message(
            chat_id,
            "Digite o novo <b>preço máximo</b> desejado (Ex: 4500 ou 4.500,00):",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, atualizar_preco_produto, pid)


def salvar_preco_maximo(message):
    if not message.text or message.text.startswith("/"):
        fluxo_cadastro.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "Cadastro cancelado.")
        return
    try:
        preco_max = parse_preco(message.text)
    except ValueError:
        # mantém o fluxo e pede de novo
        msg = bot.send_message(
            message.chat.id, "❌ Valor inválido. Digite um número (ex: 3500 ou 3.500,00):"
        )
        bot.register_next_step_handler(msg, salvar_preco_maximo)
        return

    produto = fluxo_cadastro.pop(message.chat.id, None)
    if not produto:
        bot.send_message(message.chat.id, "⚠️ Essa busca expirou. Envie o nome do produto novamente.")
        return

    registro = {
        "id": uuid.uuid4().hex[:8],
        "titulo": produto["titulo"],
        "termo": produto.get("termo", produto["titulo"]),
        "preco_maximo": preco_max,
        "preco_atual": produto["preco"],
        "preco_ref": produto["preco"],
        "link": produto["link"],
        "link_atual": produto["link"],
        "imagem": produto.get("imagem", ""),
        "loja": produto.get("loja", "Mercado Livre"),
        "ultimo_alerta": None,
    }
    with _lock:
        produtos = carregar_produtos()
        produtos.append(registro)
        salvar_produtos(produtos)

    bot.send_message(
        message.chat.id,
        f"✅ <b>{esc(registro['titulo'])}</b> adicionado com valor limite de {brl(preco_max)}!",
        parse_mode="HTML",
    )


def atualizar_preco_produto(message, pid):
    if not message.text or message.text.startswith("/"):
        bot.send_message(message.chat.id, "Ação cancelada.")
        return
    try:
        novo_preco = parse_preco(message.text)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Valor inválido. Ação cancelada.")
        return

    with _lock:
        produtos = carregar_produtos()
        produto = achar_produto(produtos, pid)
        if produto:
            produto["preco_maximo"] = novo_preco
            produto["ultimo_alerta"] = None
            salvar_produtos(produtos)
    if produto:
        bot.send_message(
            message.chat.id,
            f"✏️ Preço máximo alterado para {brl(novo_preco)} com sucesso!",
            parse_mode="HTML",
        )
    else:
        bot.send_message(message.chat.id, "Produto não encontrado.")


# ================= MONITORAMENTO AUTOMÁTICO =================
def verificar_todos():
    snapshot = [dict(p) for p in carregar_produtos()]
    mudou = False

    for p in snapshot:
        termo = p.get("termo") or p["titulo"]
        res, _ = melhor_oferta(termo, p.get("preco_ref") or p.get("preco_atual"))
        if not res:
            time.sleep(3)
            continue

        preco = res["preco"]
        enviar_alerta = False
        with _lock:
            alvo = achar_produto(carregar_produtos(), p["id"])
            if not alvo:
                continue
            if alvo.get("preco_atual") != preco:
                alvo["preco_atual"] = preco
                mudou = True
            if alvo.get("link_atual") != res["link"]:
                alvo["link_atual"] = res["link"]
                mudou = True

            ultimo = alvo.get("ultimo_alerta")
            if preco <= alvo["preco_maximo"]:
                # avisa na primeira vez e sempre que o preço cair ainda mais
                if ultimo is None or preco < ultimo:
                    alvo["ultimo_alerta"] = preco
                    enviar_alerta = True
                    mudou = True
            elif ultimo is not None:
                alvo["ultimo_alerta"] = None
                mudou = True

        if enviar_alerta:
            texto = (
                "🚨 <b>ALERTA DE PREÇO!</b>\n\n"
                f"📦 <b>{esc(p['titulo'])}</b>\n"
                f"💰 <b>Agora:</b> {brl(preco)}\n"
                f"🎯 <b>Seu limite:</b> {brl(p['preco_maximo'])}\n\n"
                f"{texto_cupom(p['titulo'])}\n\n"
                f'🛒 <a href="{esc(res["link"])}">Ver Oferta</a>'
            )
            try:
                enviar(CHAT_ID_AUTORIZADO, texto, res["imagem"] or p.get("imagem", ""))
            except Exception as e:
                print(f"[MONITOR] Falha ao enviar alerta: {e}")
        time.sleep(3)

    if mudou:
        with _lock:
            salvar_produtos(carregar_produtos())


def loop_monitoramento():
    time.sleep(60)  # dá tempo do bot subir
    while True:
        try:
            print("[MONITOR] Verificando preços...")
            verificar_todos()
        except Exception as e:
            print(f"[MONITOR] Erro no ciclo: {e}")
        time.sleep(INTERVALO_MIN * 60)


# ================= INICIALIZAÇÃO =================
if __name__ == "__main__":
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    carregar_produtos()
    threading.Thread(target=loop_monitoramento, daemon=True).start()
    try:
        bot.remove_webhook()
    except Exception as e:
        print(f"[BOT] remove_webhook: {e}")
    print("Bot rodando com busca por API/Web, alertas automáticos e Gist...")
    bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
