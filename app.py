import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
import os
import yaml
from datetime import datetime

ROLE_ADMIN = "管理员"
ROLE_USER = "普通用户"
ROLE_AUDITOR = "审计员"

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity_rules.yaml")


@st.cache_resource
def load_rules():
    if not os.path.exists(RULES_FILE):
        st.error(f"配置文件不存在: {RULES_FILE}")
        return None
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_valid_activities(rules):
    if not rules or "activities" not in rules:
        return set()
    return set(rules["activities"].keys())


def get_required_fields(rules):
    if not rules or "global" not in rules:
        return {"姓名", "活动名称", "金额"}
    return set(rules["global"].get("required_fields", ["姓名", "活动名称", "金额"]))


def get_activity_rule(rules, activity_name):
    if not rules or "activities" not in rules:
        return None
    return rules["activities"].get(activity_name)


def get_global_amount_defaults(rules):
    if not rules or "global" not in rules:
        return 0, 5000
    g = rules["global"]
    return g.get("default_amount_min", 0), g.get("default_amount_max", 5000)


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


def validate_row(row, row_idx, seen_signups, rules):
    errors = []
    reasons = []

    valid_activities = get_valid_activities(rules)
    required_fields = get_required_fields(rules)
    default_min, default_max = get_global_amount_defaults(rules)

    name = row.get("姓名", None)
    if pd.isna(name) or str(name).strip() == "":
        errors.append("姓名缺失")
        reasons.append("姓名为必填字段，不能为空")

    activity = row.get("活动名称", None)
    activity_str = str(activity).strip() if not pd.isna(activity) else ""
    if pd.isna(activity) or activity_str == "":
        errors.append("活动名称缺失")
        reasons.append("活动名称为必填字段，不能为空")
    elif activity_str not in valid_activities:
        errors.append(f"活动不存在: {activity_str}")
        reasons.append(f"活动「{activity_str}」不在有效活动列表中，有效活动: {', '.join(sorted(valid_activities))}")

    activity_rule = get_activity_rule(rules, activity_str) if activity_str else None

    if activity_rule:
        extra_fields = activity_rule.get("extra_required_fields", [])
        for ef in extra_fields:
            val = row.get(ef, None)
            if pd.isna(val) or str(val).strip() == "":
                errors.append(f"{ef}缺失")
                reasons.append(f"活动「{activity_str}」要求必填字段「{ef}」不能为空")

    amount = row.get("金额", None)
    if pd.isna(amount):
        errors.append("金额缺失")
        reasons.append("金额为必填字段，不能为空")
    else:
        try:
            amt = float(amount)
            if amt < 0:
                errors.append(f"金额为负: {amt}")
                reasons.append(f"金额不能为负数，当前值: {amt}")
            else:
                if activity_rule:
                    a_min = activity_rule.get("amount_min", default_min)
                    a_max = activity_rule.get("amount_max", default_max)
                else:
                    a_min, a_max = default_min, default_max

                if amt > a_max:
                    errors.append(f"金额超出上限(>{a_max}): {amt}")
                    reasons.append(f"活动「{activity_str}」金额上限为{a_max}元，当前值{amt}元超出范围")
                elif amt < a_min:
                    errors.append(f"金额低于下限(<{a_min}): {amt}")
                    reasons.append(f"活动「{activity_str}」金额下限为{a_min}元，当前值{amt}元低于范围")
        except (ValueError, TypeError):
            errors.append(f"金额格式错误: {amount}")
            reasons.append(f"金额字段格式不正确，无法解析为数字: {amount}")

    signup_key = None
    dup_check = rules.get("global", {}).get("duplicate_check", {}) if rules else {}
    dup_enabled = dup_check.get("enabled", True)
    key_fields = dup_check.get("key_fields", ["姓名", "活动名称"])

    if dup_enabled and name and not pd.isna(name) and str(name).strip() != "" and activity_str:
        key_values = []
        key_valid = True
        for kf in key_fields:
            kv = row.get(kf, None)
            if pd.isna(kv) or str(kv).strip() == "":
                key_valid = False
                break
            key_values.append(str(kv).strip())
        if key_valid:
            signup_key = tuple(key_values)
            if signup_key in seen_signups:
                errors.append(f"重复签到: {'-'.join(signup_key)}")
                reasons.append(f"{'-'.join(signup_key)} 重复签到，首次出现在第{seen_signups[signup_key] + 2}行")
            else:
                seen_signups[signup_key] = row_idx

    return errors, reasons, signup_key


def validate_dataframe(df, rules):
    all_errors = []
    seen_signups = {}
    valid_rows = []
    error_rows = []

    required_cols = get_required_fields(rules)
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        st.error(f"上传文件缺少必要列: {missing_cols}")
        return None, None, []

    for idx, row in df.iterrows():
        errors, reasons, signup_key = validate_row(row, idx, seen_signups, rules)
        if errors:
            error_info = row.to_dict()
            error_info["行号"] = idx + 2
            error_info["错误类型"] = "; ".join(errors)
            error_info["规则说明/失败原因"] = "; ".join(reasons)
            error_rows.append(error_info)
            all_errors.extend(errors)
        else:
            valid_rows.append(row)

    cleaned_df = pd.DataFrame(valid_rows) if valid_rows else pd.DataFrame(columns=df.columns)
    errors_df = pd.DataFrame(error_rows) if error_rows else pd.DataFrame()

    if not errors_df.empty:
        col_order = ["行号", "错误类型", "规则说明/失败原因"] + [
            c for c in errors_df.columns if c not in ("行号", "错误类型", "规则说明/失败原因")
        ]
        errors_df = errors_df[col_order]

    return cleaned_df, errors_df, all_errors


