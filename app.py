# app.py (Selenium Kaldırılmış, Sadece Requests Kullanan Final Versiyon)

import os
import sys
import smtplib
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, flash
from flask_socketio import SocketIO
from flask_apscheduler import APScheduler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from gold_club_bot import GoldClubBot

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc
from werkzeug.security import generate_password_hash, check_password_hash

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

# --- Veritabanı Modelleri ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    links = db.relationship('GeneratedLink', backref='owner', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class GeneratedLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    m3u_url = db.Column(db.Text, nullable=False)
    expiry_date = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id, 'm3u_url': self.m3u_url, 'expiry_date': self.expiry_date,
            'created_at': self.created_at.isoformat() if self.created_at else None
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
    
    config['scheduler'] = {"enabled": os.environ.get('SCHEDULER_ENABLED', 'false').lower() == 'true', "hour": int(os.environ.get('SCHEDULER_HOUR', 4)), "minute": int(os.environ.get('SCHEDULER_MINUTE', 0))}
    config['notification'] = {"enabled": os.environ.get('NOTIF_ENABLED', 'false').lower() == 'true', "smtp_server": os.environ.get('SMTP_SERVER'), "smtp_port": int(os.environ.get('SMTP_PORT', 587)), "sender_email": os.environ.get('SENDER_EMAIL'), "sender_password": os.environ.get('SENDER_PASSWORD'), "receiver_email": os.environ.get('RECEIVER_EMAIL')}
    print("Yapılandırma başarıyla yüklendi.")

def send_email_notification(subject, body, user_email=None):
    notif_config = config.get('notification', {})
    receiver = user_email or notif_config.get('receiver_email')
    if not notif_config.get('enabled') or not notif_config.get('sender_email') or not receiver: return
    try:
        msg = MIMEMultipart(); msg['From'] = notif_config['sender_email']; msg['To'] = receiver; msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        server = smtplib.SMTP(notif_config['smtp_server'], notif_config['smtp_port']); server.starttls(); server.login(notif_config['sender_email'], notif_config['sender_password']); server.send_message(msg); server.quit()
        print(f"Bildirim e-postası '{receiver}' adresine başarıyla gönderildi: '{subject}'")
    except Exception as e: print(f"E-posta gönderilemedi: {e}")

# --- BOT İŞLEMCİ FONKSİYONU ---
def process_bot_run(user_id, sid=None):
    user = User.query.get(user_id)
    if not user:
        print(f"Hata: Kullanıcı ID {user_id} bulunamadı.")
        return {"error": "Geçersiz kullanıcı."}

    # Selenium'dan arındırılmış yeni botu çağırıyoruz
    result_data = GoldClubBot(email=config['email'], password=config['password'], socketio=socketio, sid=sid).run_full_process()
    
    if "error" in result_data or not result_data.get('url'):
        error_message = result_data.get('error', 'Bilinmeyen bir hata oluştu veya link alınamadı.')
        send_email_notification("Link Oluşturma Başarısız Oldu", f"Hata: {error_message}", user.username)
        return {"error": error_message}

    new_link = GeneratedLink(
        m3u_url=result_data['url'],
        expiry_date=result_data['expiry'],
        user_id=user_id
    )
    db.session.add(new_link)
    db.session.commit()
    
    new_link_data = new_link.to_dict()
    subject = "Yeni M3U Linki Oluşturuldu"
    body = f"<p>Merhaba {user.username},</p><p>Yeni bir M3U linki başarıyla oluşturuldu.</p><ul><li><b>Link:</b> {result_data['url']}</li><li><b>Son Kullanma:</b> {result_data['expiry']}</li></ul>"
    send_email_notification(subject, body, user.username)
    return {"new_link": new_link_data}

