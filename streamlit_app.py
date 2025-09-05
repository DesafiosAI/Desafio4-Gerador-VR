import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import warnings
import io

warnings.filterwarnings('ignore')

# --- INÍCIO DA CLASSE E LÓGICA DO SISTEMA ---
class SistemaVRComGemini:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-pro')
        self.mapa_sindicatos = {
            "SINDPD SP": {"estado": "São Paulo", "dias": 22, "valor": 37.50},
            "SINDPPD RS": {"estado": "Rio Grande do Sul", "dias": 21, "valor": 35.00},
            "SINDPD RJ": {"estado": "Rio de Janeiro", "dias": 21, "valor": 35.00},
            "SITEPD PR": {"estado": "Paraná", "dias": 22, "valor": 35.00}
        }
    def consultar_gemini_elegibilidade(self, funcionario):
        prompt = f"""
        Você é um especialista em RH e legislação trabalhista brasileira.
        Analise se este funcionário tem direito ao Vale Refeição conforme CLT e acordos sindicais:
        Dados do Funcionário:
        - Matrícula: {funcionario.get('matricula', 'N/A')}
        - Categoria: {funcionario.get('categoria', 'N/A')}
        - Situação: {funcionario.get('situacao', 'N/A')}
        - Cargo: {funcionario.get('cargo', 'N/A')}
        Regras CLT para VR:
        1. Funcionários CLT ativos: ELEGÍVEL
        2. Licença maternidade: ELEGÍVEL
        3. Férias: NÃO ELEGÍVEL
        4. Auxílio doença/Afastamento INSS: NÃO ELEGÍVEL
        5. Aprendizes: NÃO ELEGÍVEL
        6. Estagiários: NÃO ELEGÍVEL
        7. Desligados: NÃO ELEGÍVEL
        8. Funcionários no exterior: NÃO ELEGÍVEL
        Responda APENAS no formato JSON: {{"elegivel": true/false, "motivo": "explicação curta", "base_legal": "lei ou artigo CLT aplicável"}}
        """
        try:
            response = self.model.generate_content(prompt)
            texto_resposta = response.text.strip()
            inicio = texto_resposta.find('{')
            fim = texto_resposta.rfind('}') + 1
            json_str = texto_resposta[inicio:fim]
            return json.loads(json_str)
        except Exception:
            return self.decisao_fallback(funcionario)
    def decisao_fallback(self, funcionario):
        situacao = str(funcionario.get('situacao', '')).lower()
        categoria = str(funcionario.get('categoria', '')).lower()
        if 'aprendiz' in categoria: return {"elegivel": False, "motivo": "Aprendiz não tem direito ao VR", "base_legal": "Lei 10.097/2000"}
        if 'estagiario' in categoria or 'estágio' in categoria: return {"elegivel": False, "motivo": "Estagiário não tem direito ao VR", "base_legal": "Lei 11.788/2008"}
        if 'desligado' in categoria or 'desligado' in situacao: return {"elegivel": False, "motivo": "Funcionário desligado", "base_legal": "CLT - Sem vínculo"}
        if 'férias' in situacao: return {"elegivel": False, "motivo": "VR suspenso durante férias", "base_legal": "CLT Art. 458"}
        if 'afastado' in situacao or 'auxílio' in situacao: return {"elegivel": False, "motivo": "VR suspenso durante afastamento", "base_legal": "CLT - Afastamento INSS"}
        if 'maternidade' in situacao: return {"elegivel": True, "motivo": "Mantém direito durante licença maternidade", "base_legal": "Lei 11.770/2008"}
        return {"elegivel": True, "motivo": "Funcionário ativo elegível", "base_legal": "CLT + Acordo Sindical"}

    def carregar_dados(self, arquivos):
        funcionarios = {}
        with st.status("Carregando e consolidando planilhas...", expanded=True) as status:
            for nome_arquivo, df in arquivos.items():
                nome_upper = nome_arquivo.upper()
                st.write(f"Processando {nome_arquivo}...")
                if 'ATIVOS' in nome_upper:
                    for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            funcionarios[mat] = {'matricula': mat, 'cargo': row.get('TITULO DO CARGO', ''), 'situacao': 'Trabalhando', 'sindicato': row.get('Sindicato', ''), 'categoria': 'ATIVO'}
                elif 'ADMISSÃO' in nome_upper:
                     for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            if mat in funcionarios:
                                funcionarios[mat]['categoria'] = 'ADMISSAO'
                elif 'FÉRIAS' in nome_upper:
                     for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            if mat in funcionarios:
                                funcionarios[mat]['situacao'] = 'Férias'
                                funcionarios[mat]['categoria'] = 'FERIAS'
                elif 'APRENDIZ' in nome_upper:
                    for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            funcionarios[mat] = {'matricula': mat, 'cargo': 'APRENDIZ', 'situacao': 'Trabalhando', 'categoria': 'APRENDIZ'}
                elif 'ESTÁGIO' in nome_upper:
                    for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            funcionarios[mat] = {'matricula': mat, 'cargo': 'ESTAGIÁRIO', 'situacao': 'Trabalhando', 'categoria': 'ESTAGIARIO'}
                elif 'DESLIGADOS' in nome_upper:
                    col_mat = 'MATRICULA' if 'MATRICULA' in df.columns else 'MATRICULA '
                    for _, row in df.iterrows():
                        if pd.notna(row.get(col_mat)):
                            mat = int(row[col_mat])
                            if mat in funcionarios:
                                funcionarios[mat]['situacao'] = 'Desligado'
                                funcionarios[mat]['categoria'] = 'DESLIGADO'
                elif 'AFASTAMENTOS' in nome_upper:
                    for _, row in df.iterrows():
                        if pd.notna(row.get('MATRICULA')):
                            mat = int(row['MATRICULA'])
                            if mat in funcionarios:
                                funcionarios[mat]['situacao'] = row.get('DESC. SITUACAO', 'Afastado')
                                funcionarios[mat]['categoria'] = 'AFASTAMENTO'
            status.update(label="Consolidação concluída!", state="complete")
        self.funcionarios = pd.DataFrame(list(funcionarios.values()))
        st.success(f"Foram encontrados {len(self.funcionarios)} registros de funcionários.")
        return True

    def processar_com_gemini(self):
        resultados = []
        total = len(self.funcionarios)
        progresso = st.progress(0)
        status_text = st.empty()
        for idx, func in self.funcionarios.iterrows():
            decisao = self.consultar_gemini_elegibilidade(func.to_dict())
            resultado = {}
            if decisao['elegivel']:
                sindicato_raw = func.get('sindicato', '')
                sindicato = '' if pd.isna(sindicato_raw) else str(sindicato_raw).strip()
                config = self.mapa_sindicatos.get(sindicato, self.mapa_sindicatos['SINDPD SP'])
                valor_total = config['valor'] * config['dias']
                resultado = {'matricula': func['matricula'], 'dias': config['dias'], 'valor_diario': config['valor'], 'valor_total': valor_total, 'obs': decisao['motivo'], 'elegivel': True}
            else:
                resultado = {'matricula': func['matricula'], 'obs': decisao['motivo'], 'base_legal': decisao.get('base_legal', 'N/A'), 'elegivel': False}
            resultados.append(resultado)
            progresso.progress((idx + 1) / total)
            status_text.text(f"Analisando com IA: {idx + 1}/{total}...")
        self.resultados = pd.DataFrame(resultados)
        return self.resultados
    def gerar_planilha_final(self):
        elegiveis = self.resultados[self.resultados['elegivel']].copy()
        planilha_vr = []
        for _, row in elegiveis.iterrows():
            planilha_vr.append({'Matricula': int(row['matricula']), 'Competência': '01/05/2025', 'Dias': int(row['dias']), 'VALOR DIÁRIO VR': float(row['valor_diario']), 'TOTAL': float(row['valor_total']), 'OBS GERAL': row['obs']})
        df_vr = pd.DataFrame(planilha_vr)
        nao_elegiveis = self.resultados[~self.resultados['elegivel']].copy()
        validacoes = nao_elegiveis[['matricula', 'obs', 'base_legal']]
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_vr.to_excel(writer, sheet_name='VR MENSAL 05.2025', index=False)
            validacoes.to_excel(writer, sheet_name='Validações', index=False)
        return output.getvalue(), df_vr['TOTAL'].sum(), len(df_vr)
