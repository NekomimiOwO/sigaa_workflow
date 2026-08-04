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
from selenium.webdriver.support.ui import WebDriverWait, Select
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# ==============================================================================
# CONFIGURAÇÃO GERAL DA PÁGINA (Interface Gráfica com Streamlit)
# ==============================================================================
st.set_page_config(page_title="Automador SIGAA", page_icon="🤖", layout="wide")
st.title("🤖 Automador de Workflows SIGAA")

ARQUIVO_CATALOGO = "elementos_sigaa.json" # Guarda os seletores mapeados das telas
ARQUIVO_TEXTOS = "textos_padrao.json"     # Guarda textos comuns para validação
ARQUIVO_CHECKPOINT = "checkpoint.json"    # Guarda o progresso em caso de erro

# ==============================================================================
# INICIALIZAÇÃO DO ESTADO GLOBAL (Memória do Aplicativo)
# ==============================================================================
if "steps" not in st.session_state:
    st.session_state.steps = []
if "driver" not in st.session_state:
    st.session_state.driver = None
if "planilhas_disponiveis" not in st.session_state:
    st.session_state.planilhas_disponiveis = {}


# ==============================================================================
# 1. FUNÇÕES DE MANIPULAÇÃO DE ARQUIVOS LOCAIS
# ==============================================================================

def carregar_json_local(caminho):
    """Lê um arquivo JSON do computador. Se não existir, cria a estrutura básica."""
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    
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
    """Atualiza o catálogo respeitando elementos desativados, opções de dropdown/radio e nomes originais."""
    dados = carregar_json_local(ARQUIVO_CATALOGO)
    
    if modulo_pai not in dados:
        dados[modulo_pai] = {}
    if nome_tela not in dados[modulo_pai]:
        dados[modulo_pai][nome_tela] = {}
        
    for chave, dados_novo_elemento in mapa_elementos.items():
        elemento_existente = dados[modulo_pai][nome_tela].get(chave, {})
        
        if elemento_existente.get("ativo") == False:
            continue
        
        dados_novo_elemento["ativo"] = True
        dados_novo_elemento["nome_original"] = elemento_existente.get("nome_original", chave)
        
        if "opcoes" in elemento_existente and "opcoes" not in dados_novo_elemento:
            dados_novo_elemento["opcoes"] = elemento_existente["opcoes"]
            dados_novo_elemento["is_select"] = elemento_existente.get("is_select", False)
            dados_novo_elemento["is_radio"] = elemento_existente.get("is_radio", False)

        dados[modulo_pai][nome_tela][chave] = dados_novo_elemento
        
    salvar_json_local(ARQUIVO_CATALOGO, dados)
    return dados


# ==============================================================================
# 2. FUNÇÕES DE AUTOMAÇÃO SELENIUM (O "Cérebro" do Robô)
# ==============================================================================

def pressionar_tecla_ou_atalho(driver, tecla_config):
    """Executa o pressionamento de uma tecla simples (ENTER, TAB) ou combinação (ALT+A, CTRL+S)."""
    actions = ActionChains(driver)
    tecla_clean = str(tecla_config).strip().upper()
    
    teclas_map = {
        "ENTER": Keys.ENTER,
        "TAB": Keys.TAB,
        "ESC": Keys.ESCAPE,
        "ESCAPE": Keys.ESCAPE,
        "SPACE": Keys.SPACE,
        "ESPAÇO": Keys.SPACE,
        "BACKSPACE": Keys.BACKSPACE,
        "DELETE": Keys.DELETE,
        "UP": Keys.ARROW_UP,
        "DOWN": Keys.ARROW_DOWN,
        "LEFT": Keys.ARROW_LEFT,
        "RIGHT": Keys.ARROW_RIGHT
    }
    
    if "+" in tecla_clean:
        partes = [p.strip() for p in tecla_clean.split("+")]
        modificadores = []
        tecla_final = partes[-1]
        
        for mod in partes[:-1]:
            if mod in ["ALT", "ALT_LEFT"]:
                modificadores.append(Keys.ALT)
            elif mod in ["CTRL", "CONTROL"]:
                modificadores.append(Keys.CONTROL)
            elif mod in ["SHIFT"]:
                modificadores.append(Keys.SHIFT)
        
        for m in modificadores:
            actions.key_down(m)
        
        key_code = teclas_map.get(tecla_final, tecla_final.lower())
        actions.send_keys(key_code)
        
        for m in reversed(modificadores):
            actions.key_up(m)
            
        actions.perform()
    else:
        key_code = teclas_map.get(tecla_clean, tecla_clean.lower())
        actions.send_keys(key_code).perform()

def parsear_seletor(sel_str):
    """Recebe um texto de seletor e converte pro padrão do Selenium."""
    sel_str = sel_str.strip()

    if sel_str.startswith("//") or sel_str.startswith("("):
        return By.XPATH, sel_str

    if sel_str.startswith("xpath"):
        return By.XPATH, re.sub(r"^xpath/*", "//", sel_str).replace('\\"', '"')

    if not any(c in sel_str for c in ["/", "[", "]", " ", ">", "~", "+"]):
        clean_id = sel_str.lstrip("#").replace("\\:", ":").replace("\\", "")
        return By.XPATH, f"//*[@id='{clean_id}']"

    if sel_str.startswith("aria/") or sel_str.startswith("text/"):
        clean_txt = sel_str.split("/", 1)[1].split("[")[0].strip()
        if "textbox" in sel_str or "role=\"textbox\"" in sel_str:
            return By.XPATH, f"//input[contains(@title, '{clean_txt}') or contains(@placeholder, '{clean_txt}') or contains(@id, '{clean_txt}')]"
        return By.XPATH, f"//*[contains(text(), '{clean_txt}') or @title='{clean_txt}']"

    if sel_str.startswith("pierce/"):
        sel_str = sel_str.replace("pierce/", "")

    return By.CSS_SELECTOR, sel_str.replace(":", "\\:")

def encontrar_elemento_com_fallback(driver, wait, passo, retentativas=3, intervalo=1.0):
    """Encontra elementos de forma rápida sem travar o script."""
    target_raw = passo.get("seletor_target", "").strip()
    lista_seletores = []

    if target_raw:
        by_type, target = parsear_seletor(target_raw)
        lista_seletores.append((by_type, target))

        if not (target_raw.startswith("//") or target_raw.startswith("(") or target_raw.startswith("xpath")):
            clean_id = target_raw.lstrip("#").replace("\\:", ":")
            if (By.ID, clean_id) not in lista_seletores:
                lista_seletores.append((By.ID, clean_id))

    for item in passo.get("lista_seletores", []):
        if item not in lista_seletores:
            lista_seletores.append(item)

    ultimo_erro = None
    for tentativa in range(retentativas):
        for by_type, target in lista_seletores:
            try:
                elems = driver.find_elements(by_type, target)
                if elems:
                    elem = elems[0]
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                    time.sleep(0.05)
                    return elem
            except Exception as e:
                ultimo_erro = e
                continue

        if lista_seletores:
            by_type, target = lista_seletores[0]
            try:
                elem = WebDriverWait(driver, 1.0).until(EC.presence_of_element_located((by_type, target)))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                return elem
            except Exception as e:
                ultimo_erro = e

        time.sleep(intervalo)

    raise Exception(f"Elemento não localizado ({target_raw}) após {retentativas} tentativa(s). Erro: {ultimo_erro}")

