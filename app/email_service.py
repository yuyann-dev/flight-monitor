"""Email service: verification codes and price alerts."""
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from app.config import settings
from app.airlines import baggage_text

# 收件箱展示的发件人名称（软件名）；SMTP 信封投递地址仍用认证邮箱 smtp_user，不随显示名改变
APP_SENDER_NAME = "机票监控"


def _sender_header() -> str:
    """构造 From 头：显示名为软件名，路由/投递地址为认证发件邮箱。"""
    return formataddr((str(Header(APP_SENDER_NAME, "utf-8")), settings.smtp_user))


def send_email(to_addr: str, subject: str, html_body: str):
    """通过 SMTP SSL 发送 HTML 邮件。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _sender_header()
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
    msg["From"] = _sender_header()
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
        # 回退：当前价 ≤ 历史最低即视为新低（含打平）
        is_new_low = bool(
            history_low is not None and analysis["best_price"] <= history_low
        )
    new_low_banner = (
        '<p style="margin:5px 0; color:#27ae60; font-weight:bold;">已创监控以来历史新低</p>'
        if is_new_low else ""
    )

    # 命中优先于临期；档位：①命中且高分 ②命中但低分 ③未命中但高分 ④未命中低分但临期；其余不发
    hit = bool(analysis.get("hit_target"))
    strong = (analysis.get("score") or 0) >= 60
    near = bool(analysis.get("near_deadline"))
    if hit and strong:
        title = "已达心理价位且当前价格偏低，强烈建议购买"
        title_color = "#c0392b"
        subject_prefix = "建议购买提醒"
        tip_line = ("当前裸票价已达到您设定的心理预期；结合距出行时间、当前价格在历史价格中的位置以及近期走势看，"
                    "当前价格已处于偏低位置，后续继续明显下降的空间可能有限，建议您结合行程和预算适时决定是否购票。")
    elif hit:
        title = "已达到心理价位，后续仍可能下降"
        title_color = "#e67e22"
        subject_prefix = "达到心理价提醒"
        tip_line = ("当前裸票价已达到您设定的心理预期；但结合距出行时间、当前价格在历史价格中的位置以及近期走势看，"
                    "当前价格还没有明显处于低位，后续仍可能波动或进一步下降，建议您持续关注、结合出行计划谨慎决定。")
    elif strong:
        title = "当前价格偏低，建议购买"
        title_color = "#e67e22"
        subject_prefix = "建议购买提醒"
        tip_line = ("当前裸票价还没有达到您设定的心理预期；但结合距出行时间、当前价格在历史价格中的位置以及近期走势看，"
                    "当前价格已处于偏低位置，后续继续明显下降的空间可能有限，建议您结合行程和预算适时决定是否购票。")
    elif near:
        title = "临近出发，继续等待的降价空间可能有限"
        title_color = "#e67e22"
        subject_prefix = "建议购买提醒"
        tip_line = ("当前裸票价尚未达到您的心理预期；但距出发日期已较近，结合历史价格位置与近期走势看，"
                    "后续大幅下降的空间可能有限，且存在临近涨价可能。请结合行程是否确定与预算权衡，自行把握购票时机。")
    else:
        # 双不满足且不临期：不生成提醒（调度层 should=False 已拦截；此处防御性返回，不发兜底邮件）
        return

    # 创监控以来历史新低时，在邮件主题与大标题前加醒目标签
    if is_new_low:
        title = "【历史新低】" + title
        subject_prefix = "【历史新低】" + subject_prefix

    target_price = analysis.get("target_price")
    target_row = (
        f'<p style="margin:5px 0; color:#555;">您的心理价位（裸票价口径）：<b>¥{target_price}</b></p>'
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
        1. 机票价格实时变动，若符合预期请结合预算尽早安排；<br>
        2. 不同航司行李额度不同，下单前请在购买页确认；<br>
        3. 如已购票，可在监控页面停止该任务；<br>
        4. 机建燃油为参考价格，具体请以购票结算页实际收取金额为准。
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
