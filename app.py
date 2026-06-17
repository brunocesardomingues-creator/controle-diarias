import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import plotly.express as px
import hashlib
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

# ==================== 1. CONFIGURAÇÃO E BANCO DE DADOS ====================
st.set_page_config(
    page_title="Controle de Diárias",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="collapsed"
)

conn = sqlite3.connect('diarias.db', check_same_thread=False)
cursor = conn.cursor()

# Tabela de usuários
cursor.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        senha_hash TEXT NOT NULL,
        data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Tabela de lançamentos com FK pro usuário
cursor.execute('''
    CREATE TABLE IF NOT EXISTS lancamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        dia INTEGER NOT NULL,
        quinzena TEXT NOT NULL,
        nome_rota TEXT,
        modalidade_veiculo TEXT NOT NULL,
        valor_diaria REAL NOT NULL,
        desconto_pnr REAL DEFAULT 0,
        desconto_combustivel REAL DEFAULT 0,
        valor_final_diaria REAL NOT NULL,
        data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id),
        UNIQUE(usuario_id, dia, data_registro)
    )
''')
conn.commit()

# ==================== 2. FUNÇÕES DE AUTENTICAÇÃO ====================

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def criar_usuario(nome, email, senha):
    try:
        cursor.execute(
            "INSERT INTO usuarios (nome, email, senha_hash) VALUES (?,?,?)",
            (nome, email, hash_senha(senha))
        )
        conn.commit()
        return True, "Conta criada com sucesso!"
    except sqlite3.IntegrityError:
        return False, "Email já cadastrado"

def fazer_login(email, senha):
    cursor.execute("SELECT id, nome, senha_hash FROM usuarios WHERE email =?", (email,))
    user = cursor.fetchone()
    if user and user[2] == hash_senha(senha):
        return True, {"id": user[0], "nome": user[1], "email": email}
    return False, None

# ==================== 3. REGRAS DE NEGÓCIO ====================

def calcular_quinzena(dia: int) -> str:
    return "1ª Quinzena" if 1 <= dia <= 15 else "2ª Quinzena"

def calcular_valor_final(valor_diaria: float, desc_pnr: float, desc_comb: float) -> float:
    return round(valor_diaria - desc_pnr - desc_comb, 2)