def preencher_campo_sigaa(driver, elem, valor, modo_select="auto"):
    """Preenche campos normais, seleciona dropdowns <select> ou aciona Radio Buttons dinamicamente."""
    val_str = str(valor).strip()
    tag_name = elem.tag_name.lower()
    type_attr = (elem.get_attribute("type") or "").lower()

    if tag_name == "input" and type_attr == "radio":
        radio_name = elem.get_attribute("name")
        radios = driver.find_elements(By.XPATH, f"//input[@type='radio' and @name='{radio_name}']") if radio_name else [elem]
        
        target_to_click = None

        for r in radios:
            r_id = r.get_attribute("id") or ""
            r_val = (r.get_attribute("value") or "").strip()
            
            labels = driver.find_elements(By.XPATH, f"//label[@for='{r_id}']")
            lbl_text = labels[0].text.strip() if labels else ""

            if val_str.lower() == lbl_text.lower() or (val_str and val_str == r_val):
                target_to_click = labels[0] if labels else r
                break
            
            if val_str.isdigit():
                nums = re.findall(r'\d+', lbl_text)
                if val_str in nums:
                    target_to_click = labels[0] if labels else r
                    break
            
            if val_str.lower() in lbl_text.lower():
                target_to_click = labels[0] if labels else r
                break

        if target_to_click:
            clicar_elemento_seguro(driver, target_to_click)
            _disparar_eventos_change(driver, target_to_click)
            return
        else:
            raise Exception(f"Opção de botão rádio '{val_str}' não foi encontrada no grupo.")

    if tag_name == "select":
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        except Exception:
            pass
        
        select_obj = Select(elem)
        
        if modo_select == "numero" or (modo_select == "auto" and val_str.isdigit()):
            val_num = val_str
            for option in select_obj.options:
                opt_text = option.text.strip()
                numeros_no_texto = re.findall(r'\d+', opt_text)
                if val_num in numeros_no_texto:
                    select_obj.select_by_visible_text(opt_text)
                    _disparar_eventos_change(driver, elem)
                    return

        for option in select_obj.options:
            opt_text = option.text.strip()
            if val_str.lower() == opt_text.lower():
                select_obj.select_by_visible_text(opt_text)
                _disparar_eventos_change(driver, elem)
                return

        for option in select_obj.options:
            opt_text = option.text.strip()
            if opt_text and val_str.lower() in opt_text.lower():
                select_obj.select_by_visible_text(opt_text)
                _disparar_eventos_change(driver, elem)
                return

        raise Exception(f"Opção correspondente a '{val_str}' não foi encontrada no campo de seleção.")

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        driver.execute_script("arguments[0].focus(); arguments[0].click();", elem)
        time.sleep(0.1)
    except Exception:
        pass

    try:
        elem.send_keys(Keys.CONTROL + "a")
        elem.send_keys(Keys.BACKSPACE)
        time.sleep(0.05)
    except Exception:
        driver.execute_script("arguments[0].value = '';", elem)

    try:
        for char in val_str:
            elem.send_keys(char)
            time.sleep(0.02)
    except Exception:
        driver.execute_script("arguments[0].value = arguments[1];", elem, val_str)

    _disparar_eventos_change(driver, elem)

def _disparar_eventos_change(driver, elem):
    """Dispara eventos JS obrigatórios para o JSF do SIGAA reconhecer a alteração."""
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
    """Garante o clique no elemento via Selenium ou JavaScript."""
    try:
        elem.click()
    except Exception:
        driver.execute_script("arguments[0].click();", elem)

def aguardar_login_manual(driver, url_login_ambiente, timeout_segundos=120):
    """Navega para a URL e aguarda o usuário realizar o login."""
    try:
        url_atual = driver.current_url
    except Exception:
        url_atual = ""

    if not url_atual or "data:" in url_atual or "about:blank" in url_atual:
        driver.get(url_login_ambiente)
        try:
            alert = driver.switch_to.alert
            alert.accept()
        except Exception:
            pass

    tempo_inicial = time.time()
    with st.spinner("🔑 Faça o login no Chrome para continuar..."):
        while time.time() - tempo_inicial < timeout_segundos:
            try:
                elementos_sessao_ativa = driver.find_elements(
                    By.XPATH, 
                    "//a[contains(@href, 'dispatch=logOff')] | //a[contains(@href, 'logoff')] | //div[@id='info-usuario'] | //div[@id='menu_principal']"
                )
                if len(elementos_sessao_ativa) > 0:
                    st.success("✅ Login e sessão ativa confirmados!")
                    return True
            except Exception:
                pass
            time.sleep(1)
            
    raise Exception("Tempo limite para login esgotado ou sessão não identificada.")

def normalizar_texto(texto):
    """Limpa textos HTML para comparação."""
    if not texto:
        return ""
    texto_desescapado = html.unescape(texto)
    return re.sub(r'\s+', ' ', texto_desescapado).strip().lower()

def validar_texto_na_tela(driver, texto_esperado, timeout=5, retentativas=3, intervalo=1.0):
    """Procura uma frase exata na página."""
    texto_esperado_norm = normalizar_texto(texto_esperado)
    if not texto_esperado_norm:
        return True

    for tentativa in range(retentativas):
        tempo_inicial = time.time()
        while time.time() - tempo_inicial < timeout:
            try:
                body_elem = driver.find_element(By.TAG_NAME, "body")
                body_texto_norm = normalizar_texto(body_elem.text)
                if texto_esperado_norm in body_texto_norm:
                    return True

                page_source_norm = normalizar_texto(driver.page_source)
                if texto_esperado_norm in page_source_norm:
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        time.sleep(intervalo)
    return False