def render_sidebar():
    rules = load_rules()
    valid_activities = get_valid_activities(rules)

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
        st.markdown("### 📋 有效活动列表 & 规则说明")
        if rules and "activities" in rules:
            for act_name in sorted(rules["activities"]):
                act_rule = rules["activities"][act_name]
                desc = act_rule.get("rule_description", "无说明")
                a_min = act_rule.get("amount_min", 0)
                a_max = act_rule.get("amount_max", 5000)
                extra = act_rule.get("extra_required_fields", [])
                with st.expander(f"🎯 {act_name}"):
                    st.markdown(f"**规则说明**: {desc}")
                    st.markdown(f"**金额范围**: {a_min} ~ {a_max} 元")
                    if extra:
                        st.markdown(f"**额外必填字段**: {', '.join(extra)}")
                    else:
                        st.markdown("**额外必填字段**: 无")
        else:
            for act in sorted(valid_activities):
                st.markdown(f"- {act}")

        st.markdown("---")
        st.markdown("### ℹ️ 通用校验规则")
        if rules and "global" in rules:
            g = rules["global"]
            req = g.get("required_fields", ["姓名", "活动名称", "金额"])
            dup = g.get("duplicate_check", {})
            st.markdown(f"**必填字段**: {', '.join(req)}")
            if dup.get("enabled", True):
                keys = dup.get("key_fields", ["姓名", "活动名称"])
                st.markdown(f"**重复签到检测**: 开启（基于 {'+'.join(keys)}）")
            else:
                st.markdown("**重复签到检测**: 关闭")
        st.markdown("""
        1. **必填字段缺失** — 全局必填字段不能为空
        2. **金额异常** — 金额为负或超出该活动配置的金额范围
        3. **活动不存在** — 活动名称不在有效活动列表中
        4. **重复签到** — 同一人同一活动重复签到
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

            rules = load_rules()
            if st.button("🚀 开始清洗校验", type="primary", use_container_width=True):
                with st.spinner("正在逐行校验..."):
                    result = validate_dataframe(df, rules)
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
    original_df = st.session_state.original_df

    st.markdown("---")
    st.markdown("## 📊 清洗结果概览")

    total = len(original_df) if original_df is not None else 0
    valid_count = len(cleaned_df) if cleaned_df is not None and not cleaned_df.empty else 0
    error_count = len(errors_df) if errors_df is not None and not errors_df.empty else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("总记录数", total)
    col2.metric("✅ 有效记录", valid_count, delta=f"{valid_count}/{total}" if total else None)
    col3.metric("❌ 错误记录", error_count, delta=f"-{error_count}" if error_count else "0", delta_color="inverse")

    if original_df is not None and "活动名称" in original_df.columns:
        st.markdown("### 📊 活动维度统计")
        rules = load_rules()
        all_activities = set()
        if rules and "activities" in rules:
            all_activities = set(rules["activities"].keys())

        valid_activity = cleaned_df["活动名称"].value_counts() if cleaned_df is not None and not cleaned_df.empty and "活动名称" in cleaned_df.columns else pd.Series(dtype=int)
        error_activity = errors_df["活动名称"].value_counts() if errors_df is not None and not errors_df.empty and "活动名称" in errors_df.columns else pd.Series(dtype=int)

        activity_stats = []
        for act in sorted(all_activities):
            v = int(valid_activity.get(act, 0))
            e = int(error_activity.get(act, 0))
            t = v + e
            ratio = f"{e / t * 100:.1f}%" if t > 0 else "0.0%"
            activity_stats.append({"活动名称": act, "有效记录数": v, "异常记录数": e, "合计": t, "异常占比": ratio})

        other_acts = set(valid_activity.index) | set(error_activity.index)
        other_acts = other_acts - all_activities
        for act in sorted(other_acts):
            v = int(valid_activity.get(act, 0))
            e = int(error_activity.get(act, 0))
            t = v + e
            ratio = f"{e / t * 100:.1f}%" if t > 0 else "0.0%"
            activity_stats.append({"活动名称": f"{act}(未配置)", "有效记录数": v, "异常记录数": e, "合计": t, "异常占比": ratio})

        if activity_stats:
            stats_df = pd.DataFrame(activity_stats)
            st.dataframe(stats_df, use_container_width=True, hide_index=True)

            fig_act = go.Figure()
            fig_act.add_trace(go.Bar(
                name="有效记录",
                x=stats_df["活动名称"],
                y=stats_df["有效记录数"],
                marker_color="seagreen",
            ))
            fig_act.add_trace(go.Bar(
                name="异常记录",
                x=stats_df["活动名称"],
                y=stats_df["异常记录数"],
                marker_color="crimson",
            ))
            fig_act.update_layout(
                barmode="stack",
                title="各活动有效/异常记录数",
                xaxis_title="活动名称",
                yaxis_title="记录数",
            )
            st.plotly_chart(fig_act, use_container_width=True)

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
    if error_text.endswith("缺失"):
        return "必填字段缺失"
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
    st.caption("支持批量导入 · 逐行校验 · 错误明细 · 角色权限 · 规则配置")

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
