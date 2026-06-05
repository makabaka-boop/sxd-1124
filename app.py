import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
import os
from datetime import datetime

VALID_ACTIVITIES = {"陶艺手作", "布艺缝纫", "皮具制作", "花艺插花", "木工雕刻", "扎染体验", "刺绣工坊"}

ROLE_ADMIN = "管理员"
ROLE_USER = "普通用户"
ROLE_AUDITOR = "审计员"


def get_access_code(role):
    env_name = "ADMIN_ACCESS_CODE" if role == ROLE_ADMIN else "AUDITOR_ACCESS_CODE"
    secret_name = "admin_access_code" if role == ROLE_ADMIN else "auditor_access_code"
    try:
        return st.secrets.get(secret_name, os.environ.get(env_name, ""))
    except Exception:
        return os.environ.get(env_name, "")


@st.cache_resource
def get_shared_cache():
    return {
        "cleaned_df": None,
        "errors_df": None,
        "original_df": None,
        "validation_done": False,
        "updated_at": None,
    }


def init_session_state():
    if "role" not in st.session_state:
        st.session_state.role = ROLE_USER
    if "cleaned_df" not in st.session_state:
        st.session_state.cleaned_df = None
    if "errors_df" not in st.session_state:
        st.session_state.errors_df = None
    if "upload_key" not in st.session_state:
        st.session_state.upload_key = 0
    if "validation_done" not in st.session_state:
        st.session_state.validation_done = False
    if "original_df" not in st.session_state:
        st.session_state.original_df = None
    if "current_upload_id" not in st.session_state:
        st.session_state.current_upload_id = None


def clear_results(clear_shared=False):
    st.session_state.cleaned_df = None
    st.session_state.errors_df = None
    st.session_state.original_df = None
    st.session_state.validation_done = False
    if clear_shared:
        shared_cache = get_shared_cache()
        shared_cache["cleaned_df"] = None
        shared_cache["errors_df"] = None
        shared_cache["original_df"] = None
        shared_cache["validation_done"] = False
        shared_cache["updated_at"] = None


def save_results(cleaned_df, errors_df, original_df):
    st.session_state.cleaned_df = cleaned_df
    st.session_state.errors_df = errors_df
    st.session_state.original_df = original_df
    st.session_state.validation_done = True

    shared_cache = get_shared_cache()
    shared_cache["cleaned_df"] = cleaned_df
    shared_cache["errors_df"] = errors_df
    shared_cache["original_df"] = original_df
    shared_cache["validation_done"] = True
    shared_cache["updated_at"] = datetime.now()


def load_shared_results():
    shared_cache = get_shared_cache()
    if not shared_cache.get("validation_done"):
        return
    st.session_state.cleaned_df = shared_cache.get("cleaned_df")
    st.session_state.errors_df = shared_cache.get("errors_df")
    st.session_state.original_df = shared_cache.get("original_df")
    st.session_state.validation_done = True


def has_results():
    return st.session_state.cleaned_df is not None or st.session_state.errors_df is not None


def validate_row(row, row_idx, seen_signups):
    errors = []

    name = row.get("姓名", None)
    if pd.isna(name) or str(name).strip() == "":
        errors.append("姓名缺失")

    activity = row.get("活动名称", None)
    if pd.isna(activity) or str(activity).strip() == "":
        errors.append("活动名称缺失")
    elif str(activity).strip() not in VALID_ACTIVITIES:
        errors.append(f"活动不存在: {str(activity).strip()}")

    amount = row.get("金额", None)
    if pd.isna(amount):
        errors.append("金额缺失")
    else:
        try:
            amt = float(amount)
            if amt < 0:
                errors.append(f"金额为负: {amt}")
            elif amt > 5000:
                errors.append(f"金额异常(>5000): {amt}")
        except (ValueError, TypeError):
            errors.append(f"金额格式错误: {amount}")

    signup_key = None
    if not pd.isna(name) and str(name).strip() != "" and not pd.isna(activity) and str(activity).strip() != "":
        signup_key = (str(name).strip(), str(activity).strip())
        if signup_key in seen_signups:
            errors.append(f"重复签到: {signup_key[0]}-{signup_key[1]}")
        else:
            seen_signups[signup_key] = row_idx

    return errors, signup_key


