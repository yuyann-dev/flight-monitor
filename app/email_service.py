"""Email service - send verification codes and price alerts."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings


def send_email(to_addr: str, subject: str, html_body: str):
    """Send email via SMTP SSL."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, to_addr, msg.as_string())


def send_verification_code(email: str, code: str):
    body = f"您的登录验证码：{code}\n\n验证码10分钟内有效，请勿泄露。"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"登录验证码 {code}"
    msg["From"] = settings.smtp_user
    msg["To"] = email
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, email, msg.as_string())


def send_price_alert(email: str, monitor_info: dict, flights: list, analysis: dict):
    """Send price drop alert email."""
    rows = ""
    for f in flights[:5]:
        platform = f.get('platform', '携程')
        rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px;">{platform}</td>
          <td style="padding: 10px;">{f.get('airline','')} {f.get('flight_no','')}</td>
          <td style="padding: 10px;">{f.get('dep_time','')} → {f.get('arr_time','')}</td>
          <td style="padding: 10px;">{f.get('stops','直飞')}</td>
          <td style="padding: 10px; font-weight: bold; color: #e74c3c;">¥{f.get('price','')}</td>
          <td style="padding: 10px;"><a href="{f.get('link','#')}" style="color: #4A90D9;">去购买</a></td>
        </tr>
        """

    history_low = monitor_info.get('best_price_ever', analysis['best_price'])
    is_new_low = analysis['best_price'] <= history_low

    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto;">
      <h2 style="color: #e74c3c;">✈️ 低价提醒！现在入手很划算</h2>
      <p><b>{monitor_info['departure_city']}</b> → <b>{monitor_info['arrival_city']}</b>
         （{monitor_info.get('departure_date','')}）</p>

      <div style="background: #f0f7ff; padding: 15px; border-radius: 8px; margin: 15px 0;">
        <p style="margin: 5px 0;">💰 当前裸票最低价：<b style="font-size: 24px; color: #e74c3c;">¥{analysis['best_price']}</b></p>
        <p style="margin: 5px 0;">📦 含税总价（机建+燃油）：<b>¥{analysis['total_price']}</b>
           <span style="color: #999; font-size: 12px;">（机建¥{analysis['airport_fee']} + 燃油¥{analysis['fuel_fee']}）</span></p>
        <p style="margin: 8px 0 0 0; color: #666; font-size: 14px;">📊 {analysis['reason']}</p>
        {"<p style='margin: 5px 0; color: #e74c3c; font-weight: bold;'>🎉 已创历史新低！</p>" if is_new_low else ""}
      </div>

      <h3 style="font-size: 16px; margin: 20px 0 10px;">推荐航班（按价格排序）</h3>
      <table style="width: 100%; border-collapse: collapse; margin: 10px 0;">
        <tr style="background: #f5f5f5; font-weight: bold; font-size: 13px;">
          <td style="padding: 8px;">平台</td>
          <td style="padding: 8px;">航班</td>
          <td style="padding: 8px;">时间</td>
          <td style="padding: 8px;">中转</td>
          <td style="padding: 8px;">裸票价</td>
          <td style="padding: 8px;">购买</td>
        </tr>
        {rows}
      </table>

      <div style="background: #fff8e6; padding: 12px; border-radius: 8px; margin: 15px 0; font-size: 13px;">
        <b>💡 购买建议：</b><br>
        1. 点对应平台的"去购买"链接直接下单<br>
        2. 机票价格实时变动，看到合适的建议尽快下手<br>
        3. 廉航（如春秋、九元）价格低但无免费托运行李，注意行李额<br>
        4. 如已购买，请在监控页面关闭此任务
      </div>

      <p style="color: #999; font-size: 12px; margin-top: 20px;">
        监控频率：每3小时自动查询一次 · 同航线6小时内不重复打扰<br>
        数据来源：携程、飞猪等平台实时查询
      </p>
    </div>
    """
    send_email(email, f"✈️ {monitor_info['departure_city']}→{monitor_info['arrival_city']} 低价提醒 ¥{analysis['best_price']}", body)
