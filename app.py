import streamlit as st
import pandas as pd
import datetime
from fpdf import FPDF
import io

# --- 必ずファイルの先頭で呼び出す ---
st.set_page_config(page_title="リハビリ支援ツール", layout="wide")


# ============================================================
# ログイン認証
# ============================================================
# ユーザー情報（実運用では secrets.toml や DB に移す）
_USERS = {
    "admin":  "password123",
    "rehab":  "rehab2024",
}

def _login_page():
    st.title("🏥 リハビリ支援ツール")
    st.subheader("ログイン")
    username = st.text_input("ユーザー名")
    password = st.text_input("パスワード", type="password")
    if st.button("ログイン"):
        if _USERS.get(username) == password:
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.rerun()
        else:
            st.error("ユーザー名またはパスワードが正しくありません")

# 未ログインならログイン画面だけ表示して終了
if not st.session_state.get("authenticated"):
    _login_page()
    st.stop()


# ============================================================
# 1. データ読み込み
# ============================================================
def load_all_logic(file_path):
    try:
        rehab_df    = pd.read_excel(file_path, sheet_name='rehab_logic')
        med_df      = pd.read_excel(file_path, sheet_name='medication_master')
        safety_df   = pd.read_excel(file_path, sheet_name='safety_criteria')
        return rehab_df, med_df, safety_df
    except Exception as e:
        st.error(f"Excelの読み込みに失敗しました: {e}")
        return None, None, None


