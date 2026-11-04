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
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #333;">机票监控系统登录</h2>
      <p>您的登录验证码为：</p>
      <p style="font-size: 32px; font-weight: bold; color: #4A90D9; letter-spacing: 8px;">{code}</p>
      <p style="color: #999; font-size: 13px;">验证码 10 分钟内有效，请勿泄露给他人。</p>
    </div>
    """
    send_email(email, "【机票监控】登录验证码", body)


def send_price_alert(email: str, monitor_info: dict, flights: list, analysis: dict):
    """Send price drop alert email."""
    rows = ""
    for f in flights[:5]:
        rows += f"""
        <tr style="border-bottom: 1px solid #eee;">
          <td style="padding: 10px;">{f.get('airline','')} {f.get('flight_no','')}</td>
          <td style="padding: 10px;">{f.get('dep_time','')} → {f.get('arr_time','')}</td>
          <td style="padding: 10px;">{f.get('stops','直飞')}</td>
          <td style="padding: 10px; font-weight: bold; color: #e74c3c;">¥{f.get('total_price','')}</td>
          <td style="padding: 10px;"><a href="{f.get('link','#')}" style="color: #4A90D9;">去购买</a></td>
        </tr>
        """

    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto;">
      <h2 style="color: #e74c3c;">✈️ 低价提醒！现在入手很划算</h2>
      <p><b>{monitor_info['departure_city']}</b> → <b>{monitor_info['arrival_city']}</b>
         （{monitor_info.get('departure_date','')}）</p>

      <div style="background: #f0f7ff; padding: 15px; border-radius: 8px; margin: 15px 0;">
        <p style="margin: 5px 0;">💰 当前最低价：<b style="font-size: 24px; color: #e74c3c;">¥{analysis['best_price']}</b>
           （含税 ¥{analysis['total_price']}）</p>
        <p style="margin: 5px 0; color: #666;">📊 {analysis['reason']}</p>
      </div>

      <table style="width: 100%; border-collapse: collapse; margin: 15px 0;">
        <tr style="background: #f5f5f5; font-weight: bold;">
          <td style="padding: 10px;">航班</td>
          <td style="padding: 10px;">时间</td>
          <td style="padding: 10px;">中转</td>
          <td style="padding: 10px;">含税价</td>
          <td style="padding: 10px;">链接</td>
        </tr>
        {rows}
      </table>

      <p style="color: #999; font-size: 12px;">
        机建费 ¥{analysis['airport_fee']} + 燃油附加费 ¥{analysis['fuel_fee']} 已计入含税总价。<br>
        如已购买，请在监控页面关闭此任务。
      </p>
    </div>
    """
    send_email(email, f"✈️ 低价提醒：{monitor_info['departure_city']}→{monitor_info['arrival_city']} ¥{analysis['best_price']}", body)