def mapear_tela_atual_para_json(driver, nome_da_tela):
    """Captura os botões, campos, dropdowns, radio buttons e links da página atual."""
    elementos = driver.find_elements(
        By.XPATH, 
        "//input[not(@type='hidden')] | //textarea | //button | //select | //a[text()] | //a[@title] | //a[@href]"
    )
    
    mapa_tela = {}
    ruidos_ignorar = [
        "ufgnet", "aumentar a texto", "diminuir o texto", "acessibilidade", 
        "ir para o conteúdo", "ir para o menu", "ir para a busca", "sair", "ajuda"
    ]
    
    for elem in elementos:
        try:
            id_attr = elem.get_attribute("id") or ""
            name_attr = elem.get_attribute("name") or ""
            title_attr = elem.get_attribute("title") or ""
            texto_visivel = elem.text.strip()
            
            tag_name = elem.tag_name.lower()
            tipo_attr = (elem.get_attribute("type") or "").lower()
            value_attr = elem.get_attribute("value") or ""
            
            if "yuievtautoid" in id_attr.lower():
                continue

            if tag_name == "input" and tipo_attr == "radio":
                chave_grupo = f"radio_group_marker_{name_attr}"
                if name_attr and chave_grupo in mapa_tela:
                    continue
                
                radios_grupo = driver.find_elements(By.XPATH, f"//input[@type='radio' and @name='{name_attr}']") if name_attr else [elem]
                opcoes_radio = []
                textos_radio = []

                for r in radios_grupo:
                    r_id = r.get_attribute("id") or ""
                    r_val = (r.get_attribute("value") or "").strip()
                    lbls = driver.find_elements(By.XPATH, f"//label[@for='{r_id}']")
                    lbl_txt = lbls[0].text.strip() if lbls else ""
                    if lbl_txt or r_val:
                        opcoes_radio.append({"texto": lbl_txt, "valor": r_val})
                        if lbl_txt:
                            textos_radio.append(lbl_txt)

                if opcoes_radio:
                    primeiro_txt = textos_radio[0] if textos_radio else "opcao"
                    nome_chave = re.sub(r'[^\w\s]', '', primeiro_txt).strip().lower().replace(" ", "_")
                    if not nome_chave.startswith("opcao_") and not nome_chave.startswith("radio_"):
                        nome_chave = f"opcao_{nome_chave}"
                    
                    target_xpath = f"//input[@type='radio' and @name='{name_attr}']" if name_attr else f"//*[@id='{id_attr}']"

                    mapa_tela[nome_chave] = {
                        "by": "xpath",
                        "target": target_xpath,
                        "nome_original": nome_chave,
                        "is_radio": True,
                        "opcoes": opcoes_radio
                    }
                    if name_attr:
                        mapa_tela[chave_grupo] = True
                    continue

            if tag_name == "select":
                try:
                    opts = elem.find_elements(By.TAG_NAME, "option")
                    opcoes_detalhadas = []
                    textos_validos = []
                    
                    for o in opts:
                        txt_opt = o.text.strip()
                        val_opt = (o.get_attribute("value") or "").strip()
                        if txt_opt or val_opt:
                            opcoes_detalhadas.append({"texto": txt_opt, "valor": val_opt})
                            if txt_opt:
                                textos_validos.append(txt_opt)
                    
                    if opcoes_detalhadas:
                        nome_chave = ""
                        if title_attr:
                            nome_chave = title_attr.lower().replace(" ", "_")
                        elif id_attr and not id_attr.startswith("j_id"):
                            nome_chave = id_attr.split(":")[-1]
                        
                        if not nome_chave:
                            amostra = "_".join([re.sub(r'[^\w\s]', '', t).strip().lower().replace(" ", "_") for t in textos_validos[:2]])
                            nome_chave = f"selecionar_{amostra}" if amostra else "selecionar_opcao"

                        nome_chave = re.sub(r'[^\w\s]', '', nome_chave).strip().lower().replace(" ", "_")
                        
                        primeiro_txt = textos_validos[0] if textos_validos else ""
                        target_xpath = f"//select[@id='{id_attr}']" if id_attr and not id_attr.startswith("j_id") else f"//select[option[contains(text(), '{primeiro_txt}')]]"

                        mapa_tela[nome_chave] = {
                            "by": "xpath",
                            "target": target_xpath,
                            "nome_original": nome_chave,
                            "is_select": True,
                            "opcoes": opcoes_detalhadas
                        }
                        continue
                except Exception:
                    pass
            
            if not texto_visivel and tag_name == "input" and tipo_attr in ["submit", "button"] and value_attr:
                texto_visivel = value_attr.strip()
                
            if texto_visivel and len(texto_visivel) > 1:
                if texto_visivel.lower() in ruidos_ignorar:
                    continue
                nome_chave = re.sub(r'[^\w\s]', '', texto_visivel).strip().lower().replace(" ", "_")
                
                if tag_name == "input" and tipo_attr in ["submit", "button"]:
                    mapa_tela[nome_chave] = {
                        "by": "xpath",
                        "target": f"//input[@value='{texto_visivel}']",
                        "nome_original": nome_chave
                    }
                else:
                    mapa_tela[nome_chave] = {
                        "by": "xpath",
                        "target": f"//*[contains(text(), '{texto_visivel}')]",
                        "nome_original": nome_chave
                    }
                    
            elif id_attr and not id_attr.startswith("j_id"):
                nome_chave = id_attr.split(":")[-1]
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//*[@id='{id_attr}']",
                    "nome_original": nome_chave
                }
            elif title_attr and title_attr.lower() not in ruidos_ignorar:
                nome_chave = title_attr.lower().replace(" ", "_")
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//*[@title='{title_attr}']",
                    "nome_original": nome_chave
                }
            elif name_attr:
                nome_chave = name_attr.split(":")[-1]
                nome_chave = re.sub(r'[^\w\s]', '', nome_chave).strip().lower()
                mapa_tela[nome_chave] = {
                    "by": "xpath",
                    "target": f"//*[@name='{name_attr}']",
                    "nome_original": nome_chave
                }
        except Exception:
            continue

    mapa_tela_final = {k: v for k, v in mapa_tela.items() if v is not True}
    return {nome_da_tela: mapa_tela_final}


# ==============================================================================
# 3. GERENCIAMENTO DO NAVEGADOR (CHROME)
# ==============================================================================

def obter_driver_ativo():
    """Checa se o navegador Chrome já está aberto."""
    if "driver" in st.session_state and st.session_state.driver is not None:
        try:
            _ = st.session_state.driver.current_url
            return st.session_state.driver
        except Exception:
            st.session_state.driver = None
    return None

def iniciar_navegador_universal():
    """Abre uma nova janela do Chrome."""
    driver_ativo = obter_driver_ativo()
    if driver_ativo:
        return driver_ativo

    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    st.session_state.driver = driver
    return driver

def fechar_navegador_universal():
    """Encerra a instância do Chrome."""
    if "driver" in st.session_state and st.session_state.driver is not None:
        try:
            st.session_state.driver.quit()
        except Exception:
            pass
        st.session_state.driver = None


# ==============================================================================
# 4. BARRA LATERAL (CONFIGURAÇÃO DE SERVIDOR)
# ==============================================================================

st.sidebar.header("⚙️ Servidor do SIGAA")

opcao_servidor = st.sidebar.selectbox(
    "Escolha o Ambiente:",
    ["UFG Produção", "Outro Servidor"]
)

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

if driver_status:
    st.sidebar.success("🟢 Navegador Aberto e Conectado")
    if st.sidebar.button("❌ Fechar Navegador", width="stretch"):
        fechar_navegador_universal()
        st.rerun()
else:
    st.sidebar.warning("🔴 Navegador Desconectado")
    if st.sidebar.button("🌐 Abrir / Logar no Chrome", width="stretch"):
        driver = iniciar_navegador_universal()
        try:
            aguardar_login_manual(driver, URL_LOGIN_CONFIG)
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao abrir navegador: {e}")


# ==============================================================================
# 5. INTERFACE PRINCIPAL DIVIDIDA EM 4 ABAS
# ==============================================================================

tab1, tab2, tab3, tab4 = st.tabs([
    "🚀 Editor e Executor do Workflow", 
    "🔍 Mapeador de Telas", 
    "📝 Textos Padrão", 
    "🗂️ Gerenciar Catálogo"
])

