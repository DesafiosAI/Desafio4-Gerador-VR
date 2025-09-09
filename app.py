import gradio as gr
import pandas as pd
import google.generativeai as genai
import json
import warnings
import os
import traceback
from datetime import datetime

warnings.filterwarnings('ignore')

# --- Lógica de Cálculo de Dias Úteis ---
def calcular_dias_uteis_proporcionais(mes, ano, data_inicio, data_fim, feriados=[]):
    """
    Calcula os dias úteis de forma proporcional dentro de um mês.
    """
    dias_no_mes = pd.Period(f'{ano}-{mes}-01').days_in_month
    dias_uteis = 0
    for dia in range(1, dias_no_mes + 1):
        data_atual = datetime(ano, mes, dia)
        if data_atual.weekday() < 5: # Considera dias de semana (Segunda=0 a Sexta=4)
            if data_inicio.date() <= data_atual.date() <= data_fim.date():
                if data_atual.strftime('%Y-%m-%d') not in feriados:
                    dias_uteis += 1
    return dias_uteis

# --- INÍCIO DA CLASSE E LÓGICA DO SISTEMA ---
class SistemaVRComGemini:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-pro')
        self.mapa_sindicatos = {
            "SINDPD SP": {"estado": "São Paulo", "dias_base": 22, "valor": 37.50, "feriados": ["2025-01-25"]},
            "SINDPPD RS": {"estado": "Rio Grande do Sul", "dias_base": 21, "valor": 35.00, "feriados": []},
            "SINDPD RJ": {"estado": "Rio de Janeiro", "dias_base": 21, "valor": 35.00, "feriados": ["2025-01-20"]},
            "SITEPD PR": {"estado": "Paraná", "dias_base": 22, "valor": 35.00, "feriados": []}
        }

    def consultar_gemini_elegibilidade(self, funcionario):
        
        prompt = f"""
        Você é um especialista em RH e legislação trabalhista brasileira.
        Analise se este funcionário tem direito a ser incluído no processo de cálculo do Vale Refeição (VR).
        Dados do Funcionário:
        - Matrícula: {funcionario.get('matricula', 'N/A')}
        - Cargo: {funcionario.get('cargo', 'N/A')}
        - Situação: {funcionario.get('situacao', 'N/A')}
        - Local de Trabalho: {funcionario.get('local', 'Brasil')}

        Regras de Elegibilidade e Exclusão para o PROCESSO:
        - INCLUIR NO PROCESSO: Funcionários CLT ativos.
        - INCLUIR NO PROCESSO: Funcionários desligados no mês (o sistema fará o cálculo proporcional).
        - INCLUIR NO PROCESSO (CASO ESPECIAL): Licença Maternidade (dependente de Acordo Coletivo).
        - EXCLUIR DO PROCESSO: Diretores, Estagiários, Aprendizes.
        - EXCLUIR DO PROCESSO: Funcionários em Férias.
        - EXCLUIR DO PROCESSO: Outros afastamentos (ex: auxílio doença, INSS).
        - EXCLUIR DO PROCESSO: Funcionários que atuam no exterior.

        Responda APENAS no formato JSON: {{"elegivel": true/false, "motivo": "explicação curta e direta"}}
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
        cargo = str(funcionario.get('cargo', '')).lower()
        situacao = str(funcionario.get('situacao', '')).lower()
        categoria = str(funcionario.get('categoria', '')).lower()
        local = str(funcionario.get('local', '')).lower()

        if any(term in cargo for term in ['diretor', 'director']): return {"elegivel": False, "motivo": "Cargo de Diretor não elegível"}
        if 'estagiario' in categoria or 'estágio' in categoria: return {"elegivel": False, "motivo": "Estagiário não elegível"}
        if 'aprendiz' in categoria: return {"elegivel": False, "motivo": "Aprendiz não elegível"}
        if 'férias' in situacao: return {"elegivel": False, "motivo": "Funcionário em férias"}
        if 'exterior' in local: return {"elegivel": False, "motivo": "Funcionário no exterior"}
        
        if 'maternidade' in situacao:
            return {"elegivel": True, "motivo": "Licença Maternidade (dependente de Acordo Coletivo)"}
        if 'afastado' in situacao or 'licença' in situacao or 'auxílio' in situacao:
            return {"elegivel": False, "motivo": "Funcionário afastado"}
            
        # CORREÇÃO: Desligados são elegíveis para o cálculo, não excluídos aqui.
        if 'desligado' in categoria or 'desligado' in situacao:
            return {"elegivel": True, "motivo": "Funcionário desligado (a verificar cálculo)"}
        
        return {"elegivel": True, "motivo": "Funcionário ativo elegível"}

    def carregar_dados(self, arquivos_temporarios):
        funcionarios = {}
        arquivos_df = {os.path.basename(f.name): pd.read_excel(f.name) for f in arquivos_temporarios}
        
        # 1. Carrega ATIVOS como base principal
        for nome_arquivo, df in arquivos_df.items():
            if 'ATIVOS' in nome_arquivo.upper():
                for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA'])
                        funcionarios[mat] = {
                            'matricula': mat, 'cargo': row.get('TITULO DO CARGO', ''), 'situacao': row.get('DESCRIÇÃO SITUAÇÃO', 'Trabalhando'), 
                            'sindicato': row.get('Sindicato', ''), 'categoria': 'ATIVO', 'data_admissao': pd.to_datetime(row.get('ADMISSAO'), errors='coerce'),
                            'data_desligamento': pd.NaT, 'local': 'Brasil'
                        }

        # 2. Adiciona e ATUALIZA com as outras bases
        for nome_arquivo, df in arquivos_df.items():
            nome_upper = nome_arquivo.upper()
            
            if 'APRENDIZ' in nome_upper:
                for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']); funcionarios[mat] = {'matricula': mat, 'cargo': 'APRENDIZ', 'situacao': 'Trabalhando', 'categoria': 'APRENDIZ', 'local': 'Brasil', 'data_admissao': pd.NaT, 'data_desligamento': pd.NaT}
            elif 'ESTÁGIO' in nome_upper:
                for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']); funcionarios[mat] = {'matricula': mat, 'cargo': 'ESTAGIÁRIO', 'situacao': 'Trabalhando', 'categoria': 'ESTAGIARIO', 'local': 'Brasil', 'data_admissao': pd.NaT, 'data_desligamento': pd.NaT}
            elif 'ADMISSÃO' in nome_upper:
                 for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']); data_adm = pd.to_datetime(row.get('Admissão'), errors='coerce')
                        if mat in funcionarios: funcionarios[mat]['data_admissao'] = data_adm
                        else: funcionarios[mat] = {'matricula': mat, 'cargo': row.get('Cargo', ''), 'situacao': 'Trabalhando', 'sindicato': 'N/A', 'categoria': 'ADMISSAO', 'data_admissao': data_adm, 'data_desligamento': pd.NaT, 'local': 'Brasil'}
            elif 'FÉRIAS' in nome_upper:
                 for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']);                         
                        if mat in funcionarios: funcionarios[mat]['situacao'] = 'Férias'
            elif 'AFASTAMENTOS' in nome_upper:
                 for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']); 
                        if mat in funcionarios: funcionarios[mat]['situacao'] = row.get('DESC. SITUACAO', 'Afastado')
            elif 'DESLIGADOS' in nome_upper:
                col_mat = 'MATRICULA' if 'MATRICULA' in df.columns else 'MATRICULA '
                for _, row in df.iterrows():
                    if pd.notna(row.get(col_mat)):
                        mat = int(row[col_mat]); data_desl = pd.to_datetime(row.get('DATA DESLIGAMENTO'), errors='coerce')
                        if mat in funcionarios:
                            funcionarios[mat]['situacao'] = 'Desligado'; funcionarios[mat]['data_desligamento'] = data_desl
                        else:
                            # CORREÇÃO: Adiciona desligados que não estão na base de ativos
                            funcionarios[mat] = {'matricula': mat, 'cargo': row.get('CARGO', 'N/A'), 'situacao': 'Desligado', 'sindicato': 'N/A', 'categoria': 'DESLIGADO', 'data_admissao': pd.NaT, 'data_desligamento': data_desl, 'local': 'Brasil'}
            elif 'EXTERIOR' in nome_upper:
                 for _, row in df.iterrows():
                    if pd.notna(row.get('MATRICULA')):
                        mat = int(row['MATRICULA']); 
                        if mat in funcionarios: funcionarios[mat]['local'] = 'Exterior'

        self.funcionarios = pd.DataFrame(list(funcionarios.values()))

    def processar_beneficio(self, progress=gr.Progress()):
        resultados = []
        total = len(self.funcionarios)
        MES_PROCESSO, ANO_PROCESSO = 5, 2025

        for idx, func_row in self.funcionarios.iterrows():
            func = func_row.to_dict()
            progress(idx / total, desc=f"Analisando Matrícula {func['matricula']}...")
            
            decisao_ia = self.consultar_gemini_elegibilidade(func)
            dias_a_pagar, valor_total = 0, 0
            motivo_final = decisao_ia['motivo']
            elegivel_para_pagamento = decisao_ia['elegivel']

            sindicato_str = str(func.get('sindicato', ''))
            config_sindicato = self.mapa_sindicatos['SINDPD SP'] # Padrão
            for sigla, dados in self.mapa_sindicatos.items():
                if sigla in sindicato_str:
                    config_sindicato = dados
                    break
            
            if elegivel_para_pagamento:
                inicio_mes = datetime(ANO_PROCESSO, MES_PROCESSO, 1)
                fim_mes = datetime(ANO_PROCESSO, MES_PROCESSO, pd.Period(f'{ANO_PROCESSO}-{MES_PROCESSO}-01').days_in_month)
                
                admissao_no_mes = pd.notna(func['data_admissao']) and func['data_admissao'].month == MES_PROCESSO and func['data_admissao'].year == ANO_PROCESSO
                desligamento_no_mes = pd.notna(func['data_desligamento']) and func['data_desligamento'].month == MES_PROCESSO and func['data_desligamento'].year == ANO_PROCESSO
                
                if 'maternidade' in str(func.get('situacao', '')).lower():
                    dias_a_pagar = config_sindicato['dias_base']
                    motivo_final = "Licença Maternidade (pago integral conf. acordo)"
                elif not admissao_no_mes and not desligamento_no_mes:
                    dias_a_pagar = config_sindicato['dias_base']
                    motivo_final = "Pagamento integral"
                else: # Lógica para proporcionais (admitidos ou desligados)
                    data_inicio_trabalho = func['data_admissao'] if admissao_no_mes else inicio_mes
                    data_fim_trabalho = func['data_desligamento'] if desligamento_no_mes else fim_mes
                    
                    if desligamento_no_mes and func['data_desligamento'].day <= 15:
                        elegivel_para_pagamento = False
                        motivo_final = "Desligado antes do dia 15"
                    else:
                        dias_a_pagar = calcular_dias_uteis_proporcionais(MES_PROCESSO, ANO_PROCESSO, data_inicio_trabalho, data_fim_trabalho, config_sindicato['feriados'])
                        if admissao_no_mes: motivo_final = "Pagamento proporcional por admissão"
                        if desligamento_no_mes: motivo_final = "Pagamento proporcional por desligamento"
                
                if elegivel_para_pagamento: valor_total = dias_a_pagar * config_sindicato['valor']

            resultados.append({
                'matricula': func['matricula'], 'data_admissao': func['data_admissao'], 'sindicato_original': sindicato_str,
                'elegivel': elegivel_para_pagamento, 'motivo': motivo_final, 'dias_calculados': dias_a_pagar,
                'valor_diario': config_sindicato['valor'], 'valor_total': valor_total
            })

        self.resultados = pd.DataFrame(resultados)

    def gerar_planilha_final(self, output_filename="VR MENSAL 05.2025.xlsx"):
        elegiveis = self.resultados[self.resultados['elegivel']].copy()
        
        planilha_vr_data = []
        for _, row in elegiveis.iterrows():
            valor_total = row['valor_total']
            admissao_str = row['data_admissao'].strftime('%d/%m/%Y') if pd.notna(row['data_admissao']) else ''
            planilha_vr_data.append({
                'Matrícula': int(row['matricula']),
                'Admissão': admissao_str,
                'Sindicato do Colaborador': row['sindicato_original'],
                'Competência': '05/2025',
                'Dias': int(row['dias_calculados']),
                'Valor Diário em Reais do VR': float(row['valor_diario']),
                'Total pago para cada matrícula': valor_total,
                'Custo para a empresa': valor_total * 0.8,
                'Desconto aplicado para o profissional': valor_total * 0.2,
                'Observações gerais': row['motivo']
            })
        df_vr = pd.DataFrame(planilha_vr_data)
        nao_elegiveis = self.resultados[~self.resultados['elegivel']].copy()
        
        with pd.ExcelWriter(output_filename, engine='openpyxl') as writer:
            df_vr.to_excel(writer, sheet_name='VR MENSAL 05.2025', index=False)
            nao_elegiveis[['matricula', 'motivo']].to_excel(writer, sheet_name='Validações (Não Elegíveis)', index=False)
        
        total_geral = df_vr['Total pago para cada matrícula'].sum() if not df_vr.empty else 0
        return output_filename, total_geral, len(df_vr)

# --- FUNÇÃO PRINCIPAL PARA A INTERFACE GRADIO ---
def processar_arquivos(lista_de_ficheiros, progress=gr.Progress()):
    try:
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValueError("ERRO CRÍTICO: Chave de API não encontrada nos segredos do repositório.")
        
        progress(0, desc="A iniciar...")
        sistema = SistemaVRComGemini(api_key)
        progress(0.1, desc="Carregando e consolidando dados...")
        sistema.carregar_dados(lista_de_ficheiros)
        
        if sistema.funcionarios.empty:
            return "Nenhum funcionário encontrado. Verifique as folhas de cálculo.", None

        progress(0.3, desc="Processando regras de negócio e IA...")
        sistema.processar_beneficio(progress)
        
        progress(0.9, desc="Gerando planilha final...")
        caminho_ficheiro, valor_total, total_func = sistema.gerar_planilha_final()
        
        resultado_md = f"## 🏆 Processamento Concluído!\n- **Total de Funcionários com VR:** {total_func}\n- **Valor Total:** R$ {valor_total:,.2f}"
        return resultado_md, caminho_ficheiro

    except Exception as e:
        error_details = traceback.format_exc()
        return f"## ❌ Ocorreu um Erro Crítico\n```\n{e}\n\n{error_details}\n```", None

# --- CRIAÇÃO DA INTERFACE GRADIO ---
with gr.Blocks(theme=gr.themes.Soft(), title="Gerador de VR com IA") as demo:
    gr.Markdown("# 🤖 Gerador de Planilha de Cálculo VR com IA")
    gr.Markdown("Faça o upload de todas as planilhas-base para gerar o relatório final de Vale Refeição (VR).")
    
    with gr.Row():
        with gr.Column(scale=1):
            input_files = gr.File(
                label="Carregar planilhas (.xlsx)",
                file_count="multiple",
                file_types=[".xlsx"]
            )
            process_button = gr.Button("🚀 Clique aqui para iniciar a análise", variant="primary")
        
        with gr.Column(scale=2):
            output_markdown = gr.Markdown(label="Resultados")
            output_file = gr.File(label="Download da planilha final para a operadora (.xlsx)")

    process_button.click(
        fn=processar_arquivos,
        inputs=[input_files],
        outputs=[output_markdown, output_file]
    )

if __name__ == "__main__":
    demo.launch()