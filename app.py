import html
import json
import os
import re
import time
import pandas as pd
import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys

# ==============================================================================
# CONFIGURAÇÃO GERAL DA PÁGINA (Interface Gráfica com Streamlit)
# ==============================================================================
# Define o título da aba do navegador, o ícone e o layout (wide = tela cheia)
st.set_page_config(page_title="Automador SIGAA", page_icon="🤖", layout="wide")
st.title("🤖 Automador de Workflows SIGAA")

# Definição dos nomes dos arquivos onde os dados serão salvos localmente
ARQUIVO_CATALOGO = "elementos_sigaa.json" # Guarda os seletores mapeados das telas
ARQUIVO_TEXTOS = "textos_padrao.json"     # Guarda textos comuns para validação
ARQUIVO_CHECKPOINT = "checkpoint.json"    # Guarda o progresso em caso de erro

# ==============================================================================
# INICIALIZAÇÃO DO ESTADO GLOBAL (Memória do Aplicativo)
# O Streamlit recarrega a página a cada clique. O 'session_state' guarda dados 
# para que eles não se percam nesses recarregamentos.
# ==============================================================================
if "steps" not in st.session_state:
    st.session_state.steps = [] # Lista que armazena os passos do workflow
if "driver" not in st.session_state:
    st.session_state.driver = None # Armazena a janela do navegador aberta
if "planilhas_disponiveis" not in st.session_state:
    st.session_state.planilhas_disponiveis = {} # Guarda os dados dos Excel/CSV


# ==============================================================================
# 1. FUNÇÕES DE MANIPULAÇÃO DE ARQUIVOS LOCAIS
# ==============================================================================

def carregar_json_local(caminho):
    """Lê um arquivo JSON do computador. Se não existir, cria a estrutura básica."""
    if os.path.exists(caminho):
        try:
            # Tenta abrir o arquivo em modo leitura ("r") com codificação UTF-8
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    
    # Se for o arquivo de catálogo e ele não existir, cria um formato inicial vazio
    if caminho == ARQUIVO_CATALOGO:
        catalogo_inicial = {
            "Modulos_Principais": {
                "Menu_Modulos": {}
            }
        }
        salvar_json_local(ARQUIVO_CATALOGO, catalogo_inicial)
        return catalogo_inicial
        
    return {}

def salvar_json_local(caminho, dados):
    """Salva um dicionário Python em um arquivo JSON de forma organizada (indent=2)."""
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

def salvar_elemento_no_catalogo(modulo_pai, nome_tela, mapa_elementos):
    """Atualiza o arquivo do catálogo (elementos_sigaa.json) com novos botões mapeados."""
    dados = carregar_json_local(ARQUIVO_CATALOGO)
    
    # Se o módulo (ex: Modulo_Graduacao) não existir, cria
    if modulo_pai not in dados:
        dados[modulo_pai] = {}
        
    # Adiciona/Atualiza a tela e os elementos dentro do módulo correspondente
    dados[modulo_pai][nome_tela] = mapa_elementos
    salvar_json_local(ARQUIVO_CATALOGO, dados)
    return dados


# ==============================================================================
# 2. FUNÇÕES DE AUTOMAÇÃO SELENIUM (O "Cérebro" do Robô)
# ==============================================================================

def parsear_seletor(sel_str):
    """Recebe um texto de seletor (ex: #botao, //div, xpath/...) e converte pro padrão do Selenium."""
    sel_str = sel_str.strip()

    # Se começa com // ou (, é claramente um XPath
    if sel_str.startswith("//") or sel_str.startswith("("):
        return By.XPATH, sel_str

    # Trata prefixos customizados caso existam
    if sel_str.startswith("xpath"):
        return By.XPATH, re.sub(r"^xpath/*", "//", sel_str).replace('\\"', '"')

    # Se não tiver caracteres complexos, assume que é um ID de HTML
    if not any(c in sel_str for c in ["/", "[", "]", " ", ">", "~", "+"]):
        clean_id = sel_str.lstrip("#").replace("\\:", ":").replace("\\", "")
        return By.XPATH, f"//*[@id='{clean_id}']"

    # Trata buscas por texto visível (aria/ ou text/)
    if sel_str.startswith("aria/") or sel_str.startswith("text/"):
        clean_txt = sel_str.split("/", 1)[1].split("[")[0].strip()
        if "textbox" in sel_str or "role=\"textbox\"" in sel_str:
            return By.XPATH, f"//input[contains(@title, '{clean_txt}') or contains(@placeholder, '{clean_txt}') or contains(@id, '{clean_txt}')]"
        return By.XPATH, f"//*[contains(text(), '{clean_txt}') or @title='{clean_txt}']"

    if sel_str.startswith("pierce/"):
        sel_str = sel_str.replace("pierce/", "")

    # Retorna como seletor CSS caso não se encaixe nos XPaths
    return By.CSS_SELECTOR, sel_str.replace(":", "\\:")

def encontrar_elemento_com_fallback(driver, wait, passo, retentativas=3, intervalo=1.0):
    """Tenta encontrar um botão/campo na tela usando diversas estratégias se a principal falhar."""
    target_raw = passo.get("seletor_target", "").strip()
    lista_seletores = []

    # 1. Gera várias opções de busca baseadas no texto fornecido
    if target_raw:
        clean_id = target_raw.lstrip("#").replace("\\:", ":")
        lista_seletores.append((By.XPATH, f"//*[@id='{clean_id}']")) # Busca por ID via XPath
        lista_seletores.append((By.ID, clean_id))                    # Busca direto por ID nativo
        
        if target_raw.startswith("//") or target_raw.startswith("("):
            lista_seletores.append((By.XPATH, target_raw))
            
        parsed = parsear_seletor(target_raw)
        if parsed not in lista_seletores:
            lista_seletores.append(parsed)

    # 2. Adiciona alternativas pré-mapeadas se existirem no passo
    for item in passo.get("lista_seletores", []):
        if item not in lista_seletores:
            lista_seletores.append(item)

    ultimo_erro = None
    
    # 3. Tenta encontrar o elemento (Loop de Retentativas)
    for tentativa in range(retentativas):
        for by_type, target in lista_seletores:
            try:
                # Aguarda até que o elemento apareça na página HTML
                elem = wait.until(EC.presence_of_element_located((by_type, target)))
                
                # Executa um script em JavaScript para rolar a tela até o elemento
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                time.sleep(0.1)
                return elem # Retorna o elemento com sucesso
            except Exception as e:
                ultimo_erro = e
                continue # Se falhou, tenta a próxima alternativa de seletor
        # Pausa antes de tentar a próxima 'rodada' (caso a tela esteja demorando a carregar)
        time.sleep(intervalo)

    # Se esgotaram as tentativas e seletores, acusa erro e para o robô
    raise Exception(f"Elemento não localizado ({target_raw}) após {retentativas} tentativa(s). Erro: {ultimo_erro}")