def formatar_moeda(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ==================== 4. CRUD COM FILTRO POR USUÁRIO ====================

def carregar_lancamentos(usuario_id: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM lancamentos WHERE usuario_id =? ORDER BY dia",
        conn, params=(usuario_id,)
    )
    return df

def salvar_lancamento(usuario_id, dia, nome_rota, modalidade, valor_diaria, desc_pnr, desc_comb):
    quinzena = calcular_quinzena(dia)
    valor_final = calcular_valor_final(valor_diaria, desc_pnr, desc_comb)
    try:
        cursor.execute('''
            INSERT INTO lancamentos
            (usuario_id, dia, quinzena, nome_rota, modalidade_veiculo, valor_diaria, desconto_pnr, desconto_combustivel, valor_final_diaria)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (usuario_id, dia, quinzena, nome_rota, modalidade, valor_diaria, desc_pnr, desc_comb, valor_final))
        conn.commit()
        return True, "Lançamento salvo!"
    except sqlite3.IntegrityError:
        return False, f"Já existe lançamento para o dia {dia}"

def excluir_lancamento(id_lancamento, usuario_id):
    cursor.execute("DELETE FROM lancamentos WHERE id =? AND usuario_id =?", (id_lancamento, usuario_id))
    conn.commit()

# ==================== 5. GERAR PDF DO CONTRACHEQUE ====================

def gerar_pdf_contracheque(df: pd.DataFrame, nome_motorista: str, mes_ano: str) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Header
    c.setFont("Helvetica-Bold", 18)
    c.drawString(2*cm, height - 2*cm, "Contracheque - Controle de Diárias")
    c.setFont("Helvetica", 12)
    c.drawString(2*cm, height - 2.8*cm, f"Motorista: {nome_motorista}")
    c.drawString(2*cm, height - 3.4*cm, f"Período: {mes_ano}")

    # Totais
    total_bruto = df['valor_diaria'].sum()
    total_desc = (df['desconto_pnr'] + df['desconto_combustivel']).sum()
    total_liq = df['valor_final_diaria'].sum()

    y = height - 5*cm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(2*cm, y, "Resumo Financeiro")
    c.setFont("Helvetica", 11)
    y -= 0.8*cm
    c.drawString(2*cm, y, f"Total Bruto: {formatar_moeda(total_bruto)}")
    y -= 0.6*cm
    c.drawString(2*cm, y, f"Total Descontos: {formatar_moeda(total_desc)}")
    y -= 0.6*cm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2*cm, y, f"Total Líquido a Receber: {formatar_moeda(total_liq)}")

    # Detalhes por quinzena
    y -= 1.5*cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Detalhamento por Quinzena")

    for quinzena in ['1ª Quinzena', '2ª Quinzena']:
        q_df = df[df['quinzena'] == quinzena]
        if not q_df.empty:
            y -= 1*cm
            c.setFont("Helvetica-Bold", 11)
            c.drawString(2*cm, y, quinzena)
            c.setFont("Helvetica", 10)
            y -= 0.5*cm
            c.drawString(2.5*cm, y, f"Dias trabalhados: {len(q_df)}")
            y -= 0.5*cm
            c.drawString(2.5*cm, y, f"Líquido: {formatar_moeda(q_df['valor_final_diaria'].sum())}")

    # Tabela de lançamentos
    y -= 1.5*cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Lançamentos Detalhados")
    y -= 0.8*cm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(2*cm, y, "Dia")
    c.drawString(3*cm, y, "Rota")
    c.drawString(8*cm, y, "Modalidade")
    c.drawString(12*cm, y, "Bruto")
    c.drawString(14.5*cm, y, "Desc")
    c.drawString(17*cm, y, "Líquido")

    c.setFont("Helvetica", 8)
    for _, row in df.iterrows():
        y -= 0.5*cm
        if y < 2*cm: # Nova página
            c.showPage()
            y = height - 2*cm
        c.drawString(2*cm, y, str(row['dia']))
        c.drawString(3*cm, y, str(row['nome_rota'])[:25])
        c.drawString(8*cm, y, str(row['modalidade_veiculo'])[:20])
        c.drawString(12*cm, y, formatar_moeda(row['valor_diaria']))
        c.drawString(14.5*cm, y, formatar_moeda(row['desconto_pnr'] + row['desconto_combustivel']))
        c.drawString(17*cm, y, formatar_moeda(row['valor_final_diaria']))

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# ==================== 6. SISTEMA DE LOGIN ====================

if 'usuario' not in st.session_state:
    st.session_state.usuario = None

if not st.session_state.usuario:
    st.title("🚚 Controle de Diárias - Login")

    tab_login, tab_cadastro = st.tabs(["Entrar", "Criar Conta"])

    with tab_login:
        email = st.text_input("Email")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            sucesso, user = fazer_login(email, senha)
            if sucesso:
                st.session_state.usuario = user
                st.rerun()
            else:
                st.error("Email ou senha incorretos")

    with tab_cadastro:
        nome = st.text_input("Nome Completo")
        email_cad = st.text_input("Email", key="email_cad")
        senha_cad = st.text_input("Senha", type="password", key="senha_cad")
        if st.button("Criar Conta"):
            if nome and email_cad and senha_cad:
                sucesso, msg = criar_usuario(nome, email_cad, senha_cad)
                if sucesso:
                    st.success(msg + " Faça login na aba ao lado")
                else:
                    st.error(msg)
            else:
                st.warning("Preencha todos os campos")
    st.stop()

# ==================== 7. APP PRINCIPAL - USUÁRIO LOGADO ====================

usuario = st.session_state.usuario

# Sidebar com logout e backup
with st.sidebar:
    st.write(f"👋 Olá, {usuario['nome']}")
    st.caption(usuario['email'])
    if st.button("Sair"):
        st.session_state.usuario = None
        st.rerun()

    st.divider()
    st.subheader("⚙️ Ferramentas")

    # Backup do banco
    with open('diarias.db', 'rb') as f:
        st.download_button(
            "📥 Backup Google Drive",
            f,
            file_name=f"backup_diarias_{datetime.now().strftime('%Y%m%d')}.db",
            mime="application/octet-stream",
            help="Baixe e faça upload manual no seu Drive"
        )

st.title("🚚 Controle de Diárias")
st.caption(f"Logado como: {usuario['nome']}")

tab1, tab2 = st.tabs(["📝 Lançamento Diário", "📊 Dashboard de Resumo"])

# ==================== TELA 1: LANÇAMENTO ====================
with tab1:
    st.subheader("Novo Lançamento")

    with st.form("form_lancamento", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            dia = st.number_input("Dia do Mês *", min_value=1, max_value=31, step=1)
            if dia:
                st.caption(f"→ {calcular_quinzena(dia)}")
            nome_rota = st.text_input("Nome da Rota", placeholder="Ex: Zona Sul - Manhã")

        with col2:
            modalidade = st.selectbox(
                "Modalidade do Veículo *",
                ["", "Van 1 rota", "kangu 1 rota", "kangu 2 rotas", "kangu+apoio", "TKS 1 rota"]
            )
            valor_diaria = st.number_input("Valor Diária *", min_value=0.0, step=10.0, format="%.2f")

        with col3:
            desc_pnr = st.number_input("Desconto PNR e Outros", min_value=0.0, step=5.0, format="%.2f")
            desc_comb = st.number_input("Desconto Combustível/Casa", min_value=0.0, step=5.0, format="%.2f")
            if valor_diaria > 0:
                valor_final_preview = calcular_valor_final(valor_diaria, desc_pnr, desc_comb)
                st.metric("Valor Final", formatar_moeda(valor_final_preview))

        if st.form_submit_button("💾 Salvar Lançamento", use_container_width=True, type="primary"):
            if not modalidade or valor_diaria <= 0:
                st.error("Preencha Modalidade e Valor da Diária")
            else:
                sucesso, msg = salvar_lancamento(
                    usuario['id'], dia, nome_rota, modalidade, valor_diaria, desc_pnr, desc_comb
                )
                if sucesso:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.divider()
    st.subheader("Lançamentos do Mês")
    df = carregar_lancamentos(usuario['id'])

    if df.empty:
        st.info("Nenhum lançamento ainda")
    else:
        df_display = df.copy()
        df_display['valor_diaria'] = df_display['valor_diaria'].apply(formatar_moeda)
        df_display['Descontos'] = (df['desconto_pnr'] + df['desconto_combustivel']).apply(formatar_moeda)
        df_display['valor_final_diaria'] = df_display['valor_final_diaria'].apply(formatar_moeda)
        df_display = df_display[['dia', 'quinzena', 'nome_rota', 'modalidade_veiculo', 'valor_diaria', 'Descontos', 'valor_final_diaria']]
        df_display.columns = ['Dia', 'Quinzena', 'Rota', 'Modalidade', 'Bruto', 'Descontos', 'Líquido']
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            id_excluir = st.selectbox("Excluir lançamento do dia:", [""] + df['dia'].astype(str).tolist())
            if id_excluir and st.button("🗑️ Excluir"):
                id_db = df[df['dia'] == int(id_excluir)]['id'].values[0]
                excluir_lancamento(id_db, usuario['id'])
                st.rerun()

# ==================== TELA 2: DASHBOARD ====================
with tab2:
    df = carregar_lancamentos(usuario['id'])
    if df.empty:
        st.info("Faça lançamentos para ver o dashboard")
    else:
        # Cards
        st.subheader("Resumo Financeiro do Mês")
        total_bruto = df['valor_diaria'].sum()
        total_descontos = (df['desconto_pnr'] + df['desconto_combustivel']).sum()
        total_liquido = df['valor_final_diaria'].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("💰 Total Bruto", formatar_moeda(total_bruto))
        col2.metric("📉 Total Descontos", formatar_moeda(total_descontos))
        col3.metric("✅ Total Líquido", formatar_moeda(total_liquido))

        # Botão PDF
        mes_ano = datetime.now().strftime("%B/%Y")
        pdf_bytes = gerar_pdf_contracheque(df, usuario['nome'], mes_ano)
        st.download_button(
            "📄 Baixar Contracheque PDF",
            pdf_bytes,
            file_name=f"contracheque_{usuario['nome']}_{mes_ano}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

        st.divider()
        # Quinzenas
        col1, col2 = st.columns(2)
        for col, quinzena in [(col1, '1ª Quinzena'), (col2, '2ª Quinzena')]:
            with col:
                st.subheader(f"📅 {quinzena}")
                q_df = df[df['quinzena'] == quinzena]
                if not q_df.empty:
                    st.write(f"**Bruto:** {formatar_moeda(q_df['valor_diaria'].sum())}")
                    st.write(f"**Descontos:** {formatar_moeda((q_df['desconto_pnr'] + q_df['desconto_combustivel']).sum())}")
                    st.write(f"**Líquido:** :blue[{formatar_moeda(q_df['valor_final_diaria'].sum())}]")
                else:
                    st.caption("Sem lançamentos")

        # Gráfico
        fig = px.bar(df, x='dia', y='valor_final_diaria', color='quinzena', title="Ganhos Líquidos por Dia")
        st.plotly_chart(fig, use_container_width=True)
