# Automador e Construtor de Workflows SIGAA

Aplicacao web desenvolvida com ajuda do Gemini utilizado Python e Streamlit para construcao, gerenciamento e execucao automatizada de workflows no Sistema Integrado de Gestao de Atividades Academicas (SIGAA). 

A ferramenta foi projetada para lidar com rotinas repetitivas do sistema, suportando loops com multiplas planilhas, recuperacao de falhas via checkpoint e tratamento especifico para componentes e mascaras do framework JSF/RichFaces.

---

## Funcionalidades Principais

* **Gerenciador de Sessao Universal:** Utiliza uma unica instancia do Google Chrome compartilhada entre o mapeador de telas e o executor de workflows, evitando a abertura de multiplas janelas.
* **Suporte a Multiplos Ambientes:** Alternancia dinamica entre o ambiente Sigaa UFG ou outro adicionado.
* **Autenticacao Inteligente:** Deteccao automatica de login ativo baseada em elementos do DOM interno (como o link de logoff e menu principal), evitando falsos positivos em paginas publicas ou em branco.
* **Construtor Flexivel de Workflows:**
  * **Tipos de Acao:** Clicar, Preencher, Navegar, Validar Texto, Iniciar Loop e Fim de Loop.
  * **Posicionamento Direcionado:** Opcao de inserir novos passos no final, antes ou depois de qualquer passo existente.
  * **Reordenacao:** Botoes para mover passos para cima ou para baixo.
  * **Importacao e Exportacao:** Possibilidade de salvar o workflow em formato JSON e carrega-lo posteriormente em qualquer maquina.
* **Iteracao com Multiplas Planilhas (Loops):**
  * Suporte ao upload simultaneo de arquivos Excel (.xlsx, .xls) e CSV.
  * Mapeamento individual de campos para colunas especificas de cada planilha.
  * Re-vinculacao dinamica de planilhas ao importar workflows salvos.
* **Preenchimento Resiliente (Tratamento JSF):**
  * Digitacao caractere por caractere com atraso ajustavel para compatibilidade com mascaras de JavaScript do SIGAA.
  * Disparo manual de eventos DOM (`input`, `change`, `keyup`) para notificacao do estado de componentes RichFaces sem reset por foco.
  * Conversao automatica de IDs com dois pontos (`id:com:colons`) para expressoes XPath literais.
* **Sistema de Re-tentativas e Checkpoint:**
  * Configuracao individual de quantidade de tentativas e intervalos de pausa para cada acao.
  * Normalizacao de texto (remocao de quebras de linha e caracteres HTML) para validacao flexivel de mensagens na tela.
  * Interrupcao automatica em caso de erro com geracao de relatorio detalhado em `checkpoint.json` (registrando a linha exata, o valor processado, a planilha e a mensagem de erro).

---

## Estrutura do Projeto

* `app.py`: Codigo fonte principal contendo a interface em Streamlit, a logica de controle do Selenium e o motor de execucao dos workflows.
* `elementos_sigaa.json`: Catalogo local contendo o mapeamento hierarquico de modulos, telas e seletores do SIGAA.
* `textos_padrao.json`: Biblioteca de textos cadastrados para acoes de validacao.
* `checkpoint.json`: Arquivo de controle de estado do loop para interrupcao e retomada de execucoes.

---

## Requisitos do Sistema

* Python 3.9 ou superior
* Google Chrome instalado no sistema operacional

### Dependencias Python

As seguintes bibliotecas sao necessarias para a execucao do projeto:

* streamlit
* selenium
* webdriver-manager
* pandas
* openpyxl


## Guia do Mapeador Automatico de Telas (Extrator)

O Mapeador Automatico de Telas e o componente da aplicacao responsavel por inspecionar a pagina aberta no Google Chrome e extrair automaticamente os seletores (XPath e IDs) de elementos interativos, como botoes, links, campos de texto e caixas de selecao. 

Esses elementos sao salvos no arquivo local `elementos_sigaa.json` de forma hierarquica, permitindo que qualquer usuario monte workflows sem precisar inspecionar o codigo HTML manualmente.

---

### Passo a Passo para Mapear uma Tela

1. **Iniciar a Sessao no Navegador:**
   * Acesse a aba **Mapeador de Telas** no painel principal.
   * Na barra lateral, selecione o ambiente desejado e clique em **Abrir / Logar no Chrome**.
   * Realize o login no SIGAA na janela do Chrome aberta e navegue ate a pagina cujos elementos voce deseja capturar.

2. **Organizar a Hierarquia do Catalogo:**
   * **Modulo Pai:** Selecione um modulo existente na lista ou escolha a opcao para criar um novo modulo (exemplo: `Modulo_Academico`, `Modulo_Biblioteca`).
   * **Nome da Tela:** Digite um nome identificador para a pagina atual (exemplo: `Consulta_Discente`, `Lancamento_Notas`).

3. **Executar a Captura de Elementos:**
   * Com a pagina desejada aberta e visivel no Chrome, clique no botao **2. Capturar Tela Atual**.
   * O robô analisara a estrutura da pagina, filtrara elementos irrelevantes do sistema e gerara um dicionario de seletores.
   * O resultado sera exibido na tela e gravado diretamente no arquivo `elementos_sigaa.json`.

4. **Reutilizar os Elementos Mapeados no Workflow:**
   * Retorne a aba **Editor e Executor do Workflow**.
   * Ao adicionar uma nova acao do tipo **Clicar** ou **Preencher**, marque a opcao **Usar Catalogo Mapeado**.
   * Selecione o Modulo, a Tela e o Elemento desejado nos menus suspensos. O seletor correto sera carregado automaticamente para a execucao.


## Edicao Manual do Arquivo de Elementos (elementos_sigaa.json)

Caso seja necessario adicionar ou corrigir um elemento diretamente no arquivo de catalogo sem utilizar o mapeador automatico, e possivel editar o arquivo `elementos_sigaa.json` utilizando qualquer editor de texto.

### Estrutura do JSON

O arquivo segue uma organizacao hierarquica composta por tres niveis de aninhamento:

1. **Primeiro Nivel (Modulo):** Agrupa as telas por grandes secoes do sistema (exemplo: `Modulo_Graduacao`, `Modulo_Biblioteca`).
2. **Segundo Nivel (Tela):** Agrupa os elementos pertencentes a uma pagina especifica (exemplo: `Consulta_Discente`, `Lancamento_Notas`).
3. **Terceiro Nivel (Elemento):** Define a chave do elemento e suas propriedades de identificacao (`by` e `target`).

---

### Esquema do Arquivo

```json
{
  "Nome_do_Modulo": {
    "Nome_da_Tela": {
      "nome_do_elemento": {
        "by": "xpath",
        "target": "seletor_ou_caminho_xpath"
      }
    }
  }
}

```
by: Define o tipo de busca. O padrao recomendado para o SIGAA é "xpath".
target: A expressao XPath exata ou o ID do elemento HTML.