def preencher_campo_sigaa(driver, elem, valor):
    """Preenche campos lidando com as máscaras e travas do framework JSF do SIGAA."""
    val_str = str(valor).strip()
    
    # Tenta focar e clicar no campo para ativá-lo (alguns campos exigem clique antes de digitar)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        driver.execute_script("arguments[0].focus(); arguments[0].click();", elem)
        time.sleep(0.2)
    except Exception:
        pass

    # Tenta limpar o campo simulando Ctrl+A e Backspace
    try:
        elem.send_keys(Keys.CONTROL + "a")
        elem.send_keys(Keys.BACKSPACE)
        time.sleep(0.1)
    except Exception:
        # Fallback: apaga o conteúdo forçando via JavaScript
        driver.execute_script("arguments[0].value = '';", elem)

    # Simula a digitação humana caractere por caractere (necessário para máscaras de JS do SIGAA)
    try:
        for char in val_str:
            elem.send_keys(char)
            time.sleep(0.03)
    except Exception:
        # Fallback: insere a string inteira de uma vez via JavaScript
        driver.execute_script("arguments[0].value = arguments[1];", elem, val_str)

    # Dispara eventos obrigatórios para que o sistema perceba que o texto mudou
    # Sem isso, o SIGAA pode "esquecer" o texto digitado ao clicar em "Salvar"
    try:
        driver.execute_script("""
            var el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        """, elem)
    except Exception:
        pass

def clicar_elemento_seguro(driver, elem):
    """Garante que o clique aconteça. Se o Selenium falhar (botão sobreposto), usa JavaScript."""
    try:
        elem.click() # Clique nativo simulando o mouse
    except Exception:
        driver.execute_script("arguments[0].click();", elem) # Clique direto no código HTML

def aguardar_login_manual(driver, url_login_ambiente, timeout_segundos=120):
    """Navega para a URL inicial e aguarda o usuário fazer o login."""
    try:
        url_atual = driver.current_url
    except Exception:
        url_atual = ""

    # Se o navegador acabou de abrir e está vazio, envia para a página de login
    if not url_atual or "data:" in url_atual or "about:blank" in url_atual:
        driver.get(url_login_ambiente)
        # Trata pop-ups (alertas) inesperados ao carregar a página
        try:
            alert = driver.switch_to.alert
            alert.accept()
        except Exception:
            pass

    tempo_inicial = time.time()
    
    # Exibe uma mensagem giratória na tela aguardando o login
    with st.spinner("🔑 Faça o login no Chrome para continuar..."):
        while time.time() - tempo_inicial < timeout_segundos:
            try:
                # O robô procura elementos que SÓ existem na tela APÓS logar (ex: botão de Sair/Logoff)
                elementos_sessao_ativa = driver.find_elements(
                    By.XPATH, 
                    "//a[contains(@href, 'dispatch=logOff')] | //a[contains(@href, 'logoff')] | //div[@id='info-usuario'] | //div[@id='menu_principal']"
                )
                
                # Se achou pelo menos um desses elementos, o login foi feito com sucesso
                if len(elementos_sessao_ativa) > 0:
                    st.success("✅ Login e sessão ativa confirmados!")
                    return True
            except Exception:
                pass
            time.sleep(1) # Aguarda 1 segundo e verifica de novo
            
    raise Exception("Tempo limite para login esgotado ou sessão não identificada.")

def normalizar_texto(texto):
    """Limpa textos HTML (quebras de linha, tags escondidas) para facilitar a comparação."""
    if not texto:
        return ""
    texto_desescapado = html.unescape(texto) # Converte &aacute; para á, etc.
    # Transforma múltiplos espaços/quebras em 1 espaço só, e converte pra minúsculo
    return re.sub(r'\s+', ' ', texto_desescapado).strip().lower()

def validar_texto_na_tela(driver, texto_esperado, timeout=5, retentativas=3, intervalo=1.0):
    """Procura uma frase exata em qualquer lugar visível ou no código fonte da página."""
    texto_esperado_norm = normalizar_texto(texto_esperado)
    if not texto_esperado_norm:
        return True

    for tentativa in range(retentativas):
        tempo_inicial = time.time()
        while time.time() - tempo_inicial < timeout:
            try:
                # Busca no texto que é visível para o usuário na tela
                body_elem = driver.find_element(By.TAG_NAME, "body")
                body_texto_norm = normalizar_texto(body_elem.text)
                if texto_esperado_norm in body_texto_norm:
                    return True

                # Busca no código por trás da página (HTML)
                page_source_norm = normalizar_texto(driver.page_source)
                if texto_esperado_norm in page_source_norm:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        time.sleep(intervalo) # Pausa entre as retentativas
    return False