# ============================================================
# 2. 安全な比較演算子ルックアップ（eval 廃止）
# ============================================================
_OPS = {
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

def _compare(val, operator: str, threshold) -> bool:
    """
    数値同士を安全に比較する。
    - val / threshold が数値でない場合は False を返す（型安全ガード）
    - operator が未定義の場合も False を返す
    """
    if not isinstance(val, (int, float)):
        return False
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return False
    op_fn = _OPS.get(str(operator).strip())
    if op_fn is None:
        return False
    return op_fn(val, threshold)


# ============================================================
# 3. 判定ロジック
# ============================================================
def evaluate(inputs, rehab_df, med_df, safety_df):
    results = {"stop": [], "caution": [], "advice": []}

    # A. 絶対中止基準
    for _, row in safety_df.iterrows():
        val = inputs.get(row['metric'])
        if val is not None and _compare(val, row['operator'], row['limit_value']):
            results["stop"].append(
                f"{row['alert_level']}: {row['metric']} が基準外です"
            )

    # B. 疾患別・共通ロジック
    target_rules = rehab_df[
        (rehab_df['disease'] == inputs['主疾患']) | (rehab_df['disease'] == '共通')
    ]
    for _, row in target_rules.iterrows():
        val = inputs.get(row['metric'])
        if val is not None and _compare(val, row['operator'], row['threshold']):
            results["caution"].append(row['message'])

    # C. 薬剤判定
    selected_drugs = inputs.get('服用薬剤', [])
    drug_times     = inputs.get('薬剤ごとの時間', {})
    if selected_drugs:
        now = datetime.datetime.now()
        for drug in selected_drugs:
            med_info = med_df[med_df['drug_name'] == drug]
            if med_info.empty:
                continue
            med_row   = med_info.iloc[0]
            dose_time = drug_times.get(drug)
            if dose_time is None:
                continue

            dose_datetime = datetime.datetime.combine(datetime.date.today(), dose_time)
            diff_h = (now - dose_datetime).total_seconds() / 3600

            # 日またぎ対策：服用時間が未来（＝昨日服用）なら 24h 加算
            if diff_h < 0:
                diff_h += 24

            if med_row['t_max_start'] <= diff_h <= med_row['t_max_end']:
                results["caution"].append(
                    f"Caution【{drug}】: {med_row['risk_message']}"
                    f"（服用から {diff_h:.1f}h 経過）"
                )
            else:
                results["advice"].append(
                    f"Info【{drug}】: 現在はピーク時間外"
                    f"（服用から {diff_h:.1f}h 経過）"
                )
    return results


# ============================================================
# 4. 日本語対応 PDF 作成
# ============================================================
FONT_PATH   = "NotoSansJP-VariableFont_wght.ttf"
FOOTER_TEXT = (
    "〇〇病院 リハビリテーション部 | "
    "※本レポートは意思決定の支援を目的としており、最終的な判断は医師が行ってください。"
)
LINE_H = 11 * 1.5


class _RehabPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("JP", size=8)
        # フッター左側：免責テキスト（固定幅）
        # フッター右側：ページ番号
        # 2 つを別 cell で並べることで上書きを防ぐ
        page_label = f"Page {self.page_no()}"
        page_w = self.get_string_width(page_label) + 4
        text_w = self.w - self.l_margin - self.r_margin - page_w
        self.cell(text_w, 10, FOOTER_TEXT, align="L")
        self.cell(page_w, 10, page_label,  align="R")


def create_pdf_data(inputs: dict, results: dict) -> bytes:
    pdf = _RehabPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("JP", "", FONT_PATH)
    pdf.add_page()
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    w = pdf.w - pdf.l_margin - pdf.r_margin

    # ---- タイトル ----
    pdf.set_font("JP", size=18)
    pdf.cell(w, 15, "リハビリ意思決定支援レポート", ln=True, align="C")
    pdf.ln(5)

    # ---- 基本情報 ----
    pdf.set_font("JP", size=10)
    pdf.cell(
        w, 8,
        f"作成日時: {datetime.datetime.now():%Y-%m-%d %H:%M}  /  主疾患: {inputs['主疾患']}",
        ln=True
    )
    pdf.ln(2)

    # ---- バイタル表 ----
    vital_data = [
        ("項目", "数値", "項目", "数値"),
        ("SBP",  f"{inputs['SBP']} mmHg",   "SpO2", f"{inputs['SpO2']} %"),
        ("HR",   f"{inputs['HR_rest']} bpm", "Hb",   f"{inputs['Hb']} g/dL"),
        ("Alb",  f"{inputs['Alb']} g/dL",   "CRP",  f"{inputs['CRP']} mg/dL"),
    ]
    with pdf.table(
        line_height=8, text_align="CENTER",
        width=w, first_row_as_headings=False
    ) as table:
        for data_row in vital_data:
            row = table.row()
            for datum in data_row:
                row.cell(datum)
    pdf.ln(5)

    # ---- 中止基準（赤字） ----
    if results["stop"]:
        pdf.set_font("JP", size=12)
        pdf.set_text_color(200, 0, 0)
        pdf.multi_cell(
            w, LINE_H,
            "【中止基準】\n" + "\n".join(results["stop"])
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    # ---- 注意事項 ----
    pdf.set_font("JP", size=11)
    if results["caution"]:
        pdf.multi_cell(w, LINE_H, "【注意事項】\n" + "\n".join(results["caution"]))
        pdf.ln(3)

    # ---- 補足情報 ----
    if results["advice"]:
        pdf.multi_cell(w, LINE_H, "【補足情報】\n" + "\n".join(results["advice"]))

    return bytes(pdf.output())


# ============================================================
# 5. メイン UI
# ============================================================
col_title, col_logout = st.columns([8, 1])
with col_title:
    st.title("🏥 高齢者リハビリ意思決定支援ツール")
with col_logout:
    st.write("")  # 縦位置を合わせる
    if st.button("ログアウト"):
        st.session_state.clear()
        st.rerun()

rehab_logic, med_master, safety_criteria = load_all_logic("logic.xlsx")

if rehab_logic is not None:
    st.sidebar.header("📋 入力項目")
    disease = st.sidebar.selectbox("主疾患", rehab_logic['disease'].unique())

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("バイタル・血液")
        sbp  = st.number_input("SBP (mmHg)", 0,   250,  120)
        spo2 = st.number_input("SpO2 (%)",   0,   100,   96)
        hr   = st.number_input("HR (bpm)",   0,   250,   70)
        hb   = st.number_input("Hb (g/dL)",  0.0, 20.0, 12.0)

    with col2:
        st.subheader("代謝・薬理")
        alb         = st.number_input("Alb (g/dL)",      0.0, 5.0,  3.5)
        crp         = st.number_input("CRP (mg/dL)",     0.0, 20.0, 0.5)
        weight_gain = st.number_input("体重増加 (kg/日)", 0.0, 5.0,  0.0)

        selected_drugs: list[str] = []
        drug_times:     dict      = {}
        drug_list = med_master['drug_name'].unique().tolist()
        for drug in drug_list:
            if drug != "なし":
                if st.checkbox(drug, key=f"chk_{drug}"):
                    selected_drugs.append(drug)
                    t = st.time_input(
                        f"  └ {drug} の服用時間",
                        datetime.time(9, 0),
                        key=f"time_{drug}"
                    )
                    drug_times[drug] = t

    input_data = {
        '主疾患':       disease,
        'SBP':          sbp,
        'SBP_rest':     sbp,
        'SpO2':         spo2,
        'HR_rest':      hr,
        'Hb':           hb,
        'Alb':          alb,
        'CRP':          crp,
        'weight_gain':  weight_gain,
        '服用薬剤':     selected_drugs,
        '薬剤ごとの時間': drug_times,
    }

    if st.button("判定開始"):
        res = evaluate(input_data, rehab_logic, med_master, safety_criteria)

        st.divider()
        st.header("🧐 判定結果")

        if res["stop"]:
            for s in res["stop"]:
                st.error(s)
        if res["caution"]:
            st.warning("### 注意事項・アドバイス")
            for c in res["caution"]:
                st.write(f"- {c}")
        if res["advice"]:
            st.info("### 補足情報")
            for a in res["advice"]:
                st.write(f"- {a}")
        if not res["stop"] and not res["caution"]:
            st.success("現在の基準において安全上の懸念は見当たりません。実施可能です。")

        st.divider()
        st.subheader("📈 バイタル確認チャート")
        st.bar_chart(
            pd.DataFrame({"値": [sbp, spo2, hr]}, index=["SBP", "SpO2", "HR"])
        )

        # PDF 生成・ダウンロード
        try:
            pdf_bytes = create_pdf_data(input_data, res)
            st.download_button(
                label="📄 判定レポート(PDF)をダウンロード",
                data=pdf_bytes,
                file_name=f"rehab_report_{datetime.date.today()}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            st.error(f"PDF生成エラー: {e}")
