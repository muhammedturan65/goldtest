# app.py
import os
import sqlite3
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

# --- Ayarlar ve Konfigürasyon ---
# Render.com'un Kalıcı Diski (Persistent Disk) için yol tanımlaması.
# Ortam değişkeni yoksa, yerel geliştirme için mevcut dizini kullanır.
DATA_DIR = os.environ.get('RENDER_DISK_MOUNT_PATH', '.')
DATABASE = os.path.join(DATA_DIR, 'goldclub_data.db')
PLAYLISTS_DIR = os.path.join(DATA_DIR, 'playlists')

# Yapılandırma artık config.json yerine ortam değişkenlerinden okunacak.
config = {}

def load_or_create_config():
    """
    Uygulama yapılandırmasını ortam değişkenlerinden yükler.
    Bu yöntem, hassas verilerin kod içinde tutulmasını engeller.
    """
    global config
    print("Yapılandırma ortam değişkenlerinden yükleniyor...")

    # Bot kimlik bilgileri
    config['email'] = os.environ.get('GCB_EMAIL')
    config['password'] = os.environ.get('GCB_PASSWORD')

    if not config['email'] or not config['password']:
        print("KRİTİK HATA: 'GCB_EMAIL' ve 'GCB_PASSWORD' ortam değişkenleri ayarlanmamış.")
        sys.exit(1)

    # Zamanlayıcı ayarları
    config['scheduler'] = {
        "enabled": os.environ.get('SCHEDULER_ENABLED', 'false').lower() == 'true',
        "hour": int(os.environ.get('SCHEDULER_HOUR', 4)),
        "minute": int(os.environ.get('SCHEDULER_MINUTE', 0)),
        "target_group": os.environ.get('SCHEDULER_TARGET_GROUP', 'TURKISH')
    }

    # E-posta bildirim ayarları
    config['notification'] = {
        "enabled": os.environ.get('NOTIF_ENABLED', 'false').lower() == 'true',
        "smtp_server": os.environ.get('SMTP_SERVER'),
        "smtp_port": int(os.environ.get('SMTP_PORT', 587)),
        "sender_email": os.environ.get('SENDER_EMAIL'),
        "sender_password": os.environ.get('SENDER_PASSWORD'),
        "receiver_email": os.environ.get('RECEIVER_EMAIL')
    }
    print("Yapılandırma başarıyla yüklendi.")

def send_email_notification(subject, body, attachment_content=None, attachment_filename=None):
    notif_config = config.get('notification', {})
    if not notif_config.get('enabled'): return
    try:
        msg = MIMEMultipart(); msg['From'] = notif_config['sender_email']; msg['To'] = notif_config['receiver_email']; msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        if attachment_content and attachment_filename:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment_content.encode('utf-8'))
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{attachment_filename}"')
            msg.attach(part)
            print(f"E-postaya ek ekleniyor: {attachment_filename}")

        server = smtplib.SMTP(notif_config['smtp_server'], notif_config['smtp_port']); server.starttls(); server.login(notif_config['sender_email'], notif_config['sender_password'])
        server.send_message(msg); server.quit()
        print(f"Bildirim e-postası başarıyla gönderildi: '{subject}'")
    except Exception as e: print(f"E-posta gönderilemedi: {e}")

def get_db_connection(): conn = sqlite3.connect(DATABASE, check_same_thread=False); conn.row_factory = sqlite3.Row; return conn

def init_app():
    # Uygulama başladığında veritabanı ve klasörlerin varlığını kontrol et
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        print(f"Veri dizini oluşturuldu: {DATA_DIR}")

    if not os.path.exists(DATABASE):
        print(f"Veritabanı '{DATABASE}' oluşturuluyor...");
        conn = get_db_connection();
        conn.execute("CREATE TABLE generated_links (id INTEGER PRIMARY KEY, m3u_url TEXT NOT NULL, expiry_date TEXT NOT NULL, channel_count INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);");
        conn.commit();
        conn.close()
    if not os.path.exists(PLAYLISTS_DIR):
        os.makedirs(PLAYLISTS_DIR)
        print(f"Playlist dizini oluşturuldu: {PLAYLISTS_DIR}")