def mapear_tela_atual_para_json(driver, nome_da_tela):
    """Lê todos os botões e campos da página atual e cria um dicionário (catalogo) deles."""
    # Busca inputs, textareas, buttons, selects e links que tenham texto ou atributo 'title'
    elementos = driver.find_elements(
        By.XPATH, 
        "//input[not(@type='hidden')] | //textarea | //button | //select | //a[text()] | //a[@title] | //a[@href]"
    )
    
    mapa_tela = {}
    
    # Palavras e links irrelevantes (rodape, acessibilidade, etc) que o extrator ignora
    ruidos_ignorar = [
        "ufgnet", "aumentar a texto", "diminuir o texto", "acessibilidade", 
        "ir para o conteúdo", "ir para o menu", "ir para a busca", "sair", "ajuda"
    ]
    
    for elem in elementos:
        try:
            id_attr = elem.get_attribute("id") or ""
            title_attr = elem.get_attribute("title") or ""
            texto_visivel = elem.text.strip()
            
            # Pula ids auto-gerados dinamicamente que não servem para automação
            if "yuievtautoid" in id_attr.lower():
                continue
                
            # Classifica e formata as chaves baseadas no texto, id ou título do elemento
            if texto_visivel and len(texto_visivel) > 1:
                if texto_visivel.lower() in ruidos_ignorar:
                    continue
                nome_chave = re.sub(r'[^\w\s]', '', texto_visivel).strip().lower().replace(" ", "_")
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//a[contains(text(), '{texto_visivel}')]"
                }
            elif id_attr and not id_attr.startswith("j_id"):
                nome_chave = id_attr.split(":")[-1]
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//*[@id='{id_attr}']"
                }
            elif title_attr and title_attr.lower() not in ruidos_ignorar:
                nome_chave = title_attr.lower().replace(" ", "_")
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//*[@title='{title_attr}']"
                }
        except Exception:
            continue
            
    return {nome_da_tela: mapa_tela}


# ==============================================================================
# 3. GERENCIAMENTO DO NAVEGADOR (CHROME)
# ==============================================================================

def obter_driver_ativo():
    """Checa se o navegador Chrome já está aberto e se comunicando com o Python."""
    if "driver" in st.session_state and st.session_state.driver is not None:
        try:
            # Tenta pegar a URL atual só para testar se a janela não foi fechada pelo usuário
            _ = st.session_state.driver.current_url
            return st.session_state.driver
        except Exception:
            st.session_state.driver = None # Reseta caso a janela tenha sido fechada
    return None

def iniciar_navegador_universal():
    """Abre uma nova janela do Chrome usando as configurações otimizadas."""
    driver_ativo = obter_driver_ativo()
    if driver_ativo:
        return driver_ativo

    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    # ChromeDriverManager baixa automaticamente a versão correta do ChromeDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    st.session_state.driver = driver
    return driver

def fechar_navegador_universal():
    """Encerra a instância do Chrome e limpa a memória."""
    if "driver" in st.session_state and st.session_state.driver is not None:
        try:
            st.session_state.driver.quit()
        except Exception:
            pass
        st.session_state.driver = None


# ==============================================================================
# 4. BARRA LATERAL (CONFIGURAÇÃO DINÂMICA DE SERVIDOR / IES)
# ==============================================================================

st.sidebar.header("⚙️ Servidor do SIGAA")

# Caixa de seleção para permitir o uso em Produção ou em Ambientes Customizados
opcao_servidor = st.sidebar.selectbox(
    "Escolha o Ambiente:",
    ["UFG Produção", "Outro Servidor"]
)

# Se escolher outro servidor, abre um campo de texto para digitar o link
if opcao_servidor == "Outro Servidor":
    URL_LOGIN_CONFIG = st.sidebar.text_input(
        "Cole a URL de Login do SIGAA:",
        value="",
        help="Informe o link da tela inicial/login do sistema desejado."
    )
else:
    URL_LOGIN_CONFIG = "https://sigaa.sistemas.ufg.br/sigaa/verTelaLogin.do"

st.sidebar.divider()
st.sidebar.header("🌐 Status do Navegador Chrome")
driver_status = obter_driver_ativo()

# Exibe o status da conexão com o Chrome
if driver_status:
    st.sidebar.success("🟢 Navegador Aberto e Conectado")
    if st.sidebar.button("❌ Fechar Navegador", use_container_width=True):
        fechar_navegador_universal()
        st.rerun() # Atualiza a página do Streamlit
else:
    st.sidebar.warning("🔴 Navegador Desconectado")
    if st.sidebar.button("🌐 Abrir / Logar no Chrome", use_container_width=True):
        driver = iniciar_navegador_universal()
        try:
            aguardar_login_manual(driver, URL_LOGIN_CONFIG)
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao abrir navegador: {e}")


# ==============================================================================
# 5. INTERFACE PRINCIPAL DIVIDIDA EM 3 ABAS
# ==============================================================================

# Cria 3 abas no topo da página
tab1, tab2, tab3 = st.tabs(["🚀 Editor e Executor do Workflow", "🔍 Mapeador de Telas", "📝 Textos Padrão"])

