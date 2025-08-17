# app.py (Sadeleştirilmiş ve Anında Güncelleme Sorunu Düzeltilmiş Final Versiyon)
import os
import json
import requests
import sys
import smtplib
import time
import base64
from flask import Flask, render_template_string, request, jsonify, Response
from flask_socketio import SocketIO
from flask_apscheduler import APScheduler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from gold_club_bot import GoldClubBot

# Veritabanı yönetimi için SQLAlchemy'yi ekliyoruz
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc

# --- Flask ve Veritabanı Kurulumu ---
app = Flask(__name__)

# Render'ın sağladığı DATABASE_URL ortam değişkenini kullanarak veritabanına bağlanıyoruz.
# 'postgres://' ile başlayan URL'leri SQLAlchemy'nin beklediği 'postgresql://' formatına çeviriyoruz.
database_uri = os.environ.get('DATABASE_URL', 'sqlite:///local_dev.db')
if database_uri.startswith("postgres://"):
    database_uri = database_uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-super-secret-key-for-local-dev')

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='eventlet')
scheduler = APScheduler()

# --- Veritabanı Modeli (Sadeleştirildi) ---
# Artık kanal sayısı veya json içeriği tutmuyoruz. Sadece linkin kendisi var.
class GeneratedLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    m3u_url = db.Column(db.Text, nullable=False) # TEXT olarak değiştirdik, linkler uzun olabilir
    expiry_date = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        return {
            'id': self.id,
            'm3u_url': self.m3u_url,
            'expiry_date': self.expiry_date,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# --- Yapılandırma ---
config = {}
def load_config():
    """Uygulama yapılandırmasını ortam değişkenlerinden yükler."""
    global config
    print("Yapılandırma ortam değişkenlerinden yükleniyor...")
    config['email'] = os.environ.get('GCB_EMAIL')
    config['password'] = os.environ.get('GCB_PASSWORD')
    if not config['email'] or not config['password']:
        print("KRİTİK HATA: 'GCB_EMAIL' ve 'GCB_PASSWORD' ortam değişkenleri ayarlanmamış.")
        sys.exit(1)
    config['scheduler'] = {"enabled": os.environ.get('SCHEDULER_ENABLED', 'false').lower() == 'true', "hour": int(os.environ.get('SCHEDULER_HOUR', 4)), "minute": int(os.environ.get('SCHEDULER_MINUTE', 0)), "target_group": os.environ.get('SCHEDULER_TARGET_GROUP', 'TURKISH')}
    config['notification'] = {"enabled": os.environ.get('NOTIF_ENABLED', 'false').lower() == 'true', "smtp_server": os.environ.get('SMTP_SERVER'), "smtp_port": int(os.environ.get('SMTP_PORT', 587)), "sender_email": os.environ.get('SENDER_EMAIL'), "sender_password": os.environ.get('SENDER_PASSWORD'), "receiver_email": os.environ.get('RECEIVER_EMAIL')}
    print("Yapılandırma başarıyla yüklendi.")

# --- E-posta Fonksiyonu ---
def send_email_notification(subject, body, attachment_content=None, attachment_filename=None):
    notif_config = config.get('notification', {})
    if not notif_config.get('enabled') or not notif_config.get('sender_email'): return
    try:
        msg = MIMEMultipart(); msg['From'] = notif_config['sender_email']; msg['To'] = notif_config['receiver_email']; msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        if attachment_content and attachment_filename:
            part = MIMEBase('application', 'octet-stream'); part.set_payload(attachment_content.encode('utf-8')); encoders.encode_base64(part); part.add_header('Content-Disposition', f'attachment; filename="{attachment_filename}"'); msg.attach(part)
        server = smtplib.SMTP(notif_config['smtp_server'], notif_config['smtp_port']); server.starttls(); server.login(notif_config['sender_email'], notif_config['sender_password']); server.send_message(msg); server.quit()
        print(f"Bildirim e-postası başarıyla gönderildi: '{subject}'")
    except Exception as e: print(f"E-posta gönderilemedi: {e}")

# --- BOT İŞLEMCİ FONKSİYONU (Sadeleştirildi) ---
def process_bot_run(target_group, sid=None):
    result_data = GoldClubBot(email=config['email'], password=config['password'], socketio=socketio, sid=sid).run_full_process()
    if "error" in result_data or not result_data.get('url'):
        error_message = result_data.get('error', 'Bilinmeyen bir hata oluştu veya link alınamadı.')
        send_email_notification("Link Oluşturma Başarısız Oldu", f"Hata: {error_message}")
        return {"error": error_message}

    new_link = GeneratedLink(
        m3u_url=result_data['url'],
        expiry_date=result_data['expiry']
    )
    db.session.add(new_link)
    db.session.commit()
    
    new_link_data = new_link.to_dict()

    subject = f"Yeni M3U Linki Oluşturuldu"
    body = f"<p>Yeni bir M3U linki başarıyla oluşturuldu.</p><ul><li><b>Link:</b> {result_data['url']}</li><li><b>Son Kullanma:</b> {result_data['expiry']}</li></ul>"
    send_email_notification(subject, body)

    return {"new_link": new_link_data}

# --- Zamanlanmış Görev ---
def scheduled_task():
    print("Zamanlanmış görev başlatılıyor...");
    with app.app_context():
        target_group = config.get('scheduler', {}).get('target_group', 'TURKISH')
        process_bot_run(target_group=target_group)
    print("Zamanlanmış görev tamamlandı.")

# --- HTML TEMPLATE (Sadeleştirilmiş Arayüz ve Anında Güncelleme Düzeltmesi) ---
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>M3U Link Üretici</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #101014; --bg-card: rgba(30, 30, 35, 0.5); --border-color: rgba(255, 255, 255, 0.1); --text-primary: #f0f0f0; --text-secondary: #a0a0a0; --accent-grad: linear-gradient(90deg, #8A2387, #E94057, #F27121); --success-color: #1ed760; --error-color: #f44336; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Manrope', sans-serif; background: var(--bg-dark); color: var(--text-primary); font-size: 15px; overflow-x: hidden; }
        body::before { content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 15% 25%, #8a238744, transparent 30%), radial-gradient(circle at 85% 75%, #f2712133, transparent 40%); z-index: -1; }
        .container { max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
        h1 { text-align: center; margin-bottom: 2rem; font-weight: 800; }
        .dashboard { display: grid; grid-template-columns: minmax(300px, 1fr) 2fr; gap: 2rem; align-items: flex-start; }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 0.75rem; width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; margin-top: 1.5rem; text-decoration: none; }
        .btn:hover:not(:disabled) { transform: translateY(-3px); box-shadow: 0 4px 20px rgba(233, 64, 87, 0.3); }
        .btn:disabled { background: #333; cursor: not-allowed; }
        .btn .spinner { animation: spin 1s linear infinite; }
        #log-container { margin-top: 1rem; background-color: rgba(0,0,0,0.3); padding: 1rem; border-radius: 8px; height: 350px; overflow-y: auto; font-family: 'Fira Code', monospace; font-size: 0.85rem; }
        .history-table { width: 100%; border-collapse: collapse; }
        .history-table th, .history-table td { padding: 1rem 0.75rem; border-bottom: 1px solid var(--border-color); text-align: left; vertical-align: top; }
        .history-table th { font-weight: 600; color: var(--text-secondary); }
        .m3u-cell { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
        .m3u-link { word-break: break-all; background: rgba(0,0,0,0.2); padding: 0.5rem; border-radius: 4px; font-family: monospace; flex-grow: 1; }
        .btn-copy { background: none; border: 1px solid var(--border-color); color: var(--text-secondary); padding: 0.4rem 0.8rem; border-radius: 20px; cursor: pointer; flex-shrink: 0; }
        @media (max-width: 992px) { .dashboard { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>M3U Link Üretici</h1>
        <div class="dashboard shell">
            <div>
                <form id="control-form">
                    <button type="submit" id="start-btn" class="btn"><i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span></button>
                </form>
                <h3 style="margin-top:2rem;color:var(--text-secondary);">Canlı Loglar</h3>
                <div id="log-container"></div>
            </div>
            <div>
                <h3 style="margin-bottom:1rem;color:var(--text-secondary);">Geçmiş Linkler</h3>
                <div style="max-height: 550px; overflow-y: auto;">
                    <table class="history-table">
                        <thead><tr><th>Üretim Zamanı</th><th>Son Kullanma</th><th>M3U Linki</th></tr></thead>
                        <tbody id="history-body"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
    <script>
        feather.replace();
        // --- OTOMATİK GÜNCELLEME SORUNUNUN ÇÖZÜMÜ BURADA ---
        // Render'ın proxy'si ile uyumlu çalışması için 'websocket' transport'unu zorluyoruz.
        const socket = io({ transports: ['websocket'] });

        const startBtn = document.getElementById('start-btn');
        const logContainer = document.getElementById('log-container');
        const historyBody = document.getElementById('history-body');

        function renderHistoryRow(item) {
            // Butonun içindeki feather iconunu da içeren tam HTML'i oluştur
            const copyButtonHTML = `<button class="btn-copy" onclick="copyLink(this, \`${item.m3u_url}\`)"><i data-feather="copy"></i></button>`;
            
            return `<tr id="history-row-${item.id}">
                <td>${new Date(item.created_at).toLocaleString('tr-TR')}</td>
                <td>${item.expiry_date}</td>
                <td class="m3u-cell">
                    <div class="m3u-link">${item.m3u_url}</div>
                    ${copyButtonHTML}
                </td>
            </tr>`;
        }

        async function fetchHistory() { 
            try { 
                const res = await fetch('/get_history?t=' + new Date().getTime());
                const historyData = await res.json();
                historyBody.innerHTML = ''; 
                historyData.forEach(item => { 
                    historyBody.innerHTML += renderHistoryRow(item);
                });
                feather.replace(); // Tüm ikonları yeniden çiz
            } catch (e) { console.error(e); } 
        }

        function copyLink(button, textToCopy) {
            navigator.clipboard.writeText(textToCopy).then(() => {
                const originalIcon = button.innerHTML;
                button.innerHTML = '<i data-feather="check"></i>';
                feather.replace();
                setTimeout(() => {
                    button.innerHTML = originalIcon;
                    feather.replace();
                }, 1500);
            });
        }

        document.getElementById('control-form').addEventListener('submit', (e) => {
            e.preventDefault();
            startBtn.disabled = true;
            startBtn.innerHTML = '<i data-feather="loader" class="spinner"></i><span>İşlem Yürütülüyor...</span>';
            feather.replace();
            logContainer.innerHTML = '';
            // Artık target_group göndermiyoruz
            socket.emit('start_process', {});
        });
        
        socket.on('process_complete', (data) => {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span>';
            if (data.new_link) {
                // Yeni satırı listenin başına ekle
                historyBody.insertAdjacentHTML('afterbegin', renderHistoryRow(data.new_link));
            }
            feather.replace(); // Yeni eklenen ikonları da çiz
        });

        socket.on('status_update', (data) => {
            logContainer.innerHTML += `<div>${data.message.replace(/</g, "&lt;")}</div>`;
            logContainer.scrollTop = logContainer.scrollHeight;
        });

        socket.on('process_error', (data) => {
            logContainer.innerHTML += `<div style="color: var(--error-color);">HATA: ${data.error.replace(/</g, "&lt;")}</div>`;
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="alert-triangle"></i><span>Tekrar Dene</span>';
            feather.replace();
        });
        
        document.addEventListener('DOMContentLoaded', fetchHistory);
    </script>
</body>
</html>
"""

# --- Flask Rotaları (Sadeleştirildi) ---
@app.route('/')
def index(): return render_template_string(HOME_TEMPLATE)

@app.route('/get_history')
def get_history():
    links = GeneratedLink.query.order_by(desc(GeneratedLink.id)).limit(20).all()
    return jsonify([link.to_dict() for link in links])

# --- SocketIO Olayları ---
@socketio.on('start_process')
def handle_start_process(data):
    sid = request.sid
    # target_group artık kullanılmıyor
    target_group = data.get('target_group', 'DEFAULT') # Varsayılan bir değer atayabiliriz
    def background_task_wrapper(sid, target_group):
        with app.app_context():
            result = process_bot_run(target_group, sid)
        if "error" in result: socketio.emit('process_error', {'error': result['error']}, to=sid)
        else: socketio.emit('process_complete', {'new_link': result['new_link']}, to=sid)
    socketio.start_background_task(background_task_wrapper, sid, target_group)

# --- Uygulama Başlatma ---
with app.app_context():
    load_config()
    db.create_all()
    
    scheduler_config = config.get('scheduler', {})
    if scheduler_config.get('enabled'):
        scheduler.init_app(app)
        scheduler.start()
        if not scheduler.get_job('scheduled_bot_task'):
             scheduler.add_job(id='scheduled_bot_task', func=scheduled_task, trigger='cron', hour=scheduler_config.get('hour', 4), minute=scheduler_config.get('minute', 0))
             print(f"Zamanlanmış görev kuruldu: Her gün saat {scheduler_config.get('hour', 4):02d}:{scheduler_config.get('minute', 0):02d}")