def validate_dataframe(df):
    all_errors = []
    seen_signups = {}
    valid_rows = []
    error_rows = []

    required_cols = {"姓名", "活动名称", "金额"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        st.error(f"上传文件缺少必要列: {missing_cols}")
        return None, None, []

    for idx, row in df.iterrows():
        errors, signup_key = validate_row(row, idx, seen_signups)
        if errors:
            error_info = row.to_dict()
            error_info["行号"] = idx + 2
            error_info["错误类型"] = "; ".join(errors)
            error_rows.append(error_info)
            all_errors.extend(errors)
        else:
            valid_rows.append(row)

    cleaned_df = pd.DataFrame(valid_rows) if valid_rows else pd.DataFrame(columns=df.columns)
    errors_df = pd.DataFrame(error_rows) if error_rows else pd.DataFrame()

    if not errors_df.empty:
        col_order = ["行号", "错误类型"] + [c for c in errors_df.columns if c not in ("行号", "错误类型")]
        errors_df = errors_df[col_order]

    return cleaned_df, errors_df, all_errors


def render_sidebar():
    with st.sidebar:
        st.markdown("### 🔐 角色选择")
        requested_role = st.radio(
            "选择当前角色",
            [ROLE_ADMIN, ROLE_USER, ROLE_AUDITOR],
            index=[ROLE_ADMIN, ROLE_USER, ROLE_AUDITOR].index(st.session_state.role),
            key="role_selector",
        )

        if requested_role == ROLE_USER:
            st.session_state.role = ROLE_USER
        else:
            access_code = st.text_input(f"{requested_role}访问码", type="password")
            configured_code = get_access_code(requested_role)
            if not configured_code:
                st.warning(f"未配置{requested_role}访问码，暂不能切换到该角色。")
                st.session_state.role = ROLE_USER
            elif access_code == configured_code:
                st.session_state.role = requested_role
            else:
                st.session_state.role = ROLE_USER
                if access_code:
                    st.error("访问码错误，已按普通用户权限访问。")

        st.markdown("---")
        st.markdown("### 📋 有效活动列表")
        for act in sorted(VALID_ACTIVITIES):
            st.markdown(f"- {act}")

        st.markdown("---")
        st.markdown("### ℹ️ 校验规则")
        st.markdown("""
        1. **姓名缺失** — 姓名字段为空
        2. **金额异常** — 金额为负或超过5000元
        3. **活动不存在** — 活动名称不在有效列表中
        4. **重复签到** — 同一人在同一活动重复签到
        """)


def render_upload_section():
    st.markdown("## 📤 数据上传")
    st.markdown("上传手作体验活动的签到/反馈/费用记录表（Excel 或 CSV 格式），系统将逐行校验并输出清洗结果。")

    uploaded_file = st.file_uploader(
        "选择文件",
        type=["csv", "xlsx", "xls"],
        key=f"file_uploader_{st.session_state.upload_key}",
    )

    if uploaded_file is not None:
        try:
            file_bytes = uploaded_file.getvalue()
            upload_id = (uploaded_file.name, len(file_bytes))
            if st.session_state.current_upload_id != upload_id:
                st.session_state.current_upload_id = upload_id
                clear_results()

            file_buffer = io.BytesIO(file_bytes)
            if uploaded_file.name.endswith(".csv"):
                df = pd.read_csv(file_buffer)
            else:
                df = pd.read_excel(file_buffer)

            st.success(f"文件上传成功: {uploaded_file.name}，共 {len(df)} 行数据")

            with st.expander("原始数据预览", expanded=False):
                st.dataframe(df, use_container_width=True)

            if st.button("🚀 开始清洗校验", type="primary", use_container_width=True):
                with st.spinner("正在逐行校验..."):
                    result = validate_dataframe(df)
                    if result[0] is None:
                        clear_results(clear_shared=True)
                        return
                    cleaned_df, errors_df, all_errors = result
                    save_results(cleaned_df, errors_df, df)

        except Exception as e:
            st.error(f"文件读取失败: {e}")


def render_cleaned_results():
    if not has_results():
        return

    cleaned_df = st.session_state.cleaned_df
    errors_df = st.session_state.errors_df

    st.markdown("---")
    st.markdown("## 📊 清洗结果概览")

    total = len(st.session_state.original_df) if st.session_state.original_df is not None else 0
    valid_count = len(cleaned_df) if cleaned_df is not None and not cleaned_df.empty else 0
    error_count = len(errors_df) if errors_df is not None and not errors_df.empty else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("总记录数", total)
    col2.metric("✅ 有效记录", valid_count, delta=f"{valid_count}/{total}" if total else None)
    col3.metric("❌ 错误记录", error_count, delta=f"-{error_count}" if error_count else "0", delta_color="inverse")

    if cleaned_df is not None and not cleaned_df.empty:
        st.markdown("### ✅ 清洗后有效数据")
        st.dataframe(cleaned_df, use_container_width=True)

        if "活动名称" in cleaned_df.columns:
            st.markdown("### 📈 有效数据 — 活动分布")
            activity_counts = cleaned_df["活动名称"].value_counts().reset_index()
            activity_counts.columns = ["活动名称", "人数"]
            fig = px.bar(activity_counts, x="活动名称", y="人数", color="活动名称", title="各活动有效签到人数")
            st.plotly_chart(fig, use_container_width=True)

        if "金额" in cleaned_df.columns:
            st.markdown("### 💰 有效数据 — 金额分布")
            fig2 = px.histogram(cleaned_df, x="金额", nbins=30, title="有效记录金额分布", marginal="box")
            st.plotly_chart(fig2, use_container_width=True)


def render_error_details():
    errors_df = st.session_state.errors_df
    if errors_df is None or errors_df.empty:
        return

    st.markdown("---")
    st.markdown("## ❌ 错误明细")

    st.dataframe(errors_df, use_container_width=True)

    error_type_list = []
    for _, row in errors_df.iterrows():
        for et in str(row["错误类型"]).split("; "):
            error_type_list.append(normalize_error_type(et.strip()))
    error_type_series = pd.Series(error_type_list, name="错误类型")

    st.markdown("### 📉 错误类型统计")
    type_counts = error_type_series.value_counts().reset_index()
    type_counts.columns = ["错误类型", "次数"]

    fig_err = px.pie(type_counts, names="错误类型", values="次数", title="错误类型占比", hole=0.4)
    st.plotly_chart(fig_err, use_container_width=True)

    if "行号" in errors_df.columns:
        fig_err2 = go.Figure()
        fig_err2.add_trace(go.Scatter(
            x=errors_df["行号"],
            y=[1] * len(errors_df),
            mode="markers+text",
            text=errors_df["错误类型"],
            textposition="top center",
            marker=dict(size=10, color="crimson"),
            name="错误行",
        ))
        fig_err2.update_layout(
            title="错误行分布（X轴为行号）",
            xaxis_title="行号",
            yaxis_visible=False,
            height=300,
        )
        st.plotly_chart(fig_err2, use_container_width=True)


def render_download_section():
    if not has_results():
        return

    st.markdown("---")
    st.markdown("## ⬇️ 下载中心")

    col1, col2 = st.columns(2)

    with col1:
        cleaned_df = st.session_state.cleaned_df
        if cleaned_df is not None and not cleaned_df.empty:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                cleaned_df.to_excel(writer, index=False, sheet_name="清洗后数据")
            buffer.seek(0)
            st.download_button(
                label="📥 下载清洗后数据 (Excel)",
                data=buffer,
                file_name=f"cleaned_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    with col2:
        errors_df = st.session_state.errors_df
        if errors_df is not None and not errors_df.empty:
            buffer_err = io.BytesIO()
            with pd.ExcelWriter(buffer_err, engine="openpyxl") as writer:
                errors_df.to_excel(writer, index=False, sheet_name="错误明细")
            buffer_err.seek(0)
            st.download_button(
                label="📥 下载错误明细 (Excel)",
                data=buffer_err,
                file_name=f"error_details_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )


def render_clear_cache():
    if st.session_state.validation_done:
        st.markdown("---")
        if st.button("🗑️ 清除缓存并重新上传", use_container_width=True):
            clear_results(clear_shared=True)
            st.session_state.upload_key += 1
            st.session_state.current_upload_id = None
            st.rerun()


def normalize_error_type(error_text):
    if error_text.startswith("金额"):
        return "金额异常"
    if error_text.startswith("活动不存在") or error_text == "活动名称缺失":
        return "活动不存在"
    if error_text.startswith("重复签到"):
        return "重复签到"
    if error_text == "姓名缺失":
        return "姓名缺失"
    return error_text


def render_no_result_notice(role):
    if has_results():
        return
    if role == ROLE_ADMIN:
        return
    st.info("暂无可查看的清洗结果，请等待管理员上传并完成清洗。")


def main():
    st.set_page_config(page_title="手作体验活动数据清洗平台", page_icon="🎨", layout="wide")
    init_session_state()
    load_shared_results()
    render_sidebar()

    st.title("🎨 手作体验活动数据清洗平台")
    st.caption("支持批量导入 · 逐行校验 · 错误明细 · 角色权限")

    role = st.session_state.role

    if role == ROLE_ADMIN:
        render_upload_section()
        render_cleaned_results()
        render_error_details()
        render_download_section()
        render_clear_cache()

    elif role == ROLE_USER:
        render_no_result_notice(role)
        render_cleaned_results()
        render_download_section()

    elif role == ROLE_AUDITOR:
        render_no_result_notice(role)
        render_error_details()
        st.info("🔒 审计员角色：仅可查看错误明细，无法修改数据或重新上传。")


if __name__ == "__main__":
    main()
