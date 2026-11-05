"""Email service: verification codes and price alerts."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings
from app.airlines import baggage_text


def send_email(to_addr: str, subject: str, html_body: str):
    """通过 SMTP SSL 发送 HTML 邮件。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, to_addr, msg.as_string())


def send_verification_code(email: str, code: str):
    """发送登录验证码（纯文本）。"""
    body = f"您的登录验证码：{code}\n\n验证码10分钟内有效，请勿泄露给他人。"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"登录验证码 {code}"
    msg["From"] = settings.smtp_user
    msg["To"] = email
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, email, msg.as_string())


def _short_time(time_str: str) -> str:
    """时间字符串取 HH:MM。"""
    if not time_str:
        return "--"
    try:
        if "T" in time_str:
            part = time_str.split("T")[1]
        elif " " in time_str:
            part = time_str.split(" ")[1]
        else:
            part = time_str
        return part[:5]
    except Exception:
        return "--"


def send_price_alert(email: str, monitor_info: dict, flights: list, analysis: dict,
                     advice: str = "建议考虑购票", is_new_low: bool | None = None):
    """发送价格提醒邮件，根据advice类型用不同文案。

    邮件发送失败只记录日志、不向外抛异常，避免 SMTP 抖动导致本轮采集数据被回滚。
    is_new_low 由调用方基于“更新前历史最低”判定；缺省时再做保守回退判断。
    """
    rows = ""
    for f in flights[:5]:
        direct_text = "直飞" if f.get("is_direct") else "中转"
        dep_t = _short_time(f.get("dep_time", ""))
        arr_t = _short_time(f.get("arr_time", ""))
        rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px;">{f.get('airline', '')} {f.get('flight_no', '')}<br>
              <span style="font-size:11px; color:#999;">{direct_text}</span></td>
          <td style="padding: 10px; font-size:13px;">{dep_t} → {arr_t}<br>
              <span style="font-size:11px; color:#999;">{f.get('date', '')}</span></td>
          <td style="padding: 10px; font-weight: bold; color: #e74c3c;">¥{f.get('price', '')}</td>
          <td style="padding: 10px;"><a href="{f.get('link', '#')}" style="color:#4A90D9;">去购买</a></td>
        </tr>
        """

    history_low = monitor_info.get("best_price_ever")
    if is_new_low is None:
        # 回退：仅当当前价严格小于历史最低才视为新低
        is_new_low = bool(
            history_low is not None and analysis["best_price"] < history_low
        )
    new_low_banner = (
        '<p style="margin:5px 0; color:#27ae60; font-weight:bold;">已创监控以来历史新低</p>'
        if is_new_low else ""
    )

    # 按“命中心理价 / 行情分达标”的组合选择客观、不绝对的措辞
    hit = bool(analysis.get("hit_target"))
    strong = (analysis.get("score") or 0) >= 60
    if hit and strong:
        title = "同时达到心理价且行情信号支持，可关注入手"
        title_color = "#c0392b"
        subject_prefix = "关注入手"
        tip_line = ("当前裸票价已达到你设定的心理价位，且综合距出行时间、价格位置与趋势后行情支持度较高；"
                    "机票价格随供需与日期波动，是否下单请结合你的行程自行决定。")
    elif hit:
        title = "已达到你设定的心理价位（行情信号偏中性）"
        title_color = "#e67e22"
        subject_prefix = "达到心理价"
        tip_line = ("当前裸票价已不高于你设定的心理价位；但从现有样本与趋势看行情信号偏中性，"
                    "当前未必处于近期最低点，后续仍可能波动或下行，仅供参考，请结合出行时间自行判断是否入手。")
    elif strong:
        title = "行情上处于相对可关注价位（未达心理价）"
        title_color = "#e67e22"
        subject_prefix = "行情关注"
        tip_line = ("当前虽未达到你设定的心理价位，但综合距出行时间、价格位置与趋势，当前处于相对值得关注的区间；"
                    "后续走势仍存在不确定性，请按你的预算与行程自行权衡。")
    else:
        title = "价格出现变化，请留意"
        title_color = "#e67e22"
        subject_prefix = "价格提醒"
        tip_line = "当前价格出现变化，请结合自身需求与出行时间自行判断。"

    target_price = analysis.get("target_price")
    target_row = (
        f'<p style="margin:5px 0; color:#555;">你的心理价位（裸票价口径）：<b>¥{target_price}</b></p>'
        if target_price else ""
    )

    body = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 640px; margin: 0 auto;">
      <h2 style="color:{title_color}; margin-bottom:8px;">{title}</h2>
      <p><b>{monitor_info['departure_city']}</b> → <b>{monitor_info['arrival_city']}</b>
         （{monitor_info.get('departure_date', '')}）</p>

      <div style="background:#f0f7ff; padding:15px; border-radius:8px; margin:15px 0;">
        <p style="margin:5px 0;">当前裸票最低价：
          <b style="font-size:24px; color:#e74c3c;">¥{analysis['best_price']}</b></p>
        <p style="margin:5px 0;">含税总价（机建+燃油）：<b>¥{analysis['total_price']}</b>
          <span style="color:#999; font-size:12px;">
          （机建¥{analysis['airport_fee']} + 燃油¥{analysis['fuel_fee']}）</span></p>
        {target_row}
        {new_low_banner}
        <p style="margin:8px 0 0 0; color:#666; font-size:13px;">{analysis['reason']}</p>
      </div>

      <p style="color:#555; font-size:14px; margin:10px 0;">{tip_line}</p>

      <h3 style="font-size:16px; margin:20px 0 10px;">推荐航班（按价格排序）</h3>
      <table style="width:100%; border-collapse:collapse; margin:10px 0;">
        <tr style="background:#f5f5f5; font-weight:bold; font-size:13px;">
          <td style="padding:8px;">航班</td>
          <td style="padding:8px;">时间</td>
          <td style="padding:8px;">裸票价</td>
          <td style="padding:8px;">购买</td>
        </tr>
        {rows}
      </table>

      <div style="background:#fff8e6; padding:12px; border-radius:8px; margin:15px 0; font-size:13px; color:#666;">
        <b>温馨提示：</b><br>
        1. 机票价格实时变动，看到合适的建议尽快下单；<br>
        2. 不同航司行李额度不同，下单前请在购买页确认；<br>
        3. 如已购票，可在监控页面停止该任务。
      </div>

      <p style="color:#999; font-size:12px; margin-top:20px;">
        本邮件由机票监控系统自动发送 · 数据来源：飞猪 / 飞常准实时比价<br>
        价格为统计判断，实际可能受节假日、供需等因素影响，仅供参考
      </p>
    </div>
    """
    try:
        send_email(
            email,
            f"{monitor_info['departure_city']}→{monitor_info['arrival_city']} {subject_prefix} ¥{analysis['best_price']}",
            body,
        )
    except Exception as exc:
        # 发信失败不影响采集与落库，仅打印便于排查
        print(f"[email] send_price_alert to {email} failed: {exc}")
