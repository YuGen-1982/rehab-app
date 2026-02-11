import streamlit as st
import pandas as pd
import datetime
from fpdf import FPDF
import io

# --- 1. データ読み込み ---
def load_all_logic(file_path):
    try:
        rehab_df = pd.read_excel(file_path, sheet_name='rehab_logic')
        med_df = pd.read_excel(file_path, sheet_name='medication_master')
        safety_df = pd.read_excel(file_path, sheet_name='safety_criteria')
        return rehab_df, med_df, safety_df
    except Exception as e:
        st.error(f"Excelの読み込みに失敗しました: {e}")
        return None, None, None

# --- 2. 判定ロジック ---
def evaluate(inputs, rehab_df, med_df, safety_df):
    results = {"stop": [], "caution": [], "advice": []}
    
    # A. 絶対中止基準
    for _, row in safety_df.iterrows():
        val = inputs.get(row['metric'])
        if val is not None:
            cond = f"{val} {row['operator']} {row['limit_value']}"
            if eval(cond):
                results["stop"].append(f"{row['alert_level']}: {row['metric']}が基準外です")

    # B. 疾患別・共通ロジック
    target_rules = rehab_df[(rehab_df['disease'] == inputs['主疾患']) | (rehab_df['disease'] == '共通')]
    for _, row in target_rules.iterrows():
        val = inputs.get(row['metric'])
        if val is not None:
            cond = f"{val} {row['operator']} {row['threshold']}"
            if eval(cond):
                results["caution"].append(row['message'])

    # C. 薬剤判定
    selected_drugs = inputs.get('服用薬剤', [])
    drug_times = inputs.get('薬剤ごとの時間', {})
    if selected_drugs:
        for drug in selected_drugs:
            med_info = med_df[med_df['drug_name'] == drug]
            if not med_info.empty:
                med_row = med_info.iloc[0]
                dose_time = drug_times.get(drug)
                now = datetime.datetime.now()
                dose_datetime = datetime.datetime.combine(datetime.date.today(), dose_time)
                diff_h = (now - dose_datetime).total_seconds() / 3600
                if med_row['t_max_start'] <= diff_h <= med_row['t_max_end']:
                    results["caution"].append(f"Caution【{drug}】: {med_row['risk_message']}（服用から{diff_h:.1f}h経過）")
                else:
                    results["advice"].append(f"Info【{drug}】: 現在はピーク時間外（服用から{diff_h:.1f}h経過）")
    return results

# --- 3. 日本語対応PDF作成関数 ---
def create_pdf_data(inputs, results):
    class PDF(FPDF):
        def footer(self):
            self.set_y(-15)
            self.set_font("JP", size=8)
            # 病院名と免責事項をフッターに配置
            footer_text = "〇〇病院 リハビリテーション部 | ※本レポートは意思決定の支援を目的としており、最終的な判断は医師が行ってください。"
            self.cell(0, 10, footer_text, align="C")
            self.cell(0, 10, f"Page {self.page_no()}", align="R")

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    w = pdf.w - pdf.l_margin - pdf.r_margin

    # フォント登録
    pdf.add_font("JP", "", "NotoSansJP-VariableFont_wght.ttf")
    line_height = 11 * 1.5 

    # --- 1. タイトル ---
    pdf.set_font("JP", size=18)
    pdf.cell(w, 15, "リハビリ意思決定支援レポート", ln=True, align="C")
    pdf.ln(5)

    # --- 2. 基本情報 & バイタル表 ---
    pdf.set_font("JP", size=10)
    pdf.cell(w, 8, f"作成日時: {datetime.datetime.now():%Y-%m-%d %H:%M}  /  主疾患: {inputs['主疾患']}", ln=True)
    pdf.ln(2)

    # バイタルデータの表作成
    vital_data = [
        ("項目", "数値", "項目", "数値"),
        ("SBP", f"{inputs['SBP']} mmHg", "SpO2", f"{inputs['SpO2']} %"),
        ("HR", f"{inputs['HR_rest']} bpm", "Hb", f"{inputs['Hb']} g/dL"),
        ("Alb", f"{inputs['Alb']} g/dL", "CRP", f"{inputs['CRP']} mg/dL")
    ]
    
    # 【重要】first_row_as_headings=False を追加して太字エラーを回避
    with pdf.table(line_height=8, text_align="CENTER", width=w, first_row_as_headings=False) as table:
        for data_row in vital_data:
            row = table.row()
            for datum in data_row:
                row.cell(datum)
    pdf.ln(5)

    # --- 3. 中止基準 (赤字で強調) ---
    if results["stop"]:
        pdf.set_font("JP", size=12)
        pdf.set_text_color(200, 0, 0)
        # 以前の成功例に基づき \n.join で一括描画 [cite: 21, 24, 25, 26]
        pdf.multi_cell(w, line_height, "【中止基準】\n" + "\n".join(results["stop"]))
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    # --- 4. 注意事項・補足情報 ---
    pdf.set_font("JP", size=11)
    if results["caution"]:
        pdf.multi_cell(w, line_height, "【注意事項】\n" + "\n".join(results["caution"]))
        pdf.ln(3)

    if results["advice"]:
        pdf.multi_cell(w, line_height, "【補足情報】\n" + "\n".join(results["advice"]))

    return bytes(pdf.output())