# ------------------------------------------------------------------------------
# ABA 1: CONSTRUTOR DE WORKFLOW E EXECUÇÃO
# ------------------------------------------------------------------------------
with tab1:
    st.subheader("🛠️ Construtor e Executor de Workflows")

    col_exp1, col_exp2, col_exp3 = st.columns([2, 2, 1])
    
    with col_exp1:
        file_import_wf = st.file_uploader("📂 Importar Workflow Salvo (.json)", type=["json"], key="import_wf_json")
        if file_import_wf is not None:
            if "last_wf_name" not in st.session_state or st.session_state.last_wf_name != file_import_wf.name:
                try:
                    loaded_steps = json.load(file_import_wf)
                    st.session_state.steps = loaded_steps
                    st.session_state.last_wf_name = file_import_wf.name
                    
                    st.session_state.planilhas_disponiveis = {}
                    for step in loaded_steps:
                        if step.get("acao") == "Iniciar Loop" and "planilhas" in step:
                            st.session_state.planilhas_disponiveis.update(step["planilhas"])
                            
                    st.success("Workflow importado com sucesso!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao carregar workflow: {e}")

    with col_exp2:
        st.write("")
        st.write("")
        if st.session_state.steps:
            json_export = json.dumps(st.session_state.steps, indent=2, ensure_ascii=False)
            st.download_button(
                label="💾 Exportar Workflow (.json)",
                data=json_export,
                file_name="workflow_sigaa.json",
                mime="application/json",
                width="stretch"
            )

    with col_exp3:
        st.write("")
        st.write("")
        if st.button("🗑️ Limpar Passos", width="stretch"):
            st.session_state.steps = []
            st.session_state.planilhas_disponiveis = {}
            st.rerun()

    st.divider()

    with st.expander("➕ Adicionar Novo Passo ao Workflow", expanded=True):
        catalogo = carregar_json_local(ARQUIVO_CATALOGO)
        
        col_pos1, col_pos2 = st.columns([2, 2])
        with col_pos1:
            tipo_passo = st.selectbox("Tipo de Ação:", ["Clicar", "Preencher", "Pressionar Tecla", "Navegar", "Validar Texto", "🔁 Iniciar Loop", "🔚 Fim de Loop"], key="add_tipo_passo")
        with col_pos2:
            total_p = len(st.session_state.steps)
            opcoes_posicao = ["No final do workflow"] + [f"Antes do Passo {i+1}" for i in range(total_p)] + [f"Depois do Passo {i+1}" for i in range(total_p)]
            posicao_escolhida = st.selectbox("Posição de Inserção:", opcoes_posicao, key="add_posicao_escolhida")

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

        if tipo_passo in ["Clicar", "Preencher"]:
            origem_seletor = st.radio("Origem do Elemento:", ["Usar Catálogo Mapeado (Recomendado)", "Digitar Seletor Manualmente"], horizontal=True, key="add_origem_seletor")
            
            target_add = ""
            by_add = "xpath"
            nome_exibicao = ""
            dados_elem_sel = {}

            if origem_seletor == "Usar Catálogo Mapeado (Recomendado)" and catalogo:
                col_h1, col_h2, col_h3 = st.columns(3)
                with col_h1:
                    modulos_disponiveis = list(catalogo.keys())
                    mod_sel = st.selectbox("1. Módulo Hierárquico:", modulos_disponiveis, key="add_mod_sel")
                with col_h2:
                    telas_disponiveis = list(catalogo.get(mod_sel, {}).keys())
                    tela_sel = st.selectbox("2. Tela / Seção:", telas_disponiveis, key="add_tela_sel")
                with col_h3:
                    elementos_brutos = catalogo.get(mod_sel, {}).get(tela_sel, {})
                    elementos_ativos = {k: v for k, v in elementos_brutos.items() if v.get("ativo", True)}
                    elem_sel = st.selectbox("3. Elemento:", list(elementos_ativos.keys()), key="add_elem_sel")

                if elem_sel in elementos_ativos:
                    dados_elem_sel = elementos_ativos[elem_sel]
                    target_add = dados_elem_sel["target"]
                    by_add = dados_elem_sel["by"]
                    nome_exibicao = f"{mod_sel} -> {tela_sel} -> {elem_sel}"
                    st.caption(f"🎯 Seletor extraído do catálogo: `{target_add}` (Tipo: `{by_add}`)")

            else:
                target_add = st.text_input("Seletor do Elemento (ID ou XPath):", value="formulario:matriculaDiscente", key="add_target_manual")
                by_add = "xpath"
                nome_exibicao = target_add

            col_p1, col_p2, col_p3 = st.columns(3)
            with col_p1:
                tempo_pausa_add = st.number_input("Pausa após a ação (s):", min_value=0.0, value=0.5, step=0.5, key="add_tempo_pausa")
            with col_p2:
                retentativas_add = st.number_input("Re-tentativas:", min_value=1, value=3, step=1, key="add_retentativas")
            with col_p3:
                intervalo_add = st.number_input("Intervalo re-tentativas (s):", min_value=0.5, value=0.5, step=0.5, key="add_intervalo")

            valor_add = ""
            usar_loop_val = False
            planilha_ref = ""
            coluna_ref = ""
            modo_select_add = "auto"

            if tipo_passo == "Preencher":
                is_radio = dados_elem_sel.get("is_radio", False)
                is_dropdown = dados_elem_sel.get("is_select", False) or bool(dados_elem_sel.get("opcoes"))

                if is_radio:
                    st.info("🔘 **Campo de Opções (Radio Buttons):** O robô acionará a opção dinamicamente com base no texto enviado (ex: `'NÚCLEO COMUM OBRIGATÓRIO'`) ou no número do atributo/posição (ex: `'1'` ou `'4'`).")
                    if dados_elem_sel.get("opcoes"):
                        opts_str = ", ".join([f"`{o['texto']}`" for o in dados_elem_sel["opcoes"] if o['texto']])
                        st.caption(f"Opções disponíveis neste grupo: {opts_str}")
                elif is_dropdown:
                    st.caption("💡 **Campo Dropdown (<select>):** Você pode digitar/enviar o número da opção (ex: `1` para `1º Per.`) ou o texto exato (ex: `ATIVO`).")

                usar_loop_val = st.checkbox("Usar valor de Planilha do Loop", value=True, key="add_usar_loop_val")
                
                if usar_loop_val:
                    if st.session_state.planilhas_disponiveis:
                        col_pl1, col_pl2 = st.columns(2)
                        with col_pl1:
                            planilha_ref = st.selectbox("Selecione a Planilha:", list(st.session_state.planilhas_disponiveis.keys()), key="add_planilha_ref")
                        with col_pl2:
                            colunas_disp = list(st.session_state.planilhas_disponiveis[planilha_ref].keys())
                            coluna_ref = st.selectbox("Selecione a Coluna:", colunas_disp, key="add_coluna_ref")
                            
                        if is_dropdown:
                            modo_select_opt = st.radio(
                                "Modo de Mapeamento do Dropdown para a Planilha:",
                                ["Mapear por Número (ex: 1 -> busca '1º Per.')", "Texto Visível Exato (ex: ATIVO)"],
                                horizontal=True,
                                key="add_modo_select_radio"
                            )
                            modo_select_add = "numero" if "Número" in modo_select_opt else "texto"
                    else:
                        st.warning("Adicione um '🔁 Iniciar Loop' com planilhas para habilitar a seleção de colunas.")
                else:
                    valor_add = st.text_input("Valor fixo a preencher/acionar:", key="add_valor_fixo", help="Digite o texto exato da opção desejada ou o número.")

            if st.button(f"➕ Adicionar {tipo_passo} ao Workflow", type="primary"):
                if target_add:
                    novo_step = {
                        "acao": tipo_passo,
                        "seletor_target": target_add,
                        "seletor_by": by_add,
                        "rotulo": nome_exibicao,
                        "tempo_espera": tempo_pausa_add,
                        "retentativas": retentativas_add,
                        "intervalo_retentativa": intervalo_add,
                        "opcoes_select": dados_elem_sel.get("opcoes", []),
                        "is_select": dados_elem_sel.get("is_select", False),
                        "is_radio": dados_elem_sel.get("is_radio", False),
                        "modo_select": modo_select_add
                    }
                    if tipo_passo == "Preencher":
                        novo_step["valor"] = valor_add
                        novo_step["usar_loop"] = usar_loop_val
                        novo_step["planilha_ref"] = planilha_ref
                        novo_step["coluna_ref"] = coluna_ref
                        
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, novo_step)
                    st.success(f"Passo '{tipo_passo}' inserido na posição {idx_ins + 1}!")
                    st.rerun()

        elif tipo_passo == "Pressionar Tecla":
            col_k1, col_k2, col_k3 = st.columns(3)
            with col_k1:
                tipo_tecla_opt = st.selectbox(
                    "Tecla ou Atalho:", 
                    ["ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "UP", "DOWN", "Digitar Atalho Customizado..."],
                    key="add_tipo_tecla_opt"
                )
                if tipo_tecla_opt == "Digitar Atalho Customizado...":
                    tecla_final_add = st.text_input("Atalho (ex: ALT+A, CTRL+S, SHIFT+TAB):", value="ALT+A", key="add_tecla_custom")
                else:
                    tecla_final_add = tipo_tecla_opt

            with col_k2:
                usar_loop_tecla = st.checkbox("Usar Atalho da Planilha do Loop", value=False, key="add_usar_loop_tecla")
                planilha_ref_k = ""
                coluna_ref_k = ""
                if usar_loop_tecla:
                    if st.session_state.planilhas_disponiveis:
                        planilha_ref_k = st.selectbox("Planilha:", list(st.session_state.planilhas_disponiveis.keys()), key="add_planilha_ref_k")
                        coluna_ref_k = st.selectbox("Coluna:", list(st.session_state.planilhas_disponiveis[planilha_ref_k].keys()), key="add_coluna_ref_k")

            with col_k3:
                tempo_pausa_add = st.number_input("Pausa após acionar (s):", min_value=0.0, value=0.5, step=0.5, key="add_pausa_tecla")

            if st.button("➕ Adicionar Pressionamento de Tecla", type="primary"):
                idx_ins = calcular_indice_insercao()
                st.session_state.steps.insert(idx_ins, {
                    "acao": "Pressionar Tecla",
                    "tecla": tecla_final_add,
                    "usar_loop": usar_loop_tecla,
                    "planilha_ref": planilha_ref_k,
                    "coluna_ref": coluna_ref_k,
                    "tempo_espera": tempo_pausa_add
                })
                st.success("Passo 'Pressionar Tecla' inserido!")
                st.rerun()

        elif tipo_passo == "Navegar":
            col_nav1, col_nav2 = st.columns([3, 1])
            with col_nav1:
                url_nav_add = st.text_input("URL para onde navegar:", value="", key="add_url_nav")
            with col_nav2:
                tempo_pausa_add = st.number_input("Pausa após navegar (s):", min_value=0.0, value=1.0, step=0.5, key="add_pausa_nav")
            
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

        elif tipo_passo == "Validar Texto":
            textos_cadastrados = carregar_json_local(ARQUIVO_TEXTOS)
            opcoes = list(textos_cadastrados.keys())
            if opcoes:
                col_v1, col_v2, col_v3 = st.columns(3)
                with col_v1:
                    txt_sel = st.selectbox("Escolha o Texto Padrão de Validação:", opcoes, key="add_txt_sel")
                    condicao_val = st.selectbox(
                        "Regra de Interrupção:", 
                        ["Parar se NÃO encontrar na tela", "Parar se ENCONTRAR na tela (Detector de Erro)"],
                        key="add_condicao_val"
                    )
                with col_v2:
                    timeout_val = st.number_input("Timeout busca (s):", min_value=1, value=5, key="add_timeout_val")
                    tempo_pausa_add = st.number_input("Pausa pós-validação (s):", min_value=0.0, value=0.5, step=0.5, key="add_pausa_val")
                with col_v3:
                    retentativas_add = st.number_input("Re-tentativas do texto:", min_value=1, value=3, step=1, key="add_retent_val")
                    intervalo_add = st.number_input("Intervalo entre buscas (s):", min_value=0.5, value=0.5, step=0.5, key="add_interv_val")
                
                if st.button("Adicionar Validação"):
                    idx_ins = calcular_indice_insercao()
                    st.session_state.steps.insert(idx_ins, {
                        "acao": "Validar Texto",
                        "texto": textos_cadastrados[txt_sel],
                        "rotulo": txt_sel,
                        "condicao": condicao_val,
                        "timeout": timeout_val,
                        "tempo_espera": tempo_pausa_add,
                        "retentativas": retentativas_add,
                        "intervalo_retentativa": intervalo_add
                    })
                    st.rerun()
            else:
                st.warning("Cadastre textos padrão na Aba 3 primeiro.")

        elif tipo_passo == "🔁 Iniciar Loop":
            st.markdown("Envie **uma ou mais planilhas** (.xlsx, .csv) para associar a este ciclo de repetição:")
            files_loop = st.file_uploader("Planilhas (.xlsx, .csv):", type=["xlsx", "xls", "csv"], accept_multiple_files=True, key="up_multi_planilhas")
            
            if files_loop:
                planilhas_processadas = {}
                max_linhas = 0

                for f in files_loop:
                    df = pd.read_csv(f) if f.name.endswith(".csv") else pd.read_excel(f)
                    planilhas_processadas[f.name] = df.astype(str).to_dict(orient="list")
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

        elif tipo_passo == "🔚 Fim de Loop":
            if st.button("Adicionar Fim de Loop"):
                idx_ins = calcular_indice_insercao()
                st.session_state.steps.insert(idx_ins, {"acao": "Fim de Loop"})
                st.rerun()

    st.subheader(f"📋 Passos do Workflow ({len(st.session_state.steps)} etapas)")

    for idx, step in enumerate(st.session_state.steps):
        acao = step["acao"]
        
        if acao == "Iniciar Loop":
            planilhas_no_step = step.get("planilhas", {})
            m_lin = step.get("max_linhas", 0)
            nomes_planilhas_loop = list(planilhas_no_step.keys())
            
            passos_dependentes = [
                i + 1 for i, s in enumerate(st.session_state.steps)
                if s.get("acao") == "Preencher" 
                and s.get("usar_loop", True) 
                and s.get("planilha_ref") in nomes_planilhas_loop
            ]
            
            col_lk1, col_lk2, col_lk3 = st.columns([4, 1.2, 0.8])
            with col_lk1:
                st.warning(f"🔁 **Passo {idx + 1}: Iniciar Loop** ({len(planilhas_no_step)} planilha(s) vinculada(s) - {m_lin} linhas)")
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
            with col_lk3:
                if st.button("🗑️ Excluir", key=f"del_loop_{idx}"):
                    if passos_dependentes:
                        st.error(f"⛔ Altere ou exclua primeiro o(s) passo(s) dependente(s): {passos_dependentes}")
                    else:
                        st.session_state.steps.pop(idx)
                        st.session_state.planilhas_disponiveis = {}
                        for s in st.session_state.steps:
                            if s.get("acao") == "Iniciar Loop" and "planilhas" in s:
                                st.session_state.planilhas_disponiveis.update(s["planilhas"])
                        st.rerun()
            
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

        elif acao == "Fim de Loop":
            col_lk1, col_lk2, col_lk3 = st.columns([4, 1.2, 0.8])
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
            with col_lk3:
                if st.button("🗑️ Excluir", key=f"del_end_loop_{idx}"):
                    st.session_state.steps.pop(idx)
                    st.rerun()

        else:
            rotulo_card = step.get('rotulo') or step.get('seletor_target') or step.get('detalhe') or step.get('tecla', '')
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
                    col_s1, col_s2 = st.columns(2)
                    with col_s1:
                        step["tempo_espera"] = st.number_input(f"Pausa após ação (s)", value=float(step.get("tempo_espera", 0.5)), min_value=0.0, step=0.5, key=f"pausa_{idx}")
                    with col_s2:
                        step["retentativas"] = st.number_input(f"Re-tentativas", value=int(step.get("retentativas", 3)), min_value=1, step=1, key=f"ret_{idx}")

                    if acao == "Navegar":
                        step["detalhe"] = st.text_input(f"URL de Destino", value=step.get("detalhe", ""), key=f"url_{idx}")
                    elif acao == "Pressionar Tecla":
                        step["tecla"] = st.text_input("Tecla/Atalho a Pressionar (ex: ENTER, ALT+A):", value=step.get("tecla", "ENTER"), key=f"tec_{idx}")
                        step["usar_loop"] = st.checkbox("Usar atalho da Planilha do Loop", value=step.get("usar_loop", False), key=f"chk_k_{idx}")
                        if step["usar_loop"] and st.session_state.planilhas_disponiveis:
                            col_pl1, col_pl2 = st.columns(2)
                            planilhas_list = list(st.session_state.planilhas_disponiveis.keys())
                            p_index = planilhas_list.index(step.get("planilha_ref")) if step.get("planilha_ref") in planilhas_list else 0
                            with col_pl1:
                                step["planilha_ref"] = st.selectbox("Planilha Alvo:", planilhas_list, index=p_index, key=f"plan_ref_k_{idx}")
                            with col_pl2:
                                colunas_list = list(st.session_state.planilhas_disponiveis[step["planilha_ref"]].keys())
                                c_index = colunas_list.index(step.get("coluna_ref")) if step.get("coluna_ref") in colunas_list else 0
                                step["coluna_ref"] = st.selectbox("Coluna Alvo:", colunas_list, index=c_index, key=f"col_ref_k_{idx}")

                    elif acao == "Preencher":
                        step["seletor_target"] = st.text_input(f"Seletor", value=step.get("seletor_target", ""), key=f"sel_{idx}")
                        
                        is_card_dropdown = step.get("is_select", False) or bool(step.get("opcoes_select"))
                        is_card_radio = step.get("is_radio", False)

                        if is_card_radio:
                            st.caption("🔘 **Opções deste Grupo de Radio:** " + ", ".join([f"`{o['texto']}`" for o in step.get("opcoes_select", []) if o['texto']]))
                        elif is_card_dropdown:
                            st.caption("💡 **Campo Dropdown:** Aceita números (ex: `1` $\\rightarrow$ `1º Per.`) ou o texto exato.")

                        step["usar_loop"] = st.checkbox("Usar valor de Planilha do Loop", value=step.get("usar_loop", True), key=f"chk_loop_{idx}")
                        
                        if step["usar_loop"]:
                            if st.session_state.planilhas_disponiveis:
                                col_pl1, col_pl2 = st.columns(2)
                                planilhas_list = list(st.session_state.planilhas_disponiveis.keys())
                                p_index = planilhas_list.index(step.get("planilha_ref")) if step.get("planilha_ref") in planilhas_list else 0
                                
                                with col_pl1:
                                    step["planilha_ref"] = st.selectbox("Planilha Alvo:", planilhas_list, index=p_index, key=f"plan_ref_{idx}")
                                
                                with col_pl2:
                                    colunas_list = list(st.session_state.planilhas_disponiveis[step["planilha_ref"]].keys())
                                    c_index = colunas_list.index(step.get("coluna_ref")) if step.get("coluna_ref") in colunas_list else 0
                                    step["coluna_ref"] = st.selectbox("Coluna Alvo:", colunas_list, index=c_index, key=f"col_ref_{idx}")

                                if is_card_dropdown:
                                    modo_atual = step.get("modo_select", "auto")
                                    idx_modo = 0 if modo_atual == "numero" else 1
                                    modo_opt_card = st.radio(
                                        "Tipo de Dado na Coluna da Planilha:",
                                        ["Número (ex: 1 -> busca '1º Per.')", "Texto Visível Exato (ex: ATIVO)"],
                                        index=idx_modo,
                                        horizontal=True,
                                        key=f"modo_sel_card_{idx}"
                                    )
                                    step["modo_select"] = "numero" if "Número" in modo_opt_card else "texto"
                            else:
                                st.warning("⚠️ Adicione ou re-vincule planilhas no passo 'Iniciar Loop' para selecionar as colunas.")
                        else:
                            step["valor"] = st.text_input("Valor Fixo", value=step.get("valor", ""), key=f"val_{idx}")
                                
                    elif acao == "Clicar":
                        step["seletor_target"] = st.text_input(f"Seletor", value=step.get("seletor_target", ""), key=f"sel_{idx}")
                    elif acao == "Validar Texto":
                        st.write(f"Texto Esperado: `{step.get('texto')}`")
                        st.caption(f"Regra: `{step.get('condicao', 'Parar se NÃO encontrar na tela')}`")
                        step["timeout"] = st.number_input(f"Timeout Busca (s)", value=step.get("timeout", 5), key=f"tout_{idx}")

                with col3:
                    if st.button("🗑️ Excluir", key=f"del_{idx}"):
                        st.session_state.steps.pop(idx)
                        st.rerun()

    st.divider()

    st.subheader("🚀 Execução da Automação")

    if st.button("🚀 Executar Workflow no Chrome", type="primary", width="stretch"):
        if not st.session_state.steps:
            st.error("Adicione passos ao workflow antes de executar.")
        else:
            driver = obter_driver_ativo()
            if not driver:
                st.info("Iniciando navegador...")
                driver = iniciar_navegador_universal()

            wait = WebDriverWait(driver, 10)
            checkpoint_dados = carregar_json_local(ARQUIVO_CHECKPOINT)

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
                aguardar_login_manual(driver, URL_LOGIN_CONFIG)

                st.info(f"📍 Executando workflow a partir da página atual: `{driver.current_url}`")

                interrompido = False
                step_idx = 0
                total_steps = len(st.session_state.steps)

                while step_idx < total_steps and not interrompido:
                    step = st.session_state.steps[step_idx]
                    acao = step["acao"]
                    tempo_pausa = float(step.get("tempo_espera", 0.5))
                    n_retentativas = int(step.get("retentativas", 3))
                    t_intervalo = float(step.get("intervalo_retentativa", 0.5))

                    if acao == "Navegar":
                        st.write(f"▶️ Navegando para: `{step['detalhe']}`")
                        try:
                            driver.get(step["detalhe"])
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_nav_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Erro ao navegar para `{step['detalhe']}`.")
                            interrompido = True

                    elif acao == "Pressionar Tecla" and not step.get("usar_loop", False):
                        st.write(f"▶️ Pressionando tecla/atalho: `{step['tecla']}`")
                        try:
                            pressionar_tecla_ou_atalho(driver, step["tecla"])
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_key_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Erro ao pressionar tecla `{step['tecla']}`: {e_key_out}")
                            interrompido = True

                    elif acao == "Clicar":
                        st.write(f"▶️ Clicando em: `{step.get('rotulo', step['seletor_target'])}`")
                        try:
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
                            preencher_campo_sigaa(driver, elem, step["valor"], modo_select=step.get("modo_select", "auto"))
                            time.sleep(tempo_pausa)
                            step_idx += 1
                        except Exception as e_pr_out:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** {e_pr_out}")
                            interrompido = True

                    elif acao == "Validar Texto" and not step.get("no_loop", False):
                        condicao_regra = step.get("condicao", "Parar se NÃO encontrar na tela")
                        st.write(f"▶️ Verificando texto: '{step['texto']}' (Regra: {condicao_regra})")
                        encontrou = validar_texto_na_tela(driver, step['texto'], timeout=step.get('timeout', 5), retentativas=n_retentativas, intervalo=t_intervalo)
                        
                        if "ENCONTRAR" in condicao_regra and encontrou:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Texto de alerta/erro '{step['texto']}' foi localizado na tela.")
                            interrompido = True
                        elif "NÃO encontrar" in condicao_regra and not encontrou:
                            st.error(f"⛔ **Interrupção no Passo {step_idx + 1}:** Texto esperado '{step['texto']}' não foi localizado na tela.")
                            interrompido = True
                        else:
                            st.success(f"✅ Validação de texto concluída com sucesso: '{step['texto']}'")
                            time.sleep(tempo_pausa)
                            step_idx += 1

                    elif acao == "Iniciar Loop":
                        planilhas_loop = step.get("planilhas", {})
                        max_lin = step.get("max_linhas", 0)
                        
                        nome_key_checkpoint = "_".join(list(planilhas_loop.keys())) or "loop_generico"

                        dados_salvos = checkpoint_dados.get(nome_key_checkpoint, {})
                        if isinstance(dados_salvos, dict):
                            linha_checkpoint = dados_salvos.get("proxima_linha_index", 0)
                        else:
                            try:
                                linha_checkpoint = int(dados_salvos)
                            except Exception:
                                linha_checkpoint = 0

                        end_loop_idx = step_idx + 1
                        while end_loop_idx < total_steps and st.session_state.steps[end_loop_idx]["acao"] != "Fim de Loop":
                            end_loop_idx += 1

                        st.info(f"🔄 **Iniciando Loop ({max_lin} linhas). Retomando da linha {linha_checkpoint + 1}...**")

                        for i_item in range(linha_checkpoint, max_lin):
                            st.write(f"🔁 **Processando Linha {i_item + 1}/{max_lin}**")

                            for inner_idx in range(step_idx + 1, end_loop_idx):
                                inner_step = st.session_state.steps[inner_idx]
                                inner_acao = inner_step["acao"]
                                inner_pausa = float(inner_step.get("tempo_espera", 0.5))
                                inner_ret = int(inner_step.get("retentativas", 3))
                                inner_inter = float(inner_step.get("intervalo_retentativa", 0.5))

                                valor_atual_acao = ""
                                planilha_atual = inner_step.get("planilha_ref", "")
                                coluna_atual = inner_step.get("coluna_ref", "")

                                if inner_step.get("usar_loop", True) and planilha_atual and coluna_atual:
                                    try:
                                        valor_atual_acao = planilhas_loop[planilha_atual][coluna_atual][i_item]
                                    except Exception:
                                        valor_atual_acao = "N/A"
                                else:
                                    valor_atual_acao = inner_step.get("valor", inner_step.get("detalhe", inner_step.get("texto", inner_step.get("tecla", ""))))

                                rotulo_p = inner_step.get("rotulo", inner_step.get("seletor_target", inner_step.get("tecla", "")))

                                if inner_acao == "Navegar":
                                    try:
                                        driver.get(inner_step["detalhe"])
                                    except Exception as e_nav:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_nav)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Erro ao navegar.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

                                elif inner_acao == "Pressionar Tecla":
                                    try:
                                        pressionar_tecla_ou_atalho(driver, valor_atual_acao)
                                    except Exception as e_k:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_k)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Falha ao pressionar tecla `{valor_atual_acao}`.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

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

                                elif inner_acao == "Preencher":
                                    try:
                                        elem = encontrar_elemento_com_fallback(driver, wait, inner_step, retentativas=inner_ret, intervalo=inner_inter)
                                        preencher_campo_sigaa(driver, elem, valor_atual_acao, modo_select=inner_step.get("modo_select", "auto"))
                                    except Exception as e_pr:
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, e_pr)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** Falha ao preencher o campo com valor `{valor_atual_acao}`.")
                                        interrompido = True
                                        break
                                    time.sleep(inner_pausa)

                                elif inner_acao == "Validar Texto":
                                    condicao_regra = inner_step.get("condicao", "Parar se NÃO encontrar na tela")
                                    encontrou = validar_texto_na_tela(driver, inner_step["texto"], timeout=inner_step.get("timeout", 5), retentativas=inner_ret, intervalo=inner_inter)
                                    
                                    if "ENCONTRAR" in condicao_regra and encontrou:
                                        msg_txt = f"Texto de alerta/erro '{inner_step['texto']}' foi localizado na tela."
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, msg_txt)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** {msg_txt}")
                                        interrompido = True
                                        break
                                    elif "NÃO encontrar" in condicao_regra and not encontrou:
                                        msg_txt = f"Texto esperado '{inner_step['texto']}' não foi localizado na tela."
                                        registrar_falha_checkpoint(nome_key_checkpoint, i_item, inner_idx, inner_acao, rotulo_p, valor_atual_acao, planilha_atual, coluna_atual, msg_txt)
                                        st.error(f"⛔ **Interrupção no Passo {inner_idx + 1} (Linha {i_item + 1}):** {msg_txt}")
                                        interrompido = True
                                        break
                                    else:
                                        st.success(f"✅ Validação de texto concluída na linha {i_item + 1}: '{inner_step['texto']}'")

                                    time.sleep(inner_pausa)

                            if interrompido:
                                break

                            checkpoint_dados[nome_key_checkpoint] = {
                                "proxima_linha_index": i_item + 1,
                                "status": "EM_ANDAMENTO" if (i_item + 1) < max_lin else "CONCLUIDO",
                                "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "ultima_linha_concluida": i_item + 1
                            }
                            salvar_json_local(ARQUIVO_CHECKPOINT, checkpoint_dados)

                        if interrompido:
                            break

                        step_idx = end_loop_idx + 1

                    elif acao == "Fim de Loop":
                        step_idx += 1

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
        opcoes_mod = ["➕ Criar Novo Módulo..."] + modulos_existentes
        mod_escolhido = st.selectbox("1. Módulo Pai:", opcoes_mod, key="map_mod_escolhido")
        
        if mod_escolhido == "➕ Criar Novo Módulo...":
            modulo_pai_final = st.text_input("Nome do Novo Módulo:", value="Modulo_Geral", key="map_mod_novo")
        else:
            modulo_pai_final = mod_escolhido

    with col_sel2:
        telas_existentes = list(catalogo_atual.get(modulo_pai_final, {}).keys())
        opcoes_tela = ["➕ Criar Nova Tela..."] + telas_existentes
        tela_escolhida = st.selectbox("2. Tela / Seção:", opcoes_tela, key="map_tela_escolhida")
        
        if tela_escolhida == "➕ Criar Nova Tela...":
            nome_tela_input = st.text_input("Nome da Nova Tela:", value="Consulta_Geral", key="map_tela_nova")
        else:
            nome_tela_input = tela_escolhida

    st.divider()

    col_b1, col_b2, col_b3 = st.columns([2, 3, 2])
    with col_b1:
        if st.button("🌐 1. Abrir Chrome / Logar", width="stretch"):
            driver = iniciar_navegador_universal()
            try:
                aguardar_login_manual(driver, URL_LOGIN_CONFIG)
                st.success("Navegador pronto para captura!")
            except Exception as e:
                st.error(f"Erro: {e}")

    with col_b2:
        if st.button("📸 2. Capturar Tela Atual", type="primary", width="stretch"):
            driver = obter_driver_ativo()
            if not driver:
                st.error("Abra o navegador primeiro pelo botão '1. Abrir Chrome / Logar'.")
            else:
                try:
                    mapa_elem = mapear_tela_atual_para_json(driver, nome_tela_input)[nome_tela_input]
                    cat_atualizado = salvar_elemento_no_catalogo(modulo_pai_final, nome_tela_input, mapa_elem)
                    
                    st.success(f"✅ Tela '{nome_tela_input}' salva com sucesso no módulo '{modulo_pai_final}'!")
                    st.json(cat_atualizado[modulo_pai_final][nome_tela_input])
                except Exception as e:
                    st.error(f"Erro ao capturar: {e}")

    with col_b3:
        if st.button("❌ 3. Fechar Navegador", width="stretch"):
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
            textos_atuais[chave_txt] = conteudo_txt
            salvar_json_local(ARQUIVO_TEXTOS, textos_atuais)
            st.success("Texto salvo!")
            st.rerun()

    st.divider()
    st.json(textos_atuais)


