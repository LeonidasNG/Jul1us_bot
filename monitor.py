import json
import requests
import urllib.parse

def buscar_mercadolivre(termo, preco_alvo):
    resultados = []
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={urllib.parse.quote(termo)}&condition=new&sort=price_asc"
    
    try:
        resposta = requests.get(url).json()
        itens = resposta.get('results', [])
        
        for item in itens[:3]: 
            preco = float(item['price'])
            if preco <= preco_alvo:
                resultados.append({
                    "loja": "Mercado Livre",
                    "titulo": item['title'],
                    "preco": preco,
                    "link": item['permalink']
                })
    except Exception as e:
        print(f"Erro na busca: {e}")
    return resultados

def enviar_telegram(token, chat_id, mensagem):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "Markdown"
    }
    requests.post(url, data=payload)

def iniciar():
    with open('produtos.json', 'r', encoding='utf-8') as f:
        dados = json.load(f)
    
    config = dados['configuracoes']
    
    for busca in dados['buscas']:
        print(f"Procurando por: {busca['termo']}")
        ofertas = buscar_mercadolivre(busca['termo'], busca['preco_alvo'])
        
        if ofertas:
            melhor = ofertas[0]
            msg = (f"🚨 *ALERTA DE PREÇO!*\n\n"
                   f"🔍 Busca: {busca['termo']}\n"
                   f"💰 Preço: *R$ {melhor['preco']:.2f}*\n"
                   f"🎯 Seu Alvo: R$ {busca['preco_alvo']:.2f}\n\n"
                   f"🛒 [Clique aqui para comprar]({melhor['link']})")
            
            enviar_telegram(config['telegram_token'], config['telegram_chat_id'], msg)
        else:
            print("Nenhum preço bom encontrado ainda.")

if __name__ == "__main__":
    iniciar()