# --- 4. メインUI ---
st.set_page_config(page_title="リハビリ支援ツール", layout="wide")
st.title("🏥 高齢者リハビリ意思決定支援ツール")

rehab_logic, med_master, safety_criteria = load_all_logic("logic.xlsx")

if rehab_logic is not None:
    st.sidebar.header("📋 入力項目")
    disease = st.sidebar.selectbox("主疾患", rehab_logic['disease'].unique())
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("バイタル・血液")
        sbp = st.number_input("SBP (mmHg)", 0, 250, 120)
        spo2 = st.number_input("SpO2 (%)", 0, 100, 96)
        hr = st.number_input("HR (bpm)", 0, 250, 70)
        hb = st.number_input("Hb (g/dL)", 0.0, 20.0, 12.0)
    with col2:
        st.subheader("代謝・薬理")
        alb = st.number_input("Alb (g/dL)", 0.0, 5.0, 3.5)
        crp = st.number_input("CRP (mg/dL)", 0.0, 20.0, 0.5)
        weight_gain = st.number_input("体重増加 (kg/日)", 0.0, 5.0, 0.0)
        
        selected_drugs = []
        drug_times = {}
        drug_list = med_master['drug_name'].unique().tolist()
        for drug in drug_list:
            if drug != "なし":
                if st.checkbox(drug, key=f"chk_{drug}"):
                    selected_drugs.append(drug)
                    t = st.time_input(f"  └ {drug} の服用時間", datetime.time(9, 0), key=f"time_{drug}")
                    drug_times[drug] = t

    input_data = {'主疾患': disease, 'SBP': sbp, 'SpO2': spo2, 'HR_rest': hr, 'SBP_rest': sbp,
                  'Hb': hb, 'Alb': alb, 'CRP': crp, 'weight_gain': weight_gain,
                  '服用薬剤': selected_drugs, '薬剤ごとの時間': drug_times}

    if st.button("判定開始"):
        res = evaluate(input_data, rehab_logic, med_master, safety_criteria)
        st.divider()
        st.header("🧐 判定結果")
        if res["stop"]:
            for s in res["stop"]: st.error(s)
        if res["caution"]:
            st.warning("### 注意事項・アドバイス")
            for c in res["caution"]: st.write(f"- {c}")
        if res["advice"]:
            st.info("### 補足情報")
            for a in res["advice"]: st.write(f"- {a}")
        if not res["stop"] and not res["caution"]:
            st.success("現在の基準において安全上の懸念は見当たりません。実施可能です。")

        st.divider()
        st.subheader("📈 バイタル確認チャート")
        st.bar_chart(pd.DataFrame({"値": [sbp, spo2, hr]}, index=["SBP", "SpO2", "HR"]))

        # PDF生成・提供
        try:
            pdf_bytes = create_pdf_data(input_data, res)
            st.download_button(
                label="📄 判定レポート(PDF)をダウンロード",
                data=pdf_bytes,
                file_name=f"rehab_report_{datetime.date.today()}.pdf",
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"PDF生成エラー: {e}")