# ------------------------------------------------------------------------------
# ABA 4: GERENCIADOR DE CATÁLOGO (CORRIGIDO: SALVAMENTO E LIMPEZA DE CACHE)
# ------------------------------------------------------------------------------
with tab4:
    st.subheader("🗂️ Gerenciador de Catálogo Mapeado")
    st.write("Ative, desative, renomeie, edite seletores (XPaths) ou adicione novos elementos manuais.")
    
    catalogo_gerencia = carregar_json_local(ARQUIVO_CATALOGO)
    
    if catalogo_gerencia:
        col_g1, col_g2 = st.columns(2)
        
        with col_g1:
            modulos_disp = list(catalogo_gerencia.keys())
            mod_gerencia = st.selectbox("1. Selecione o Módulo:", modulos_disp, key="mod_ger")
        
        if mod_gerencia:
            telas_disp = list(catalogo_gerencia[mod_gerencia].keys())
            with col_g2:
                tela_gerencia = st.selectbox("2. Selecione a Tela:", telas_disp, key="tela_ger")
                
            if tela_gerencia:
                dict_elementos = catalogo_gerencia[mod_gerencia][tela_gerencia]
                key_editor = f"editor_{mod_gerencia}_{tela_gerencia}"
                
                st.divider()
                st.markdown(f"### 📋 Gerenciar Elementos da Tela: `{tela_gerencia}`")
                st.caption("• Você pode editar diretamente os nomes e os **Seletores/XPaths** na tabela abaixo.\n• Desmarque a caixa 'Ativo' para ocultar elementos desnecessários.\n• Se apagar o 'Identificador' e salvar, o nome original será restaurado automaticamente.")
                
                # --- BLOCO DE INSERÇÃO MANUAL DE NOVO ELEMENTO / PATH ---
                with st.expander("➕ Adicionar Elemento/Seletor Customizado Manualmente a esta Tela"):
                    col_m1, col_m2, col_m3 = st.columns([2, 3, 1])
                    with col_m1:
                        novo_nome_elem = st.text_input("Nome do Elemento:", value="horas_prerequisito", key="add_man_nome")
                    with col_m2:
                        novo_target_elem = st.text_input(
                            "Seletor / XPath Customizado:", 
                            value="//input[@maxlength='4' and following-sibling::text()[contains(., 'Horas')]]", 
                            key="add_man_target"
                        )
                    with col_m3:
                        novo_by_elem = st.selectbox("Tipo:", ["xpath", "id", "css_selector"], key="add_man_by")
                    
                    if st.button("➕ Inserir Elemento na Tela", type="primary"):
                        if novo_nome_elem and novo_target_elem:
                            nome_chave_clean = re.sub(r'[^\w\s]', '', novo_nome_elem).strip().lower().replace(" ", "_")
                            if not nome_chave_clean:
                                nome_chave_clean = "elemento_customizado"
                                
                            catalogo_gerencia[mod_gerencia][tela_gerencia][nome_chave_clean] = {
                                "by": novo_by_elem,
                                "target": novo_target_elem,
                                "ativo": True,
                                "nome_original": nome_chave_clean
                            }
                            salvar_json_local(ARQUIVO_CATALOGO, catalogo_gerencia)
                            
                            # Limpa o cache do data_editor para forçar atualização visual
                            if key_editor in st.session_state:
                                del st.session_state[key_editor]
                                
                            st.success(f"✅ Elemento '{nome_chave_clean}' adicionado com sucesso!")
                            st.rerun()

                st.divider()

                # --- TABELA INTERATIVA COM CAMPO DE SELETOR EDITÁVEL ---
                lista_dados = []
                chaves_originais_ordem = list(dict_elementos.keys())
                
                for k in chaves_originais_ordem:
                    v = dict_elementos[k]
                    nome_orig = v.get("nome_original", k)
                    lista_dados.append({
                        "Identificador": k,
                        "Nome Original": nome_orig,
                        "Ativo": v.get("ativo", True),
                        "Tipo": v.get("by", "xpath"),
                        "Alvo (Seletor)": v.get("target", "")
                    })
                
                df_elementos = pd.DataFrame(lista_dados)
                
                df_editado = st.data_editor(
                    df_elementos,
                    column_config={
                        "Ativo": st.column_config.CheckboxColumn("Ativo", help="Desmarque para desativar/ocultar no editor"),
                        "Identificador": st.column_config.TextColumn("Identificador (Nome Customizado)"),
                        "Nome Original": st.column_config.TextColumn("Nome Original Mapeado", disabled=True),
                        "Tipo": st.column_config.SelectboxColumn("Tipo", options=["xpath", "id", "css_selector"]),
                        "Alvo (Seletor)": st.column_config.TextColumn("Alvo (Seletor/XPath Customizado)"),
                    },
                    disabled=["Nome Original"],
                    hide_index=True,
                    width="stretch",
                    key=key_editor
                )
                
                col_sav1, col_sav2, col_sav3 = st.columns(3)
                
                with col_sav1:
                    if st.button("💾 Salvar Alterações na Tela", type="primary", width="stretch"):
                        novo_dict_tela = {}
                        
                        for i, row in df_editado.iterrows():
                            nome_digitado = str(row["Identificador"]).strip()
                            nome_orig = str(row["Nome Original"]).strip()
                            target_digitado = str(row["Alvo (Seletor)"]).strip()
                            tipo_digitado = str(row["Tipo"]).strip()
                            
                            nome_final = nome_digitado if nome_digitado else nome_orig
                            
                            # Recupera todas as propriedades originais do elemento (opcoes, is_select, etc.)
                            elem_dict_base = {}
                            if i < len(chaves_originais_ordem):
                                chave_antiga = chaves_originais_ordem[i]
                                elem_dict_base = dict_elementos[chave_antiga].copy()
                            
                            # Atualiza com as modificações feitas pelo usuário na tabela
                            elem_dict_base["by"] = tipo_digitado
                            elem_dict_base["target"] = target_digitado
                            elem_dict_base["ativo"] = bool(row["Ativo"])
                            elem_dict_base["nome_original"] = nome_orig
                            
                            novo_dict_tela[nome_final] = elem_dict_base
                        
                        catalogo_gerencia[mod_gerencia][tela_gerencia] = novo_dict_tela
                        salvar_json_local(ARQUIVO_CATALOGO, catalogo_gerencia)
                        
                        # Limpa o cache do data_editor no session_state para carregar os novos dados
                        if key_editor in st.session_state:
                            del st.session_state[key_editor]
                            
                        st.success("✅ Alterações salvas com sucesso no arquivo JSON!")
                        st.rerun()

                with col_sav2:
                    if st.button("🔄 Restaurar Nomes Originais", width="stretch"):
                        st.session_state[f"confirm_restore_{mod_gerencia}_{tela_gerencia}"] = True

                with col_sav3:
                    if st.button("🗑️ Apagar Sub-módulo (Tela)", width="stretch"):
                        st.session_state[f"confirm_delete_tela_{mod_gerencia}_{tela_gerencia}"] = True

                # Bloco de Confirmação para Restaurar Nomes
                if st.session_state.get(f"confirm_restore_{mod_gerencia}_{tela_gerencia}"):
                    st.warning("⚠️ **Confirmação:** Tem certeza que deseja restaurar TODOS os nomes desta tela para os padrões originais?")
                    col_res_c1, col_res_c2 = st.columns(2)
                    if col_res_c1.button("✅ Sim, Restaurar", type="primary", width="stretch", key=f"btn_res_sim_{mod_gerencia}_{tela_gerencia}"):
                        novo_dict_tela = {}
                        for i, row in df_editado.iterrows():
                            nome_orig = str(row["Nome Original"]).strip()
                            elem_dict_base = {}
                            if i < len(chaves_originais_ordem):
                                chave_antiga = chaves_originais_ordem[i]
                                elem_dict_base = dict_elementos[chave_antiga].copy()
                                
                            elem_dict_base["by"] = str(row["Tipo"])
                            elem_dict_base["target"] = str(row["Alvo (Seletor)"])
                            elem_dict_base["ativo"] = bool(row["Ativo"])
                            elem_dict_base["nome_original"] = nome_orig
                            
                            novo_dict_tela[nome_orig] = elem_dict_base

                        catalogo_gerencia[mod_gerencia][tela_gerencia] = novo_dict_tela
                        salvar_json_local(ARQUIVO_CATALOGO, catalogo_gerencia)
                        
                        if key_editor in st.session_state:
                            del st.session_state[key_editor]
                            
                        st.session_state[f"confirm_restore_{mod_gerencia}_{tela_gerencia}"] = False
                        st.success("✅ Todos os nomes foram restaurados!")
                        st.rerun()
                    if col_res_c2.button("❌ Cancelar", width="stretch", key=f"btn_res_nao_{mod_gerencia}_{tela_gerencia}"):
                        st.session_state[f"confirm_restore_{mod_gerencia}_{tela_gerencia}"] = False
                        st.rerun()

                # Bloco de Confirmação para Apagar Sub-módulo (Tela)
                if st.session_state.get(f"confirm_delete_tela_{mod_gerencia}_{tela_gerencia}"):
                    st.error(f"🚨 **ATENÇÃO:** Tem certeza que deseja EXCLUIR PERMANENTEMENTE o sub-módulo (tela) **'{tela_gerencia}'** do módulo **'{mod_gerencia}'**?")
                    col_del_c1, col_del_c2 = st.columns(2)
                    if col_del_c1.button("🔥 Sim, Apagar Sub-módulo", type="primary", width="stretch", key=f"btn_del_sim_{mod_gerencia}_{tela_gerencia}"):
                        del catalogo_gerencia[mod_gerencia][tela_gerencia]
                        # Remove o módulo pai apenas se ele não tiver mais nenhuma tela restante
                        if not catalogo_gerencia[mod_gerencia]:
                            del catalogo_gerencia[mod_gerencia]
                            
                        salvar_json_local(ARQUIVO_CATALOGO, catalogo_gerencia)
                        
                        if key_editor in st.session_state:
                            del st.session_state[key_editor]
                            
                        st.session_state[f"confirm_delete_tela_{mod_gerencia}_{tela_gerencia}"] = False
                        st.success(f"🗑️ Tela '{tela_gerencia}' apagada com sucesso!")
                        st.rerun()
                    if col_del_c2.button("❌ Cancelar", width="stretch", key=f"btn_del_nao_{mod_gerencia}_{tela_gerencia}"):
                        st.session_state[f"confirm_delete_tela_{mod_gerencia}_{tela_gerencia}"] = False
                        st.rerun()
    else:
        st.info("O catálogo atual está vazio. Use a Aba 2 para mapear novos elementos.")