# --- ZAMANLANMIŞ GÖREVLER ---
def scheduled_task():
    print("Zamanlanmış görevler bu çok kullanıcılı yapıda yeniden düşünülmeli. Şimdilik devre dışı.")

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

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Giriş Yap</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #101014; --bg-card: rgba(30, 30, 35, 0.5); --border-color: rgba(255, 255, 255, 0.1); --text-primary: #f0f0f0; --text-secondary: #a0a0a0; --accent-grad: linear-gradient(90deg, #8A2387, #E94057, #F27121); --error-color: #f44336; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Manrope', sans-serif; background: var(--bg-dark); color: var(--text-primary); display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
        .login-box { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 2rem; backdrop-filter: blur(20px); width: 100%; max-width: 400px; }
        h1 { text-align: center; margin-bottom: 1.5rem; font-weight: 800;}
        .form-group { margin-bottom: 1.5rem; }
        label { display: block; margin-bottom: 0.5rem; color: var(--text-secondary); }
        input { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        input:focus { border-color: #E94057; outline: none; }
        .btn { width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; }
        .flash-error { background: rgba(244, 67, 54, 0.2); border: 1px solid var(--error-color); color: var(--error-color); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; text-align: center; }
        .switch-form { text-align: center; margin-top: 1.5rem; color: var(--text-secondary); }
        .switch-form a { color: #E94057; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>Giriş Yap</h1>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-error">{{ messages[0] }}</div>
            {% endif %}
        {% endwith %}
        <form method="post">
            <div class="form-group">
                <label for="username">Kullanıcı Adı (E-posta)</label>
                <input type="email" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Şifre</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn">Giriş Yap</button>
        </form>
        <div class="switch-form">Hesabın yok mu? <a href="/register">Kayıt Ol</a></div>
    </div>
</body>
</html>
"""

REGISTER_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Kayıt Ol</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root { --bg-dark: #101014; --bg-card: rgba(30, 30, 35, 0.5); --border-color: rgba(255, 255, 255, 0.1); --text-primary: #f0f0f0; --text-secondary: #a0a0a0; --accent-grad: linear-gradient(90deg, #8A2387, #E94057, #F27121); --error-color: #f44336; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Manrope', sans-serif; background: var(--bg-dark); color: var(--text-primary); display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 1rem; }
        .login-box { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 2rem; backdrop-filter: blur(20px); width: 100%; max-width: 400px; }
        h1 { text-align: center; margin-bottom: 1.5rem; font-weight: 800;}
        .form-group { margin-bottom: 1.5rem; }
        label { display: block; margin-bottom: 0.5rem; color: var(--text-secondary); }
        input { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        input:focus { border-color: #E94057; outline: none; }
        .btn { width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; }
        .flash-error { background: rgba(244, 67, 54, 0.2); border: 1px solid var(--error-color); color: var(--error-color); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; text-align: center; }
        .switch-form { text-align: center; margin-top: 1.5rem; color: var(--text-secondary); }
        .switch-form a { color: #E94057; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>Hesap Oluştur</h1>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="flash-error">{{ messages[0] }}</div>
            {% endif %}
        {% endwith %}
        <form method="post">
            <div class="form-group">
                <label for="username">Kullanıcı Adı (Geçerli bir e-posta adresi olmalı)</label>
                <input type="email" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Şifre</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn">Kayıt Ol</button>
        </form>
        <div class="switch-form">Zaten hesabın var mı? <a href="/login">Giriş Yap</a></div>
    </div>
</body>
</html>
"""

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
        .header-info { text-align: center; color: var(--text-secondary); margin-bottom: 2rem; font-size: 0.9em; }
        .header-info strong { color: var(--text-primary); }
        .header-info a { color: var(--error-color); text-decoration: none; font-weight: 600; margin-left: 0.5rem; }
        h1 { text-align: center; margin-bottom: 0.5rem; font-weight: 800; }
        .dashboard { display: grid; grid-template-columns: minmax(300px, 1fr) 2.5fr; gap: 2rem; align-items: flex-start; }
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
        tr.expiring td:nth-child(2) { color: var(--warning-color); font-weight: 600; }
        tr.expired td { color: var(--text-secondary); text-decoration: line-through; }
        tr.expired .m3u-link, tr.expired .btn-copy { opacity: 0.5; pointer-events: none; }
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
        <h1>M3U Link Üretici</h1>
        <div class="header-info">
            Giriş yapan kullanıcı: <strong>{{ username }}</strong> | <a href="/logout">Çıkış Yap</a>
        </div>
        <div class="dashboard shell">
            <div>
                <form id="control-form"><button type="submit" id="start-btn" class="btn"><i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span></button></form>
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

            const localCreationTime = new Date(item.created_at).toLocaleString('tr-TR', {
                year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            });

            const copyButtonHTML = `<button class="btn-copy" onclick="copyLink(this, \`${item.m3u_url}\`)"><i data-feather="copy"></i></button>`;
            
            return `<tr id="history-row-${item.id}" class="${rowClass}">
                <td data-label="Üretim">${localCreationTime}</td>
                <td data-label="Son Kullanma">${item.expiry_date}</td>
                <td data-label="M3U Linki" class="m3u-cell">
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
            socket.emit('start_process', {});
        });
        
        socket.on('process_complete', (data) => {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span>';
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

# --- Flask Rotaları ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))
    return render_template_string(HOME_TEMPLATE, username=user.username)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            return redirect(url_for('index'))
        else:
            flash('Hatalı kullanıcı adı veya şifre.')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash('Kullanıcı adı ve şifre alanları zorunludur.')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Bu e-posta adresi zaten kullanımda.')
            return redirect(url_for('register'))
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('Hesabınız başarıyla oluşturuldu! Şimdi giriş yapabilirsiniz.')
        return redirect(url_for('login'))
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    flash('Başarıyla çıkış yaptınız.')
    return redirect(url_for('login'))

@app.route('/get_history')
def get_history():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    links = GeneratedLink.query.filter_by(user_id=session['user_id']).order_by(desc(GeneratedLink.id)).limit(20).all()
    return jsonify([link.to_dict() for link in links])

# --- SocketIO Olayları ---
@socketio.on('start_process')
def handle_start_process(data):
    if 'user_id' not in session:
        return
    user_id = session['user_id']
    sid = request.sid
    def background_task_wrapper(user_id, sid):
        with app.app_context():
            result = process_bot_run(user_id, sid)
        if "error" in result: socketio.emit('process_error', {'error': result['error']}, to=sid)
        else: socketio.emit('process_complete', {'new_link': result['new_link']}, to=sid)
    socketio.start_background_task(background_task_wrapper, user_id, sid)

# --- Uygulama Başlatma ---
with app.app_context():
    load_config()
    db.create_all()
    
    scheduler_config = config.get('scheduler', {})
    if scheduler_config.get('enabled'):
        scheduler.init_app(app)
        scheduler.start()
        # Not: Zamanlanmış link üretme görevi çok kullanıcılı yapıda mantıklı olmadığı için devre dışı bırakıldı.
        
        if not scheduler.get_job('cleanup_task'):
             cleanup_hour = (scheduler_config.get('hour', 4) + 1) % 24 
             scheduler.add_job(id='cleanup_task', func=cleanup_expired_links, trigger='cron', hour=cleanup_hour, minute=scheduler_config.get('minute', 0))
             print(f"Zamanlanmış veritabanı temizlik görevi kuruldu: Her gün saat {cleanup_hour:02d}:{scheduler_config.get('minute', 0):02d}")