# ------------------------------------------------------------------------------
# ABA 1: CONSTRUTOR DE WORKFLOW E EXECUÇÃO
# ------------------------------------------------------------------------------
with tab1:
    st.subheader("🛠️ Construtor e Executor de Workflows")

    col_exp1, col_exp2, col_exp3 = st.columns([2, 2, 1])
    
    # Bloco para IMPORTAR um workflow (Arquivo JSON salvo anteriormente)
    with col_exp1:
        file_import_wf = st.file_uploader("📂 Importar Workflow Salvo (.json)", type=["json"], key="import_wf_json")
        if file_import_wf is not None:
            # Evita importar o mesmo arquivo de forma duplicada
            if "last_wf_name" not in st.session_state or st.session_state.last_wf_name != file_import_wf.name:
                try:
                    loaded_steps = json.load(file_import_wf)
                    st.session_state.steps = loaded_steps
                    st.session_state.last_wf_name = file_import_wf.name
                    
                    # Procura planilhas anexadas dentro do passo 'Iniciar Loop'
                    st.session_state.planilhas_disponiveis = {}
                    for step in loaded_steps:
                        if step.get("acao") == "Iniciar Loop" and "planilhas" in step:
                            st.session_state.planilhas_disponiveis.update(step["planilhas"])
                            
                    st.success("Workflow importado com sucesso!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao carregar workflow: {e}")

    # Bloco para EXPORTAR o workflow atual da tela para um arquivo JSON
    with col_exp2:
        st.write("") # Espaçadores estéticos
        st.write("")
        if st.session_state.steps:
            # Transforma a lista de passos (dicionários) em texto JSON
            json_export = json.dumps(st.session_state.steps, indent=2, ensure_ascii=False)
            st.download_button(
                label="💾 Exportar Workflow (.json)",
                data=json_export,
                file_name="workflow_sigaa.json",
                mime="application/json",
                use_container_width=True
            )

    # Bloco para Limpar/Zerar a tela
    with col_exp3:
        st.write("")
        st.write("")
        if st.button("🗑️ Limpar Passos", use_container_width=True):
            st.session_state.steps = []
            st.session_state.planilhas_disponiveis = {}
            st.rerun()

    st.divider()

    # --- SEÇÃO DE ADICIONAR NOVOS PASSOS ---
    with st.expander("➕ Adicionar Novo Passo ao Workflow", expanded=True):
        catalogo = carregar_json_local(ARQUIVO_CATALOGO)
        
        # Seleção de Qual ação realizar e Onde inserir na lista
        col_pos1, col_pos2 = st.columns([2, 2])
        with col_pos1:
            tipo_passo = st.selectbox("Tipo de Ação:", ["Clicar", "Preencher", "Navegar", "Validar Texto", "🔁 Iniciar Loop", "🔚 Fim de Loop"])
        with col_pos2:
            total_p = len(st.session_state.steps)
            opcoes_posicao = ["No final do workflow"] + [f"Antes do Passo {i+1}" for i in range(total_p)] + [f"Depois do Passo {i+1}" for i in range(total_p)]
            posicao_escolhida = st.selectbox("Posição de Inserção:", opcoes_posicao)

        # Função auxiliar que traduz a seleção acima ("Antes do X") para um número/índice de lista
        def calcular_indice_insercao():
            if posicao_escolhida == "No final do workflow" or total_p == 0:
                return len(st.session_state.steps)
            if "Antes do Passo" in posicao_escolhida:
                num = int(posicao_escolhida.replace("Antes do Passo ", "")) - 1
                return max(0, num)
            if "Depois do Passo" in posicao_escolhida:
                num = int(posicao_escolhida.replace("Depois do Passo ", ""))
                return min(len(st.session_state.steps), num)
            return len(st.session_state.steps)

        # Configurações para ações que interagem diretamente com a tela (Clicar, Preencher)
        if tipo_passo in ["Clicar", "Preencher"]:
            origem_seletor = st.radio("Origem do Elemento:", ["Usar Catálogo Mapeado (Recomendado)", "Digitar Seletor Manualmente"], horizontal=True)
            
            target_add = ""
            by_add = "xpath"
            nome_exibicao = ""

            # Carrega a lista hierárquica a partir do JSON (Módulo > Tela > Elemento)
            if origem_seletor == "Usar Catálogo Mapeado (Recomendado)" and catalogo:
                col_h1, col_h2, col_h3 = st.columns(3)
                with col_h1:
                    modulos_disponiveis = list(catalogo.keys())
                    mod_sel = st.selectbox("1. Módulo Hierárquico:", modulos_disponiveis)
                with col_h2:
                    telas_disponiveis = list(catalogo.get(mod_sel, {}).keys())
                    tela_sel = st.selectbox("2. Tela / Seção:", telas_disponiveis)
                with col_h3:
                    elementos_disponiveis = catalogo.get(mod_sel, {}).get(tela_sel, {})
                    elem_sel = st.selectbox("3. Elemento:", list(elementos_disponiveis.keys()))

                # Pega as informações do seletor escolhido no catálogo
                if elem_sel in elementos_disponiveis:
                    dados_elem = elementos_disponiveis[elem_sel]
                    target_add = dados_elem["target"]
                    by_add = dados_elem["by"]
                    nome_exibicao = f"{mod_sel} -> {tela_sel} -> {elem_sel}"
                    st.caption(f"🎯 Seletor extraído do catálogo: `{target_add}` (Tipo: `{by_add}`)")

            else:
                # Permite inserção manual (usuários avançados digitarem o XPath)
                target_add = st.text_input("Seletor do Elemento (ID ou XPath):", value="formulario:matriculaDiscente")
                by_add = "xpath"
                nome_exibicao = target_add

            # Opções universais de timeout e pausas para o passo
            col_p1, col_p2, col_p3 = st.columns(3)
            with col_p1:
                tempo_pausa_add = st.number_input("Pausa após a ação (s):", min_value=0.0, value=1.0, step=0.5)
            with col_p2:
                retentativas_add = st.number_input("Re-tentativas:", min_value=1, value=3, step=1)
            with col_p3:
                intervalo_add = st.number_input("Intervalo re-tentativas (s):", min_value=0.5, value=1.0, step=0.5)

            valor_add = ""
            usar_loop_val = False
            planilha_ref = ""
            coluna_ref = ""

            # Configurações específicas para "Preencher" (Escolher se o valor é fixo ou vem do Excel)
            if tipo_passo == "Preencher":
                usar_loop_val = st.checkbox("Usar valor de Planilha do Loop", value=True)
                if usar_loop_val:
                    if st.session_state.planilhas_disponiveis:
                        col_pl1, col_pl2 = st.columns(2)
                        with col_pl1:
                            planilha_ref = st.selectbox("Selecione a Planilha:", list(st.session_state.planilhas_disponiveis.keys()))
                        with col_pl2:
                            colunas_disp = list(st.session_state.planilhas_disponiveis[planilha_ref].keys())
                            coluna_ref = st.selectbox("Selecione a Coluna:", colunas_disp)
                    else:
                        st.warning("Adicione um '🔁 Iniciar Loop' com planilhas para habilitar a seleção de colunas.")
                else:
                    valor_add = st.text_input("Valor fixo a preencher:")

            # Botão de confirmação que insere de fato o passo na lista principal
            if st.button(f"➕ Adicionar {tipo_passo} ao Workflow", type="primary"):
                if target_add:
                    novo_step = {
                        "acao": tipo_passo,
                        "seletor_target": target_add,
                        "seletor_by": by_add,
                        "rotulo": nome_exibicao,
                        "tempo_espera": tempo_pausa_add,
                        "retentativas": retentativas_add,
                        "intervalo_retentativa": intervalo_add
                    }
                    if tipo_passo == "Preencher":
                        novo_step["valor"] = valor_add
                        novo_step["usar_loop"] = usar_loop_val
                        novo_step["planilha_ref"] = planilha_ref
                        novo_step["coluna_ref"] = coluna_ref
                        
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, novo_step)
                    st.success(f"Passo '{tipo_passo}' inserido na posição {idx_ins + 1}!")
                    st.rerun() # Atualiza a tela

        # Configurações para a ação "Navegar" (Acessar Link)
        elif tipo_passo == "Navegar":
            col_nav1, col_nav2 = st.columns([3, 1])
            with col_nav1:
                url_nav_add = st.text_input("URL para onde navegar:", value="")
            with col_nav2:
                tempo_pausa_add = st.number_input("Pausa após navegar (s):", min_value=0.0, value=1.5, step=0.5)
            
            if st.button("➕ Adicionar Navegação ao Workflow", type="primary"):
                if url_nav_add:
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, {
                        "acao": "Navegar",
                        "detalhe": url_nav_add,
                        "tempo_espera": tempo_pausa_add
                    })
                    st.success("Passo 'Navegar' inserido!")
                    st.rerun()

        # Configurações para a ação "Validar Texto" (Lê a aba de Textos Padrões)
        elif tipo_passo == "Validar Texto":
            textos_cadastrados = carregar_json_local(ARQUIVO_TEXTOS)
            opcoes = list(textos_cadastrados.keys())
            if opcoes:
                col_v1, col_v2, col_v3 = st.columns(3)
                with col_v1:
                    txt_sel = st.selectbox("Escolha o Texto Padrão de Validação:", opcoes)
                with col_v2:
                    timeout_val = st.number_input("Timeout busca (s):", min_value=1, value=5)
                    tempo_pausa_add = st.number_input("Pausa pós-validação (s):", min_value=0.0, value=1.0, step=0.5)
                with col_v3:
                    retentativas_add = st.number_input("Re-tentativas do texto:", min_value=1, value=3, step=1)
                    intervalo_add = st.number_input("Intervalo entre buscas (s):", min_value=0.5, value=1.0, step=0.5)
                
                if st.button("Adicionar Validação"):
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, {
                        "acao": "Validar Texto",
                        "texto": textos_cadastrados[txt_sel],
                        "rotulo": txt_sel,
                        "timeout": timeout_val,
                        "tempo_espera": tempo_pausa_add,
                        "retentativas": retentativas_add,
                        "intervalo_retentativa": intervalo_add
                    })
                    st.rerun()
            else:
                st.warning("Cadastre textos padrão na Aba 3 primeiro.")

        # Configurações para a ação "Iniciar Loop" (Leitura de Planilhas)
        elif tipo_passo == "🔁 Iniciar Loop":
            st.markdown("Envie **uma ou mais planilhas** (.xlsx, .csv) para associar a este ciclo de repetição:")
            files_loop = st.file_uploader("Planilhas (.xlsx, .csv):", type=["xlsx", "xls", "csv"], accept_multiple_files=True, key="up_multi_planilhas")
            
            if files_loop:
                planilhas_processadas = {}
                max_linhas = 0

                # Lê cada arquivo que o usuário subiu
                for f in files_loop:
                    # Usa Pandas para ler o Excel ou CSV e transforma as colunas em dicionários do Python
                    df = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f)
                    planilhas_processadas[f.name] = df.astype(str).to_dict(orient="list")
                    
                    # Define que a duração total do Loop será igual à planilha que tem mais linhas
                    if len(df) > max_linhas:
                        max_linhas = len(df)

                st.info(f"📊 {len(files_loop)} planilha(s) pronta(s). Total de linhas no loop: {max_linhas}")

                if st.button("Adicionar Início de Loop com Planilhas"):
                    st.session_state.planilhas_disponiveis.update(planilhas_processadas)
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, {
                        "acao": "Iniciar Loop",
                        "planilhas": planilhas_processadas,
                        "max_linhas": max_linhas,
                        "nomes_arquivos": list(planilhas_processadas.keys())
                    })
                    st.success("Loop inserido!")
                    st.rerun()

        # Fim de Loop (Demarcação onde as repetições recomeçam)
        elif tipo_passo == "🔚 Fim de Loop":
            if st.button("Adicionar Fim de Loop"):
                idx_ins = calcular_indice_insercao()
                st.session_state.steps.insert(idx_ins, {"acao": "Fim de Loop"})
                st.rerun()

    # --- LISTAGEM E REORDENAÇÃO DOS PASSOS DO WORKFLOW ATUAL ---
    st.subheader(f"📋 Passos do Workflow ({len(st.session_state.steps)} etapas)")

    # Varre a lista principal de passos montando o layout visual (Cards)
    for idx, step in enumerate(st.session_state.steps):
        acao = step["acao"]
        
        # Card Visual: Passo de Iniciar Loop
        if acao == "Iniciar Loop":
            planilhas_no_step = step.get("planilhas", {})
            m_lin = step.get("max_linhas", 0)
            
            col_lk1, col_lk2 = st.columns([5, 1])
            with col_lk1:
                st.warning(f"🔁 **Passo {idx + 1}: Iniciar Loop** ({len(planilhas_no_step)} planilha(s) vinculada(s) - {m_lin} linhas)")
            with col_lk2:
                # Botões de mover para cima/baixo na lista
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if idx > 0 and st.button("⬆️", key=f"up_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx-1] = st.session_state.steps[idx-1], st.session_state.steps[idx]
                        st.rerun()
                with col_btn2:
                    if idx < len(st.session_state.steps) - 1 and st.button("⬇️", key=f"down_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx+1] = st.session_state.steps[idx+1], st.session_state.steps[idx]
                        st.rerun()
            
            # Se a planilha não veio carregada do JSON, exibe um uploader pedindo de novo
            if not planilhas_no_step:
                st.info("⚠️ Este loop precisa de planilhas. Faça o upload abaixo para re-vincular:")
                re_files = st.file_uploader(f"Upload de Planilhas para o Passo {idx+1}:", type=["xlsx", "xls", "csv"], accept_multiple_files=True, key=f"re_up_{idx}")
                
                if re_files:
                    re_planilhas = {}
                    re_max = 0
                    for rf in re_files:
                        rdf = pd.read_csv(rf) if rf.name.endswith(".csv") else pd.read_excel(rf)
                        re_planilhas[rf.name] = rdf.astype(str).to_dict(orient="list")
                        if len(rdf) > re_max:
                            re_max = len(rdf)
                    
                    step["planilhas"] = re_planilhas
                    step["max_linhas"] = re_max
                    step["nomes_arquivos"] = list(re_planilhas.keys())
                    st.session_state.planilhas_disponiveis.update(re_planilhas)
                    st.success("Planilhas vinculadas com sucesso!")
                    st.rerun()

        # Card Visual: Passo de Fim de Loop
        elif acao == "Fim de Loop":
            col_lk1, col_lk2 = st.columns([5, 1])
            with col_lk1:
                st.warning(f"🔚 **Passo {idx + 1}: Fim de Loop**")
            with col_lk2:
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if idx > 0 and st.button("⬆️", key=f"up_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx-1] = st.session_state.steps[idx-1], st.session_state.steps[idx]
                        st.rerun()
                with col_btn2:
                    if idx < len(st.session_state.steps) - 1 and st.button("⬇️", key=f"down_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx+1] = st.session_state.steps[idx+1], st.session_state.steps[idx]
                        st.rerun()

        # Card Visual: Outros passos normais (Clica, Preencher, Navegar)
        else:
            rotulo_card = step.get('rotulo') or step.get('seletor_target') or step.get('detalhe', '')
            with st.expander(f"Passo {idx + 1}: {acao} - {rotulo_card}", expanded=False):
                col1, col2, col3 = st.columns([2, 4, 1])
                with col1:
                    st.write(f"**Ação:** `{acao}`")
                    if idx > 0 and st.button("⬆️ Mover para Cima", key=f"up_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx-1] = st.session_state.steps[idx-1], st.session_state.steps[idx]
                        st.rerun()
                    if idx < len(st.session_state.steps) - 1 and st.button("⬇️ Mover para Baixo", key=f"down_{idx}"):
                        st.session_state.steps[idx], st.session_state.steps[idx+1] = st.session_state.steps[idx+1], st.session_state.steps[idx]
                        st.rerun()

                with col2:
                    # Edição das propriedades do passo diretamente no card criado
                    col_s1, col_s2 = st.columns(2)
                    with col_s1:
                        step["tempo_espera"] = st.number_input(f"Pausa após ação (s)", value=float(step.get("tempo_espera", 1.0)), min_value=0.0, step=0.5, key=f"pausa_{idx}")
                    with col_s2:
                        step["retentativas"] = st.number_input(f"Re-tentativas", value=int(step.get("retentativas", 3)), min_value=1, step=1, key=f"ret_{idx}")

                    if acao == "Navegar":
                        step["detalhe"] = st.text_input(f"URL de Destino", value=step.get("detalhe", ""), key=f"url_{idx}")
                    elif acao == "Preencher":
                        step["seletor_target"] = st.text_input(f"Seletor", value=step.get("seletor_target", ""), key=f"sel_{idx}")
                        step["usar_loop"] = st.checkbox("Usar valor de Planilha do Loop", value=step.get("usar_loop", True), key=f"chk_loop_{idx}")
                        if step["usar_loop"]:
                            st.caption(f"📊 Planilha Alvo: `{step.get('planilha_ref')}` | Coluna: `{step.get('coluna_ref')}`")
                        else:
                            step["valor"] = st.text_input(f"Valor Fixo", value=step.get("valor", ""), key=f"val_{idx}")
                    elif acao == "Clicar":
                        step["seletor_target"] = st.text_input(f"Seletor", value=step.get("seletor_target", ""), key=f"sel_{idx}")
                    elif acao == "Validar Texto":
                        st.write(f"Texto Esperado: `{step.get('texto')}`")
                        step["timeout"] = st.number_input(f"Timeout Busca (s)", value=step.get("timeout", 5), key=f"tout_{idx}")

                with col3:
                    # Deletar o passo da lista principal
                    if st.button("🗑️ Excluir", key=f"del_{idx}"):
                        st.session_state.steps.pop(idx)
                        st.rerun()

    st.divider()

    # --- EXECUÇÃO DO WORKFLOW E CHECKPOINT (O MOTOR DO APLICATIVO) ---
    st.subheader("🚀 Execução da Automação")

    if st.button("🚀 Executar Workflow no Chrome", type="primary", use_container_width=True):
        if not st.session_state.steps:
            st.error("Adicione passos ao workflow antes de executar.")
        else:
            driver = obter_driver_ativo()
            # Se não há driver, obriga a abrir um novo navegador
            if not driver:
                st.info("Iniciando navegador...")
                driver = iniciar_navegador_universal()

            wait = WebDriverWait(driver, 10)
            checkpoint_dados = carregar_json_local(ARQUIVO_CHECKPOINT)

            # Função que estrutura o salvamento do erro para retomar depois (Checkpoint)
            def registrar_falha_checkpoint(chave_cp, i_linha, idx_passo, acao_nome, passo_ref, val_proc, plan_orig, col_orig, err_msg):
                checkpoint_dados[chave_cp] = {
                    "proxima_linha_index": i_linha,
                    "status": "INTERROMPIDO_COM_ERRO",
                    "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "detalhes_falha": {
                        "linha_numero": i_linha + 1 if i_linha is not None else None,
                        "passo_index": idx_passo + 1,
                        "acao": acao_nome,
                        "rotulo_passo": passo_ref,
                        "valor_em_processamento": str(val_proc),
                        "planilha_origem": plan_orig,
                        "coluna_origem": col_orig,
                        "erro_mensagem": str(err_msg)
                    }
                }
                salvar_json_local(ARQUIVO_CHECKPOINT, checkpoint_dados)

            try:
                # Chama a função que bloqueia o script até que o usuário faça o login
                aguardar_login_manual(driver, URL_LOGIN_CONFIG)

                st.info(f"📍 Executando workflow a partir da página atual: `{driver.current_url}`")

                interrompido = False
                step_idx = 0
                total_steps = len(st.session_state.steps)

                # Loop Principal de Passos (Anda item por item do fluxo construído na tela)
                while step_idx < total_steps and not interrompido:
                    step = st.session_state.steps[step_idx]
                    acao = step["acao"]
                    tempo_pausa = float(step.get("tempo_espera", 1.0))
                    n_retentativas = int(step.get("retentativas", 3))
                    t_intervalo = float(step.get("intervalo_retentativa", 1.0))

                    # Lida com comandos Básicos (FORA DO LOOP de planilhas)
                    
                    if acao == "Navegar":
                        st.write(f"▶️ Navegando para: `{step['detalhe']}`")
                        try:
                            driver.get(step["detalhe"])
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_nav_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Erro ao navegar para `{step['detalhe']}`.")
                            interrompido = True

                    elif acao == "Clicar":
                        st.write(f"▶️ Clicando em: `{step.get('rotulo', step['seletor_target'])}`")
                        try:
                            # Ignora alertas chatos (pop-ups JS do navegador) se aparecerem
                            try:
                                driver.switch_to.alert.accept()
                            except Exception:
                                pass
                            
                            elem = encontrar_elemento_com_fallback(driver, wait, step, retentativas=n_retentativas, intervalo=t_intervalo)
                            clicar_elemento_seguro(driver, elem)
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_clk_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** {e_clk_out}")
                            interrompido = True

                    elif acao == "Preencher" and not step.get("usar_loop", False):
                        st.write("▶️ Preenchendo campo com valor fixo...")
                        try:
                            elem = encontrar_elemento_com_fallback(driver, wait, step, retentativas=n_retentativas, intervalo=t_intervalo)
                            preencher_campo_sigaa(driver, elem, step["valor"])
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_pr_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** {e_pr_out}")
                            interrompido = True

                    elif acao == "Validar Texto" and not step.get("no_loop", False):
                        st.write(f"▶️ Verificando texto: '{step['texto']}'")
                        encontrou = validar_texto_na_tela(driver, step['texto'], timeout=step.get('timeout', 5), retentativas=n_retentativas, intervalo=t_intervalo)
                        if not encontrou:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Texto '{step['texto']}' não encontrado após {n_retentativas} tentativa(s).")
                            interrompido = True
                        else:
                            st.success(f"✅ Texto localizado na tela: '{step['texto']}'")
                            time.sleep(tempo_pausa)
                            step_idx += 1

                    # MODO AVANÇADO (DENTRO DO LOOP DE PLANILHAS)
                    elif acao == "Iniciar Loop":
                        planilhas_loop = step.get("planilhas", {})
                        max_lin = step.get("max_linhas", 0)
                        
                        # Gera um nome de arquivo pro checkpoint (ex: dados_alunos.xlsx)
                        nome_key_checkpoint = "_".join(list(planilhas_loop.keys())) or "loop_generico"

                        # Consulta o arquivo de Checkpoint para saber de qual linha deve recomeçar
                        dados_salvos = checkpoint_dados.get(nome_key_checkpoint, {})
                        if isinstance(dados_salvos, dict):
                            linha_checkpoint = dados_salvos.get("proxima_linha_index", 0)
                        else:
                            try:
                                linha_checkpoint = int(dados_salvos)
                            except Exception:
                                linha_checkpoint = 0

                        # Acha qual é o passo "Fim de Loop" para demarcar onde as repetições acabam
                        end_loop_idx = step_idx + 1
                        while end_loop_idx < total_steps and st.session_state.steps[end_loop_idx]["acao"] != "Fim de Loop":
                            end_loop_idx += 1

                        st.info(f"🔄 **Iniciando Loop ({max_lin} linhas). Retomando da linha {linha_checkpoint + 1}...**")

                        # Roda os blocos internos para cada linha da(s) planilha(s)
                        for i_item in range(linha_checkpoint, max_lin):
                            st.write(f"🔁 **Processando Linha {i_item + 1}/{max_lin}**")

                            for inner_idx in range(step_idx + 1, end_loop_idx):
                                inner_step = st.session_state.steps[inner_idx]
                                inner_acao = inner_step["acao"]
                                inner_pausa = float(inner_step.get("tempo_espera", 1.0))
                                inner_ret = int(inner_step.get("retentativas", 3))
                                inner_inter = float(inner_step.get("intervalo_retentativa", 1.0))

                                valor_atual_acao = ""
                                planilha_atual = inner_step.get("planilha_ref", "")
                                coluna_atual = inner_step.get("coluna_ref", "")

                                # Define de onde vem o valor (Planilha ou Campo Fixo)
                                if inner_step.get("usar_loop", True) and planilha_atual and coluna_atual:
                                    try:
                                        valor_atual_acao = planilhas_loop[planilha_atual][coluna_atual][i_item]
                                    except Exception:
                                        valor_atual_acao = "N/A"
                                else:
                                    valor_atual_acao = inner_step.get("valor", inner_step.get("detalhe", inner_step.get("texto", "")))

                                rotulo_p = inner_step.get("rotulo", inner_step.get("seletor_target", ""))

                                # Navegar Interno
                                if inner_acao == "Navegar":
                                    try:
                                        driver.get(inner_step["detalhe"])
                                    except Exception as e_nav:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_nav)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Erro ao navegar.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

                                # Clicar Interno
                                elif inner_acao == "Clicar":
                                    try:
                                        elem = encontrar_elemento_com_fallback(driver, wait, inner_step, retentativas=inner_ret, intervalo=inner_inter)
                                        clicar_elemento_seguro(driver, elem)
                                    except Exception as e_clk:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_clk)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Elemento não encontrado para clicar.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

                                # Preencher Interno
                                elif inner_acao == "Preencher":
                                    try:
                                        elem = encontrar_elemento_com_fallback(driver, wait, inner_step, retentativas=inner_ret, intervalo=inner_inter)
                                        preencher_campo_sigaa(driver, elem, valor_atual_acao)
                                    except Exception as e_pr:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_pr)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Falha ao preencher o campo com valor `{valor_atual_acao}`.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

                                # Validar Texto Interno
                                elif inner_acao == "Validar Texto":
                                    encontrou = validar_texto_na_tela(driver, inner_step["texto"], timeout=inner_step.get("timeout", 5), retentativas=inner_ret, intervalo=inner_inter)
                                    if not encontrou:
                                        msg_txt = f"Texto '{inner_step['texto']}' não encontrado na tela."
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, msg_txt)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** {msg_txt}")
                                        interrompido = True
                                        break
                                    else:
                                        st.success(f"✅ Texto localizado na linha {i_item + 1}: '{inner_step['texto']}'")

                                    time.sleep(inner_pausa)

                            if interrompido:
                                break # Quebra a repetição do arquivo Excel se algo der errado na linha

                            # Se todas as ações da linha completaram com sucesso, atualiza o Checkpoint avançando a linha
                            checkpoint_dados[nome_key_checkpoint] = {
                                "proxima_linha_index": i_item + 1,
                                "status": "EM_ANDAMENTO" if (i_item + 1) < max_lin else "CONCLUIDO",
                                "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "ultima_linha_concluida": i_item + 1
                            }
                            salvar_json_local(ARQUIVO_CHECKPOINT, checkpoint_dados)

                        if interrompido:
                            break

                        # Ao final do loop das planilhas, avança o fluxo principal pro passo "Fim de Loop"
                        step_idx = end_loop_idx + 1

                    elif acao == "Fim de Loop":
                        step_idx += 1

                # Se a execução chegar aqui e o interrompido ainda for Falso, deu tudo certo!
                if not interrompido:
                    st.success("🎉 Execução do Workflow concluída!")

            except Exception as err:
                st.error(f"❌ Erro durante a execução geral: {err}")