# --- FIM DA CLASSE E LÓGICA DO SISTEMA ---


# --- INTERFACE GRÁFICA COM STREAMLIT ---
st.set_page_config(page_title="Gerador de VR Mensal com Gemini AI", layout="wide")
st.title("🤖 Gerador de Planilha VR com IA")

# --- VERIFICAÇÃO DA API KEY (A PARTE MAIS IMPORTANTE) ---
try:
    api_key = st.secrets["API_KEY"]
except Exception:
    st.error("ERRO: Chave da API não encontrada. Por favor, configure o segredo 'API_KEY' nas configurações do seu Space no Hugging Face.")
    st.stop() 

st.success("✅ Chave de API carregada com sucesso!")
st.markdown("---")

# Lógica de Upload e Processamento
st.header("1. Faça o Upload dos Arquivos Base")
uploaded_files = st.file_uploader(
    "Selecione todas as 7 planilhas necessárias (.xlsx)",
    accept_multiple_files=True,
    type=['xlsx']
)

if uploaded_files:
    if len(uploaded_files) < 7:
        st.warning("Atenção: Você precisa fazer o upload de todos os 7 arquivos para um processamento completo.")
    
    if st.button("🚀 Iniciar Processamento com Gemini AI"):
        try:
            sistema = SistemaVRComGemini(api_key)
            arquivos_df = {file.name: pd.read_excel(file) for file in uploaded_files}

            sistema.carregar_dados(arquivos_df)
            if not sistema.funcionarios.empty:
                sistema.processar_com_gemini()
                planilha_bytes, valor_total, total_func = sistema.gerar_planilha_final()

                st.success("🏆 Processamento Concluído!")
                st.metric("Valor Total do VR", f"R$ {valor_total:,.2f}")
                st.download_button(
                    label="📥 Baixar Planilha VR MENSAL 05.2025.xlsx",
                    data=planilha_bytes,
                    file_name="VR MENSAL 05.2025.xlsx"
                )
            else:
                st.error("Nenhum registro de funcionário foi encontrado após a consolidação das planilhas. Verifique os arquivos.")

        except Exception as e:
            st.error(f"❌ Ocorreu um erro crítico durante o processamento: {str(e)}")