# Flask uygulaması ve SocketIO kurulumu
# SECRET_KEY de güvenlik için ortam değişkenlerinden alınır.
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-super-secret-key-for-local-dev')
socketio = SocketIO(app, async_mode='eventlet')
scheduler = APScheduler()


# --- HTML TEMPLATE'LER ---
# Bu bölümlerde herhangi bir değişiklik yapılmasına gerek yoktur.
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Playlist Yönetim Paneli</title>
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
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2); }
        h1 { font-weight: 800; text-align: center; margin-bottom: 2rem; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
        .stat-card { background: rgba(0,0,0,0.2); padding: 1.5rem; border-radius: 12px; border: 1px solid var(--border-color); }
        .stat-card h3 { color: var(--text-secondary); font-size: 1rem; font-weight: 500; margin-bottom: 0.5rem; }
        .stat-card p { font-size: 1.8rem; font-weight: 700; background: var(--accent-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .dashboard { display: grid; grid-template-columns: minmax(300px, 1fr) 2fr; gap: 2rem; align-items: flex-start; margin-top: 1rem; }
        .card-header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.5rem; color: var(--text-secondary); font-size: 1.1rem; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; }
        label { display: block; margin-bottom: 0.5rem; font-weight: 500; color: var(--text-secondary); }
        input[type="text"] { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; transition: all 0.2s; }
        input[type="text"]:focus { border-color: #E94057; box-shadow: 0 0 0 3px #e9405733; outline: none; }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 0.75rem; width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; transition: all 0.2s; font-weight: 700; margin-top: 1.5rem; text-decoration: none; }
        .btn:hover:not(:disabled) { transform: translateY(-3px); box-shadow: 0 4px 20px rgba(233, 64, 87, 0.3); }
        .btn:disabled { background: #333; cursor: not-allowed; }
        .btn .spinner { animation: spin 1s linear infinite; }
        #log-container { margin-top: 1rem; background-color: rgba(0,0,0,0.3); padding: 1rem; border-radius: 8px; height: 350px; overflow-y: auto; font-family: 'Fira Code', monospace; font-size: 0.85rem; }
        .history-table { width: 100%; border-collapse: collapse; }
        .history-table th, .history-table td { padding: 1rem 0.75rem; border-bottom: 1px solid var(--border-color); text-align: left; vertical-align: middle; }
        .history-table th { font-weight: 600; color: var(--text-secondary); }
        .btn-action { background: none; border: 1px solid var(--border-color); color: var(--text-secondary); padding: 0.4rem 1rem; border-radius: 20px; text-decoration: none; font-size: 0.9rem; font-weight: 500; cursor: pointer; transition: all 0.2s; margin-left: 0.5rem; white-space: nowrap; }
        .btn-details { background: var(--success-color); color: white !important; border-color: var(--success-color); }
        .btn-delete { border-color: var(--error-color); color: var(--error-color); }
        .btn-delete:hover { background: var(--error-color); color: white; }
        
        @media (max-width: 992px) {
            .dashboard { grid-template-columns: 1fr; }
        }
        @media (max-width: 480px) {
            .container { margin: 1rem auto; padding: 0 0.5rem; }
            .shell { padding: 1rem; }
            h1 { font-size: 1.5rem; margin-bottom: 1.5rem; }
            .history-table { font-size: 0.9rem; }
            .btn-action { padding: 0.4rem 0.6rem; margin-left: 0.2rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Playlist Yönetim Paneli</h1>
        <div class="stats-grid shell">
            <div class="stat-card"><h3>Toplam Üretim</h3><p id="stat-total">0</p></div>
            <div class="stat-card"><h3>Ort. Kanal Sayısı</h3><p id="stat-avg-channels">0</p></div>
            <div class="stat-card"><h3>Son Başarılı İşlem</h3><p id="stat-last-run" style="font-size: 1.5rem;">-</p></div>
        </div>
        <div class="dashboard shell">
            <div><div class="card-header"><i data-feather="sliders"></i><span>Kontrol Merkezi</span></div><form id="control-form"><label for="target_group">Filtrelenecek Kanal Grubu</label><input type="text" id="target_group" value="TURKISH"><button type="submit" id="start-btn" class="btn"><i data-feather="play-circle"></i><span>Link Üret ve Analiz Et</span></button></form><h3 style="margin-top:2rem;color:var(--text-secondary);">Canlı Loglar</h3><div id="log-container"></div></div>
            <div><div class="card-header"><i data-feather="clock"></i><span>Geçmiş İşlemler</span></div><div style="max-height: 550px; overflow-y: auto;"><table class="history-table"><thead><tr><th>Üretim Zamanı</th><th>Son Kullanma</th><th>Kanal Sayısı</th><th>İşlemler</th></tr></thead><tbody id="history-body"></tbody></table></div></div>
        </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
    <script>
        feather.replace(); const socket = io(); const startBtn = document.getElementById('start-btn'); const logContainer = document.getElementById('log-container'); const historyBody = document.getElementById('history-body');
        let historyData = [];

        function updateStats() {
            if (!historyData || historyData.length === 0) {
                 document.getElementById('stat-total').textContent = 0;
                 document.getElementById('stat-avg-channels').textContent = 0;
                 document.getElementById('stat-last-run').textContent = '-';
                 return;
            }
            document.getElementById('stat-total').textContent = historyData.length;
            const totalChannels = historyData.reduce((sum, item) => sum + item.channel_count, 0);
            document.getElementById('stat-avg-channels').textContent = historyData.length > 0 ? Math.round(totalChannels / historyData.length) : 0;
            document.getElementById('stat-last-run').textContent = new Date(historyData[0].created_at).toLocaleString('tr-TR');
        }

        async function fetchHistory() { 
            try { 
                const res = await fetch('/get_history?t=' + new Date().getTime());
                historyData = await res.json();
                historyBody.innerHTML = ''; 
                historyData.forEach(item => { 
                    historyBody.innerHTML += renderHistoryRow(item);
                });
                updateStats();
            } catch (e) { console.error(e); } 
        }

        function renderHistoryRow(item) {
            return `<tr id="history-row-${item.id}" style="opacity:0; transition: opacity 0.5s;"><td data-label="Üretim Zamanı">${new Date(item.created_at).toLocaleString('tr-TR')}</td><td data-label="Son Kullanma">${item.expiry_date}</td><td data-label="Kanal Sayısı">${item.channel_count}</td>
            <td data-label="İşlemler" style="text-align:right;"><a href="/playlist/${item.id}" class="btn btn-action btn-details" style="width:auto;margin-top:0;">Detaylar</a> <button onclick="deletePlaylist(${item.id})" class="btn-action btn-delete">Sil</button></td></tr>`;
        }
        
        function fadeInRow(rowElement) {
            requestAnimationFrame(() => { rowElement.style.opacity = 1; });
        }

        async function deletePlaylist(id) {
            if (!confirm(`Playlist #${id} silinecek. Emin misiniz?`)) return;
            try {
                const res = await fetch('/delete_playlist/' + id, { method: 'POST' });
                if (res.ok) {
                    const row = document.getElementById(`history-row-${id}`);
                    row.style.opacity = '0';
                    setTimeout(() => {
                        historyData = historyData.filter(item => item.id !== id);
                        row.remove();
                        updateStats();
                    }, 400);
                } else { alert('Silme işlemi başarısız oldu.'); }
            } catch (e) { console.error(e); alert('Bir hata oluştu.'); }
        }

        document.getElementById('control-form').addEventListener('submit', (e) => { e.preventDefault(); startBtn.disabled = true; startBtn.innerHTML = '<i data-feather="loader" class="spinner"></i><span>İşlem Yürütülüyor...</span>'; feather.replace(); logContainer.innerHTML = ''; socket.emit('start_process', { target_group: document.getElementById('target_group').value.trim() }); });
        
        socket.on('process_complete', (data) => {
            startBtn.disabled = false; startBtn.innerHTML = '<i data-feather="play-circle"></i><span>Link Üret ve Analiz Et</span>'; feather.replace();
            if (data.new_link) {
                historyData.unshift(data.new_link);
                if(historyData.length > 20) historyData.pop();
                
                historyBody.insertAdjacentHTML('afterbegin', renderHistoryRow(data.new_link));
                const newRow = document.getElementById(`history-row-${data.new_link.id}`);
                fadeInRow(newRow);

                if (historyBody.children.length > 20) {
                    historyBody.lastElementChild.remove();
                }
                updateStats();
            } else { fetchHistory(); }
        });

        socket.on('status_update', (data) => { logContainer.innerHTML += `<div><span style="color:var(--text-secondary);">${new Date().toLocaleTimeString()}:</span> ${data.message.replace(/</g, "&lt;")}</div>`; logContainer.scrollTop = logContainer.scrollHeight; });
        socket.on('process_error', (data) => { logContainer.innerHTML += `<div style="color: var(--error-color);">HATA: ${data.error.replace(/</g, "&lt;")}</div>`; startBtn.disabled = false; startBtn.innerHTML = '<i data-feather="alert-triangle"></i><span>Tekrar Dene</span>'; feather.replace(); });
        
        document.addEventListener('DOMContentLoaded', () => {
            fetchHistory().then(() => {
                document.querySelectorAll('#history-body tr').forEach(fadeInRow);
            });
        });
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
        body::before { content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at 15% 85%, #8a238722, transparent 30%), radial-gradient(circle at 85% 25%, #f2712122, transparent 40%); z-index: -1; }
        .container { max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
        h1 { display: flex; flex-wrap: wrap; align-items: center; gap: 1rem; font-weight: 800; font-size: 1.8rem; }
        .controls { display: flex; flex-wrap: wrap; gap: 1rem; margin: 2rem 0; align-items: center; }
        .search-wrapper { flex-grow: 1; position: relative; max-width: 450px; }
        #search-box { width: 100%; padding: 0.8rem 1rem; padding-left: 3rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        .search-wrapper i { position: absolute; left: 1rem; top: 50%; transform: translateY(-50%); color: var(--text-secondary); }
        .btn { display: flex; align-items: center; justify-content: center; gap: 0.5rem; padding: 0.8rem 1.5rem; color: white; border: none; border-radius: 8px; cursor: pointer; text-decoration: none; font-weight: 600; white-space: nowrap; }
        .btn-download { background-image: var(--accent-grad); } 
        .btn-back { background-color: #444; }
        .table-container { max-height: 70vh; overflow-y: auto; border-radius: 8px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 0.8rem 1rem; text-align: left; border-bottom: 1px solid var(--border-color); }
        thead th { background-color: rgba(0,0,0,0.3); position: sticky; top: 0; z-index: 10; }
        .actions-cell { position: relative; text-align: right !important; display: flex; justify-content: flex-end; align-items: center; gap: 0.5rem; }
        .btn-actions, .btn-play { background: none; border: 1px solid var(--border-color); color: var(--text-secondary); padding: 0.3rem; border-radius: 5px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        .btn-play { border-color: var(--success-color); color: var(--success-color); }
        .copy-menu { display: none; position: absolute; background-color: #2a2a2a; border: 1px solid var(--border-color); border-radius: 6px; z-index: 100; padding: 0.5rem; right: 1rem; top: 100%; min-width: 180px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);}
        .copy-option { display: flex; align-items: center; gap: 0.5rem; width: 100%; background: none; border: none; color: var(--text-primary); padding: 0.5rem; text-align: left; border-radius: 4px; cursor: pointer;}
        .copy-option:hover { background-image: var(--accent-grad); color: white;}
        
        @media (max-width: 768px) {
            h1 { font-size: 1.5rem; }
            .controls { flex-direction: column; align-items: stretch; }
            .search-wrapper { max-width: 100%; }
            .table-container { border: none; }
            table, thead, tbody, th, td, tr { display: block; }
            thead tr { position: absolute; top: -9999px; left: -9999px; }
            tr { border: 1px solid var(--border-color); border-radius: 8px; margin-bottom: 1rem; padding: 1rem; }
            td { border: none; position: relative; padding-left: 40%; min-height: 24px; display: flex; align-items: center; }
            td:not(:last-child) { border-bottom: 1px solid rgba(255,255,255,0.05); }
            td:before { position: absolute; left: 0; width: 35%; padding-right: 10px; white-space: nowrap; content: attr(data-label); font-weight: bold; color: var(--text-secondary); }
            .actions-cell { padding-left: 0; justify-content: flex-start; margin-top: 0.5rem; }
            td[data-label="Aksiyonlar"]::before { content: none; }
        }
    </style>
</head>
<body>
    <div class="container">
     <div class="shell">
        <h1><i data-feather="list"></i><span>Playlist Detayları (<span id="channel-count">0</span> Kanal)</span><a href="/" class="btn btn-back" style="margin-left: auto;"><i data-feather="arrow-left"></i><span>Ana Sayfa</span></a></h1>
        <div class="controls">
            <div class="search-wrapper"><i data-feather="search"></i><input type="text" id="search-box" placeholder="Kanal adında veya grupta ara..."></div>
            <button id="download-selected-btn" class="btn btn-download"><i data-feather="download"></i><span>Seçilenleri İndir</span></button>
        </div>
        <div class="table-container">
            <table id="channels-table">
                <thead><tr><th><input type="checkbox" id="select-all"></th><th>Grup</th><th>Kanal Adı</th><th style="text-align: right;">Aksiyonlar</th></tr></thead>
                <tbody>
                    {% for channel in channels %}
                    <tr data-url="{{ channel.url }}" data-name="{{ channel.name }}">
                        <td data-label="Seç"><input type="checkbox" class="channel-checkbox"></td>
                        <td data-label="Grup">{{ channel.group }}</td>
                        <td data-label="Kanal Adı">{{ channel.name }}</td>
                        <td data-label="Aksiyonlar" class="actions-cell">
                            <a href="#" class="btn-play" title="Kanalı Oynat"><i data-feather="play-circle"></i></a>
                            <button class="btn-actions" title="Kopyala"><i data-feather="copy"></i></button>
                            <div class="copy-menu"><button class="copy-option" data-format="ts"><i data-feather="film"></i><span>TS Olarak Kopyala</span></button><button class="copy-option" data-format="m3u8"><i data-feather="list"></i><span>M3U8 Kopyala</span></button><button class="copy-option" data-format="original"><i data-feather="link"></i><span>Orijinal Kopyala</span></button></div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
     </div>
    </div>
    <script>
        feather.replace(); const tableBody = document.querySelector("#channels-table tbody");
        document.getElementById("channel-count").textContent = tableBody.rows.length;

        document.getElementById("search-box").addEventListener("keyup", e => { 
            const q = e.target.value.toLowerCase(); 
            tableBody.querySelectorAll("tr").forEach(r => r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none"); 
        });

        document.getElementById("select-all").addEventListener("change", e => { 
            document.querySelectorAll(".channel-checkbox").forEach(cb => { 
                if(cb.closest('tr').style.display !== 'none') cb.checked = e.target.checked; 
            }); 
        });
        
        function getFormattedUrl(baseUrl, format) {
            if (format === 'original') return baseUrl;
            try {
                const lastSlashIndex = baseUrl.lastIndexOf('/');
                if (lastSlashIndex === -1) return null;
                const pathWithoutStream = baseUrl.substring(0, lastSlashIndex);
                let streamPart = baseUrl.substring(lastSlashIndex + 1);
                const dotIndex = streamPart.lastIndexOf('.');
                const streamId = (dotIndex !== -1) ? streamPart.substring(0, dotIndex) : streamPart;
                return `${pathWithoutStream}/${streamId}.${format}`;
            } catch (err) {
                console.error("URL oluşturma hatası:", err);
                return null;
            }
        }

        document.body.addEventListener('click', e => {
            const actionButton = e.target.closest('.btn-actions');
            const copyButton = e.target.closest('.copy-option');
            const playButton = e.target.closest('.btn-play');

            if (actionButton) {
                const currentMenu = actionButton.nextElementSibling;
                const isVisible = currentMenu.style.display === 'block';
                document.querySelectorAll('.copy-menu').forEach(m => m.style.display = 'none');
                currentMenu.style.display = isVisible ? 'none' : 'block';
                return;
            }

            if (copyButton) {
                const format = copyButton.dataset.format;
                const baseUrl = copyButton.closest('tr').dataset.url;
                const finalUrl = getFormattedUrl(baseUrl, format);
                if (finalUrl) {
                    navigator.clipboard.writeText(finalUrl).then(() => {
                        const originalHtml = copyButton.innerHTML;
                        copyButton.innerHTML = '<i data-feather="check"></i><span>Kopyalandı!</span>';
                        feather.replace();
                        setTimeout(() => { copyButton.innerHTML = originalHtml; feather.replace(); }, 1500);
                    }).catch(err => alert("Kopyalama başarısız oldu."));
                } else { alert("Link formatı kopyalama için uygun değil."); }
            }
            
            if (playButton) {
                e.preventDefault();
                const row = playButton.closest('tr');
                const baseUrl = row.dataset.url;
                const channelName = row.dataset.name;
                const m3u8Url = getFormattedUrl(baseUrl, 'm3u8');
                if (m3u8Url) {
                    const encodedUrl = btoa(m3u8Url);
                    const encodedName = btoa(channelName);
                    const playerUrl = `/play?url=${encodedUrl}&name=${encodedName}`;
                    window.open(playerUrl, '_blank');
                } else { alert("Oynatma linki oluşturulamadı."); }
            }
            
            if (!e.target.closest('.actions-cell')) {
                document.querySelectorAll('.copy-menu').forEach(m => m.style.display = 'none');
            }
        });
        
        document.getElementById("download-selected-btn").addEventListener("click", () => {
            const selected = Array.from(document.querySelectorAll(".channel-checkbox:checked")).map(cb => { const r = cb.closest("tr"); return { group: r.cells[1].textContent, name: r.cells[2].textContent, url: r.dataset.url }; });
            if (selected.length === 0) return alert("Lütfen en az bir kanal seçin.");
            fetch('/generate_custom_playlist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channels: selected }) }).then(res => res.blob()).then(blob => { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `playlist_{{ link_id }}.m3u`; document.body.appendChild(a); a.click(); a.remove(); });
        });
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <script src="https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #101014; --text-primary: #f0f0f0; --text-secondary: #a0a0a0; --border-color: rgba(255, 255, 255, 0.1); }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        body, html { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; background-color: var(--bg-dark); color: var(--text-primary); font-family: 'Manrope', sans-serif; display: flex; flex-direction: column; }
        .player-header { padding: 1rem; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border-color); flex-shrink: 0; }
        .player-header h1 { font-size: 1.2rem; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .back-btn { display: flex; align-items: center; gap: 0.5rem; text-decoration: none; color: var(--text-secondary); background: rgba(255,255,255,0.1); padding: 0.5rem 1rem; border-radius: 6px; transition: background 0.2s; }
        .back-btn:hover { background: rgba(255,255,255,0.2); }
        .player-container { flex-grow: 1; position: relative; display: flex; align-items: center; justify-content: center; }
        #video-player { width: 100%; height: 100%; background: #000; }
        .loading-overlay, .error-overlay { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; background: rgba(0,0,0,0.5); backdrop-filter: blur(5px); }
        .error-overlay { display: none; }
        .spinner { width: 48px; height: 48px; border: 4px solid var(--border-color); border-top-color: var(--text-primary); border-radius: 50%; animation: spin 1s linear infinite; }
        .error-message { text-align: center; padding: 2rem; }
    </style>
</head>
<body>
    <div class="player-header">
        <h1>Yükleniyor...</h1>
        <a href="javascript:window.close();" class="back-btn"><i data-feather="arrow-left"></i><span>Geri Dön</span></a>
    </div>
    <div class="player-container">
        <video id="video-player" controls autoplay></video>
        <div class="loading-overlay" id="loading">
            <div class="spinner"></div>
        </div>
        <div class="error-overlay" id="error">
             <div class="error-message">
                <i data-feather="alert-triangle" style="width: 48px; height: 48px;"></i>
                <h2>Yayın Açılamadı</h2>
                <p>Bu video akışı oynatılamıyor. Lütfen daha sonra tekrar deneyin.</p>
             </div>
        </div>
    </div>
    <script>
        feather.replace();
        const video = document.getElementById('video-player');
        const headerTitle = document.querySelector('.player-header h1');
        const loadingOverlay = document.getElementById('loading');
        const errorOverlay = document.getElementById('error');
        const params = new URLSearchParams(window.location.search);
        
        try {
            const encodedUrl = params.get('url');
            const encodedName = params.get('name');

            if (!encodedUrl) throw new Error("URL bulunamadı.");
            
            const videoSrc = atob(encodedUrl);
            const channelName = encodedName ? atob(encodedName) : "Oynatıcı";
            
            document.title = channelName;
            headerTitle.textContent = channelName;

            if (Hls.isSupported()) {
                const hls = new Hls();
                hls.loadSource(videoSrc);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    loadingOverlay.style.display = 'none';
                    video.play();
                });
                hls.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        loadingOverlay.style.display = 'none';
                        errorOverlay.style.display = 'flex';
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = videoSrc;
                video.addEventListener('loadedmetadata', () => {
                    loadingOverlay.style.display = 'none';
                    video.play();
                });
                 video.addEventListener('error', () => {
                    loadingOverlay.style.display = 'none';
                    errorOverlay.style.display = 'flex';
                });
            } else {
                throw new Error("Tarayıcınız HLS yayınlarını desteklemiyor.");
            }
        } catch (e) {
            loadingOverlay.style.display = 'none';
            errorOverlay.style.display = 'flex';
            errorOverlay.querySelector('p').textContent = e.message;
        }
    </script>
</body>
</html>
"""

# --- BOT İŞLEMCİ FONKSİYONU ---
def process_bot_run(target_group, sid=None):
    result_data = GoldClubBot(email=config['email'], password=config['password'], socketio=socketio, sid=sid, target_group=target_group).run_full_process()
    if "error" in result_data or not result_data.get('channels'):
        error_message = result_data.get('error', 'Bilinmeyen bir hata oluştu veya hiç kanal bulunamadı.')
        subject = "Playlist Oluşturma Başarısız Oldu"
        body = f"<html><body>Playlist oluşturma işlemi sırasında bir hata meydana geldi.<br><br><b>Hata Detayı:</b> {error_message}</body></html>"
        send_email_notification(subject, body)
        return {"error": error_message}
    conn = get_db_connection(); cursor = conn.cursor()
    channel_count = len(result_data['channels'])
    cursor.execute('INSERT INTO generated_links (m3u_url, expiry_date, channel_count) VALUES (?, ?, ?)', (result_data['url'], result_data['expiry'], channel_count))
    new_id = cursor.lastrowid; conn.commit()
    new_link_raw = conn.execute('SELECT * FROM generated_links WHERE id = ?', (new_id,)).fetchone(); conn.close()
    new_link_data = dict(new_link_raw)
    with open(os.path.join(PLAYLISTS_DIR, f"{new_id}.json"), 'w', encoding='utf-8') as f:
        json.dump(result_data['channels'], f, ensure_ascii=False, indent=4)
    filtered_m3u_content = "#EXTM3U\n"
    for ch in result_data['channels']:
        filtered_m3u_content += f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n'
    subject = f"Yeni Playlist Oluşturuldu ({channel_count} Kanal)"
    body = f"""
    <html><body>
    <p>Yeni bir playlist başarıyla oluşturuldu ve e-posta ekinde gönderildi.</p>
    <ul>
        <li><b>Kanal Sayısı:</b> {channel_count}</li>
        <li><b>Son Kullanma:</b> {result_data['expiry']}</li>
        <li><b>Filtre Grubu:</b> {target_group}</li>
    </ul>
    </body></html>
    """
    m3u_filename = f"playlist_{new_id}_{time.strftime('%Y%m%d')}.m3u"
    send_email_notification(subject, body, filtered_m3u_content, m3u_filename)
    return {"new_link": new_link_data}

# --- ZAMANLANMIŞ GÖREV ---
def scheduled_task():
    print("Zamanlanmış görev başlatılıyor...");
    target_group = config.get('scheduler', {}).get('target_group', 'TURKISH')
    process_bot_run(target_group=target_group)
    print("Zamanlanmış görev tamamlandı.")

# --- Flask Yolları ve SocketIO Olayları ---
@app.route('/')
def index(): return render_template_string(HOME_TEMPLATE)

@app.route('/play')
def play_stream():
    encoded_url = request.args.get('url')
    encoded_name = request.args.get('name')
    if not encoded_url:
        return "Hata: Oynatılacak URL belirtilmedi.", 400
    # PLAYER_PAGE_HTML içindeki script'ler parametreleri kendi alıyor.
    return render_template_string(PLAYER_PAGE_HTML)

@app.route('/playlist/<int:link_id>')
def playlist_details(link_id):
    playlist_path = os.path.join(PLAYLISTS_DIR, f"{link_id}.json");
    if not os.path.exists(playlist_path): return "Playlist bulunamadı.", 404
    with open(playlist_path, 'r', encoding='utf-8') as f: channels = json.load(f)
    return render_template_string(PLAYLIST_DETAILS_HTML, channels=channels, link_id=link_id)

@app.route('/get_history')
def get_history():
    conn = get_db_connection(); links = conn.execute('SELECT * FROM generated_links ORDER BY id DESC LIMIT 20').fetchall(); conn.close()
    return jsonify([dict(link) for link in links])

@app.route('/delete_playlist/<int:link_id>', methods=['POST'])
def delete_playlist(link_id):
    try:
        conn = get_db_connection(); conn.execute('DELETE FROM generated_links WHERE id = ?', (link_id,)); conn.commit(); conn.close()
        playlist_path = os.path.join(PLAYLISTS_DIR, f"{link_id}.json")
        if os.path.exists(playlist_path): os.remove(playlist_path)
        return jsonify({"success": True}), 200
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500

@app.route('/generate_custom_playlist', methods=['POST'])
def generate_custom_playlist():
    data = request.json; channels = data.get('channels', []); content = "#EXTM3U\n"
    for ch in channels: content += f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}\n{ch["url"]}\n'
    return Response(content, mimetype="audio/x-mpegurl", headers={"Content-disposition": "attachment; filename=custom_playlist.m3u"})

@socketio.on('start_process')
def handle_start_process(data):
    sid = request.sid
    target_group = data.get('target_group', 'TURKISH')
    def background_task_wrapper(sid, target_group):
        result = process_bot_run(target_group, sid)
        if "error" in result:
            socketio.emit('process_error', {'error': result['error']}, to=sid)
        else:
            socketio.emit('process_complete', {'new_link': result['new_link']}, to=sid)
    socketio.start_background_task(background_task_wrapper, sid, target_group)

# --- Uygulama Başlatma ---
# Bu blok, doğrudan 'python app.py' çalıştırıldığında değil,
# Gunicorn gibi bir WSGI sunucusu tarafından çağrıldığında da çalışacak şekilde ayarlanır.
load_or_create_config()
init_app()
scheduler_config = config.get('scheduler', {})
if scheduler_config.get('enabled'):
    scheduler.init_app(app)
    scheduler.add_job(id='scheduled_bot_task', func=scheduled_task, trigger='cron', hour=scheduler_config.get('hour', 4), minute=scheduler_config.get('minute', 0))
    scheduler.start()
    print(f"Zamanlanmış görev kuruldu: Her gün saat {scheduler_config.get('hour', 4):02d}:{scheduler_config.get('minute', 0):02d}")

# if __name__ == '__main__': bloğu kaldırıldı.
# Gunicorn, 'app' adlı Flask nesnesini doğrudan bulup çalıştıracaktır.
# Yerel geliştirme için, terminalde 'gunicorn --worker-class eventlet -w 1 app:app' komutunu kullanabilirsiniz.
print("Uygulama sunucu tarafından başlatılmaya hazır.")