# ------------------------------------------------------------------------------
# ABA 2: MAPEADOR DE TELAS (O Extrator Automático)
# ------------------------------------------------------------------------------
with tab2:
    st.subheader("🔍 Mapeador Automático de Telas")
    catalogo_atual = carregar_json_local(ARQUIVO_CATALOGO)
    modulos_existentes = list(catalogo_atual.keys())
    
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        opcoes = ["➕ Criar Novo Módulo..."] + modulos_existentes
        mod_escolhido = st.selectbox("Módulo Pai:", opcoes)
        modulo_pai_final = st.text_input("Nome do Módulo:", value="Modulo_Geral") if mod_escolhido == "➕ Criar Novo Módulo..." else mod_escolhido
    with col_sel2:
        nome_tela_input = st.text_input("Nome da Tela:", value="Consulta_Geral")

    st.divider()

    col_b1, col_b2, col_b3 = st.columns([2, 3, 2])
    with col_b1:
        if st.button("🌐 1. Abrir Chrome / Logar", use_container_width=True):
            driver = iniciar_navegador_universal()
            try:
                aguardar_login_manual(driver, URL_LOGIN_CONFIG)
                st.success("Navegador pronto para captura!")
            except Exception as e:
                st.error(f"Erro: {e}")

    with col_b2:
        if st.button("📸 2. Capturar Tela Atual", type="primary", use_container_width=True):
            driver = obter_driver_ativo()
            if not driver:
                st.error("Abra o navegador primeiro pelo botão '1. Abrir Chrome / Logar'.")
            else:
                try:
                    # Captura tudo via mapear_tela_atual_para_json() e injeta direto no dicionario 'mapa_elem'
                    mapa_elem = mapear_tela_atual_para_json(driver, nome_tela_input)[nome_tela_input]
                    # Salva permanentemente as capturas usando a função do catálogo
                    cat_atualizado = salvar_elemento_no_catalogo(modulo_pai_final, nome_tela_input, mapa_elem)
                    st.success(f"✅ Tela '{nome_tela_input}' salva no módulo '{modulo_pai_final}'!")
                    st.json(cat_atualizado[modulo_pai_final][nome_tela_input])
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao capturar: {e}")

    with col_b3:
        if st.button("❌ 3. Fechar Navegador", use_container_width=True):
            fechar_navegador_universal()
            st.info("Navegador fechado.")


# ------------------------------------------------------------------------------
# ABA 3: TEXTOS PADRÃO (Para validações/confirmações na tela)
# ------------------------------------------------------------------------------
with tab3:
    st.subheader("📝 Biblioteca de Textos Padrão")
    textos_atuais = carregar_json_local(ARQUIVO_TEXTOS)

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        chave_txt = st.text_input("Identificador (ex: Sucesso_Salvar):")
    with col_t2:
        conteudo_txt = st.text_input("Texto Exato Esperado na Tela:")

    if st.button("💾 Salvar Texto Padrão", type="primary"):
        if chave_txt and conteudo_txt:
            # Associa a Chave digitada (Sucesso_Salvar) ao Conteúdo Real ("Operação realizada com sucesso")
            textos_atuais[chave_txt] = conteudo_txt
            salvar_json_local(ARQUIVO_TEXTOS, textos_atuais)
            st.success("Texto salvo!")
            st.rerun()

    st.divider()
    # Exibe no formato Dicionário/JSON para visualização rápida
    st.json(textos_atuais)
