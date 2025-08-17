# app.py (Tüm Geliştirmeleri İçeren Final Versiyon)

import os
import json
import requests
import sys
import smtplib
import time
import base64
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, Response
from flask_socketio import SocketIO
from flask_apscheduler import APScheduler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from gold_club_bot import GoldClubBot

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc

# --- Flask ve Veritabanı Kurulumu ---
app = Flask(__name__)
database_uri = os.environ.get('DATABASE_URL', 'sqlite:///local_dev.db')
if database_uri.startswith("postgres://"):
    database_uri = database_uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-super-secret-key-for-local-dev')

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='eventlet')
scheduler = APScheduler()

# --- Veritabanı Modeli (Geliştirildi) ---
class GeneratedLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    m3u_url = db.Column(db.Text, nullable=False)
    expiry_date = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    channel_count = db.Column(db.Integer)
    channels_json = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'm3u_url': self.m3u_url,
            'expiry_date': self.expiry_date,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'channel_count': self.channel_count
        }

# --- Yapılandırma ve E-posta Fonksiyonları ---
config = {}
def load_config():
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

# --- BOT İŞLEMCİ FONKSİYONU (Güncellendi) ---
def process_bot_run(target_group, sid=None):
    result_data = GoldClubBot(email=config['email'], password=config['password'], socketio=socketio, sid=sid, target_group=target_group).run_full_process()
    if "error" in result_data or not result_data.get('url'):
        error_message = result_data.get('error', 'Bilinmeyen bir hata oluştu veya link alınamadı.')
        send_email_notification("Link Oluşturma Başarısız Oldu", f"Hata: {error_message}")
        return {"error": error_message}

    channel_count = len(result_data['channels']) if result_data.get('channels') else 0
    channels_json_data = json.dumps(result_data['channels'], ensure_ascii=False, indent=4) if result_data.get('channels') else None

    new_link = GeneratedLink(
        m3u_url=result_data['url'],
        expiry_date=result_data['expiry'],
        channel_count=channel_count,
        channels_json=channels_json_data
    )
    db.session.add(new_link)
    db.session.commit()
    
    new_link_data = new_link.to_dict()
    subject = f"Yeni Playlist Oluşturuldu ({channel_count} Kanal)"
    body = f"<p>Yeni bir playlist başarıyla oluşturuldu.</p><ul><li><b>Kanal Sayısı:</b> {channel_count}</li><li><b>Link:</b> {result_data['url']}</li><li><b>Son Kullanma:</b> {result_data['expiry']}</li></ul>"
    send_email_notification(subject, body)
    return {"new_link": new_link_data}

# --- ZAMANLANMIŞ GÖREVLER ---
def scheduled_task():
    print("Zamanlanmış link üretme görevi başlatılıyor...");
    with app.app_context():
        target_group = config.get('scheduler', {}).get('target_group', 'TURKISH')
        process_bot_run(target_group=target_group)
    print("Zamanlanmış link üretme görevi tamamlandı.")

def cleanup_expired_links():
    print("Süresi dolmuş linkler için temizlik görevi başlatılıyor...");
    with app.app_context():
        try:
            now = datetime.now()
            expired_links = GeneratedLink.query.all()
            deleted_count = 0
            for link in expired_links:
                try:
                    expiry_dt = datetime.strptime(link.expiry_date, "%A, %B %d, %Y")
                    if expiry_dt < now:
                        db.session.delete(link)
                        deleted_count += 1
                except ValueError:
                    print(f"ID #{link.id} için tarih formatı anlaşılamadı: {link.expiry_date}")
            if deleted_count > 0:
                db.session.commit()
                print(f"{deleted_count} adet süresi dolmuş link veritabanından silindi.")
            else:
                print("Silinecek süresi dolmuş link bulunamadı.")
        except Exception as e:
            print(f"Temizlik görevi sırasında hata oluştu: {e}")

# --- HTML TEMPLATE'LER ---

HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>M3U Link Üretici</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/toastify-js/src/toastify.min.css">
    <script src="https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { 
            --bg-dark: #101014; --bg-card: rgba(30, 30, 35, 0.5); --border-color: rgba(255, 255, 255, 0.1); 
            --text-primary: #f0f0f0; --text-secondary: #a0a0a0; 
            --accent-grad: linear-gradient(90deg, #8A2387, #E94057, #F27121); 
            --success-color: #1ed760; --error-color: #f44336; --warning-color: #f2c94c;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Manrope', sans-serif; background: var(--bg-dark); color: var(--text-primary); font-size: 15px; overflow-x: hidden; }
        body::before { content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 15% 25%, #8a238744, transparent 30%), radial-gradient(circle at 85% 75%, #f2712133, transparent 40%); z-index: -1; }
        .container { max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
        h1 { text-align: center; margin-bottom: 2rem; font-weight: 800; }
        .dashboard { display: grid; grid-template-columns: minmax(300px, 1fr) 2.5fr; gap: 2rem; align-items: flex-start; }
        label { display: block; margin-bottom: 0.5rem; font-weight: 500; color: var(--text-secondary); }
        input[type="text"] { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; transition: all 0.2s; }
        input[type="text"]:focus { border-color: #E94057; }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 0.75rem; width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; margin-top: 1.5rem; text-decoration: none; }
        .btn:hover:not(:disabled) { transform: translateY(-3px); box-shadow: 0 4px 20px rgba(233, 64, 87, 0.3); }
        .btn:disabled { background: #333; cursor: not-allowed; }
        .btn .spinner { animation: spin 1s linear infinite; }
        #log-container { margin-top: 1rem; background-color: rgba(0,0,0,0.3); padding: 1rem; border-radius: 8px; height: 350px; overflow-y: auto; font-family: 'Fira Code', monospace; font-size: 0.85rem; }
        .history-table { width: 100%; border-collapse: collapse; }
        .history-table th, .history-table td { padding: 1rem 0.75rem; border-bottom: 1px solid var(--border-color); text-align: left; vertical-align: top; }
        .history-table th { font-weight: 600; color: var(--text-secondary); }
        .history-table th:last-child, .history-table td:last-child { text-align: right; }
        .m3u-cell { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
        .m3u-link { word-break: break-all; background: rgba(0,0,0,0.2); padding: 0.5rem; border-radius: 4px; font-family: monospace; flex-grow: 1; }
        .btn-copy { background: none; border: 1px solid var(--border-color); color: var(--text-secondary); padding: 0.4rem 0.8rem; border-radius: 20px; cursor: pointer; flex-shrink: 0; }
        .btn-details { background: var(--success-color); color: white !important; padding: 0.4rem 1rem; border-radius: 20px; text-decoration: none; font-size: 0.9rem; font-weight: 500; white-space: nowrap; }
        tr.expiring td:nth-child(3) { color: var(--warning-color); font-weight: 600; }
        tr.expired td { color: var(--text-secondary); text-decoration: line-through; }
        tr.expired .m3u-link, tr.expired .btn-copy, tr.expired .btn-details { opacity: 0.5; pointer-events: none; }
        .log-line.info { color: var(--text-primary); }
        .log-line.warning { color: var(--warning-color); }
        .log-line.error { color: var(--error-color); font-weight: bold; }
        @media (max-width: 992px) { 
            .dashboard { grid-template-columns: 1fr; } 
            .history-table thead { border: none; clip: rect(0 0 0 0); height: 1px; margin: -1px; overflow: hidden; padding: 0; position: absolute; width: 1px; }
            .history-table tr { display: block; border-bottom: 2px solid var(--accent-grad); margin-bottom: 1.5rem; border-radius: 8px; background: rgba(0,0,0,0.2); }
            tr.expired { border-bottom-color: var(--border-color); }
            .history-table td { display: block; text-align: right; border-bottom: 1px dotted rgba(255,255,255,0.1); padding: 0.75rem; }
            .history-table td:last-child { border-bottom: 0; }
            .history-table td::before { content: attr(data-label); float: left; font-weight: bold; color: var(--text-secondary); text-transform: uppercase; font-size: 0.85em; }
            .m3u-cell { flex-direction: column; align-items: flex-start; gap: 0.5rem; }
            .m3u-link { width: 100%; text-align: left; }
            .btn-copy { align-self: flex-end; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>M3U Link Üretici & Analiz Aracı</h1>
        <div class="dashboard shell">
            <div>
                <form id="control-form">
                    <label for="target_group">Filtrelenecek Kanal Grubu</label>
                    <input type="text" id="target_group" value="TURKISH">
                    <button type="submit" id="start-btn" class="btn"><i data-feather="play-circle"></i><span>Link Üret ve Analiz Et</span></button>
                </form>
                <h3 style="margin-top:2rem;color:var(--text-secondary);">Canlı Loglar</h3>
                <div id="log-container"></div>
            </div>
            <div>
                <h3 style="margin-bottom:1rem;color:var(--text-secondary);">Geçmiş Linkler</h3>
                <div style="max-height: 550px; overflow-y: auto;">
                    <table class="history-table">
                        <thead><tr><th>Üretim Zamanı</th><th>Kanal Sayısı</th><th>Son Kullanma</th><th>M3U Linki</th><th>İşlemler</th></tr></thead>
                        <tbody id="history-body"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
    <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/toastify-js"></script>
    <script>
        feather.replace();
        const socket = io({ transports: ['websocket'] });
        const startBtn = document.getElementById('start-btn');
        const logContainer = document.getElementById('log-container');
        const historyBody = document.getElementById('history-body');

        function renderHistoryRow(item) {
            const expiryDate = new Date(item.expiry_date.replace(/,/, ''));
            const now = new Date();
            const oneDay = 24 * 60 * 60 * 1000;
            let rowClass = '';
            if (expiryDate < now) { rowClass = 'expired'; } 
            else if ((expiryDate - now) < oneDay) { rowClass = 'expiring'; }

            const detailsButton = (item.channel_count !== null && item.channel_count > 0)
                ? `<a href="/playlist/${item.id}" target="_blank" class="btn-details">Detaylar (${item.channel_count})</a>` 
                : '';
            const copyButtonHTML = `<button class="btn-copy" onclick="copyLink(this, \`${item.m3u_url}\`)"><i data-feather="copy"></i></button>`;
            
            return `<tr id="history-row-${item.id}" class="${rowClass}">
                <td data-label="Üretim">${new Date(item.created_at).toLocaleString('tr-TR')}</td>
                <td data-label="Kanallar">${item.channel_count === null ? 'N/A' : item.channel_count}</td>
                <td data-label="Son Kullanma">${item.expiry_date}</td>
                <td data-label="M3U Linki" class="m3u-cell">
                    <div class="m3u-link">${item.m3u_url}</div>
                    ${copyButtonHTML}
                </td>
                <td data-label="İşlemler">${detailsButton}</td>
            </tr>`;
        }

        async function fetchHistory() { 
            try { 
                const res = await fetch('/get_history?t=' + new Date().getTime());
                const historyData = await res.json();
                historyBody.innerHTML = ''; 
                historyData.forEach(item => { historyBody.innerHTML += renderHistoryRow(item); });
                feather.replace();
            } catch (e) { console.error(e); } 
        }

        function copyLink(button, textToCopy) {
            navigator.clipboard.writeText(textToCopy).then(() => {
                Toastify({ text: "Link panoya kopyalandı!", duration: 3000, gravity: "bottom", position: "right", style: { background: "var(--success-color)" } }).showToast();
            });
        }

        document.getElementById('control-form').addEventListener('submit', (e) => {
            e.preventDefault();
            startBtn.disabled = true;
            startBtn.innerHTML = '<i data-feather="loader" class="spinner"></i><span>İşlem Yürütülüyor...</span>';
            feather.replace();
            logContainer.innerHTML = '';
            socket.emit('start_process', { target_group: document.getElementById('target_group').value.trim() });
        });
        
        socket.on('process_complete', (data) => {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="play-circle"></i><span>Link Üret ve Analiz Et</span>';
            if (data.new_link) {
                historyBody.insertAdjacentHTML('afterbegin', renderHistoryRow(data.new_link));
            }
            feather.replace();
            Toastify({ text: "Yeni link başarıyla üretildi!", duration: 4000, gravity: "bottom", position: "right", style: { background: "var(--accent-grad)" } }).showToast();
        });

        socket.on('status_update', (data) => {
            const level = data.level || 'info';
            logContainer.innerHTML += `<div class="log-line ${level}">${data.message.replace(/</g, "&lt;")}</div>`;
            logContainer.scrollTop = logContainer.scrollHeight;
        });

        socket.on('process_error', (data) => {
            logContainer.innerHTML += `<div class="log-line error">HATA: ${data.error.replace(/</g, "&lt;")}</div>`;
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="alert-triangle"></i><span>Tekrar Dene</span>';
            feather.replace();
        });
        
        document.addEventListener('DOMContentLoaded', fetchHistory);
    </script>
</body>
</html>
"""

PLAYLIST_DETAILS_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Playlist Detayları</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #101014; --bg-card: rgba(30, 30, 35, 0.5); --border-color: rgba(255, 255, 255, 0.1); --text-primary: #f0f0f0; --text-secondary: #a0a0a0; --accent-grad: linear-gradient(90deg, #8A2387, #E94057, #F27121); --success-color: #1ed760; }
        body { font-family: 'Manrope', sans-serif; background: var(--bg-dark); color: var(--text-primary); }
        body::before { content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 15% 25%, #8a238744, transparent 30%), radial-gradient(circle at 85% 75%, #f2712133, transparent 40%); z-index: -1; }
        .container { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
        h1 { display: flex; align-items: center; gap: 1rem; }
        .search-wrapper { position: relative; margin: 1.5rem 0; }
        #search-box { width: 100%; padding: 0.8rem 1rem 0.8rem 3rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        .search-wrapper i { position: absolute; left: 1rem; top: 50%; transform: translateY(-50%); color: var(--text-secondary); }
        .channel-table { width: 100%; border-collapse: collapse; }
        .channel-table th, .channel-table td { padding: 0.8rem 1rem; border-bottom: 1px solid var(--border-color); }
        .channel-table th { background: rgba(0,0,0,0.2); text-align: left; }
        .channel-table th:last-child, .channel-table td:last-child { text-align: right; }
        .btn-play { background: none; border: 1px solid var(--success-color); color: var(--success-color); border-radius: 50%; width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.2s; }
        .btn-play:hover { background: var(--success-color); color: var(--bg-dark); }
    </style>
</head>
<body>
    <div class="container">
        <div class="shell">
            <h1><i data-feather="list"></i> Kanal Listesi ({{ channels|length }} Kanal)</h1>
            <div class="search-wrapper">
                <i data-feather="search"></i>
                <input type="text" id="search-box" placeholder="Kanal ara...">
            </div>
            <table class="channel-table">
                <thead><tr><th>Grup</th><th>Kanal Adı</th><th>Oynat</th></tr></thead>
                <tbody id="channels-body">
                {% for channel in channels %}
                    <tr>
                        <td>{{ channel.group }}</td>
                        <td>{{ channel.name }}</td>
                        <td>
                            <button class="btn-play" title="Oynat" onclick="playChannel('{{ channel.url }}', '{{ channel.name }}')"><i data-feather="play"></i></button>
                        </td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js"></script>
    <script>
        feather.replace();

        document.getElementById("search-box").addEventListener("keyup", e => { 
            const query = e.target.value.toLowerCase();
            document.querySelectorAll("#channels-body tr").forEach(row => {
                row.style.display = row.textContent.toLowerCase().includes(query) ? "" : "none";
            });
        });

        function getM3u8Url(baseUrl) {
            try {
                const lastSlash = baseUrl.lastIndexOf('/');
                if (lastSlash === -1) return null;
                const streamPart = baseUrl.substring(lastSlash + 1);
                const streamId = streamPart.split('.')[0];
                return `${baseUrl.substring(0, lastSlash)}/${streamId}.m3u8`;
            } catch (e) { console.error(e); return null; }
        }

        function playChannel(originalUrl, channelName) {
            const m3u8Url = getM3u8Url(originalUrl);
            if (!m3u8Url) {
                alert("Oynatma linki oluşturulamadı.");
                return;
            }
            const encodedUrl = btoa(m3u8Url);
            const encodedName = btoa(channelName);
            window.open(`/play?url=${encodedUrl}&name=${encodedName}`, '_blank');
        }
    </script>
</body>
</html>
"""

PLAYER_PAGE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>Oynatıcı</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; background-color: #000; }
        video { width: 100%; height: 100%; }
        .error-message { color: white; text-align: center; padding-top: 2rem; font-family: sans-serif; }
    </style>
</head>
<body>
    <video id="video-player" controls autoplay></video>
    <script>
        const video = document.getElementById('video-player');
        const params = new URLSearchParams(window.location.search);
        
        try {
            const encodedUrl = params.get('url');
            const encodedName = params.get('name');
            if (!encodedUrl) throw new Error("URL parametresi bulunamadı.");
            
            const videoSrc = atob(encodedUrl);
            const channelName = encodedName ? atob(encodedName) : "Oynatıcı";
            document.title = channelName;

            if (Hls.isSupported()) {
                const hls = new Hls();
                hls.loadSource(videoSrc);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());
                hls.on(Hls.Events.ERROR, function (event, data) {
                    if (data.fatal) {
                        document.body.innerHTML = `<div class="error-message">Hata: Bu yayın oynatılamıyor. <br/> (${data.type}: ${data.details})</div>`;
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = videoSrc;
                video.addEventListener('loadedmetadata', () => video.play());
            } else {
                throw new Error("Tarayıcınız HLS yayınlarını desteklemiyor.");
            }
        } catch (e) {
            document.body.innerHTML = `<div class="error-message">Hata: ${e.message}</div>`;
        }
    </script>
</body>
</html>
"""

# --- Flask Rotaları ---
@app.route('/')
def index(): return render_template_string(HOME_TEMPLATE)

@app.route('/get_history')
def get_history():
    links = GeneratedLink.query.order_by(desc(GeneratedLink.id)).limit(20).all()
    return jsonify([link.to_dict() for link in links])

@app.route('/playlist/<int:link_id>')
def playlist_details(link_id):
    link = GeneratedLink.query.get(link_id)
    if not link or not link.channels_json:
        return "Playlist bulunamadı veya bu link için kanal listesi kaydedilmemiş.", 404
    channels = json.loads(link.channels_json)
    return render_template_string(PLAYLIST_DETAILS_HTML, channels=channels, link_id=link_id)

@app.route('/play')
def play_stream():
    return render_template_string(PLAYER_PAGE_HTML)

# --- SocketIO Olayları ---
@socketio.on('start_process')
def handle_start_process(data):
    sid = request.sid
    target_group = data.get('target_group', 'TURKISH')
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
             print(f"Zamanlanmış link üretme görevi kuruldu: Her gün saat {scheduler_config.get('hour', 4):02d}:{scheduler_config.get('minute', 0):02d}")
        
        if not scheduler.get_job('cleanup_task'):
             cleanup_hour = (scheduler_config.get('hour', 4) + 1) % 24 
             scheduler.add_job(id='cleanup_task', func=cleanup_expired_links, trigger='cron', hour=cleanup_hour, minute=scheduler_config.get('minute', 0))
             print(f"Zamanlanmış veritabanı temizlik görevi kuruldu: Her gün saat {cleanup_hour:02d}:{scheduler_config.get('minute', 0):02d}")
