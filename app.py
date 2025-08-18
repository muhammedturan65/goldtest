import os
import sys
import smtplib
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for, flash
from flask_socketio import SocketIO
from flask_apscheduler import APScheduler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from gold_club_bot import GoldClubBot
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc
import requests # Bu satır zaten vardı

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

# --- Veritabanı Modeli ---
class GeneratedLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    m3u_url = db.Column(db.Text, nullable=False)
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
    global config
    print("Yapılandırma ortam değişkenlerinden yükleniyor...")
    config['app_password'] = os.environ.get('APP_PASSWORD')
    if not config['app_password']:
        print("KRİTİK HATA: 'APP_PASSWORD' ortam değişkeni ayarlanmamış.")
        sys.exit(1)
    config['email'] = os.environ.get('GCB_EMAIL')
    config['password'] = os.environ.get('GCB_PASSWORD')
    if not config['email'] or not config['password']:
        print("KRİTİK HATA: 'GCB_EMAIL' ve 'GCB_PASSWORD' ortam değişkenleri ayarlanmamış.")
        sys.exit(1)
    config['scheduler'] = {"enabled": os.environ.get('SCHEDULER_ENABLED', 'false').lower() == 'true', "hour": int(os.environ.get('SCHEDULER_HOUR', 4)), "minute": int(os.environ.get('SCHEDULER_MINUTE', 0))}
    config['notification'] = {"enabled": os.environ.get('NOTIF_ENABLED', 'false').lower() == 'true', "smtp_server": os.environ.get('SMTP_SERVER'), "smtp_port": int(os.environ.get('SMTP_PORT', 587)), "sender_email": os.environ.get('SENDER_EMAIL'), "sender_password": os.environ.get('SENDER_PASSWORD'), "receiver_email": os.environ.get('RECEIVER_EMAIL')}
    print("Yapılandırma başarıyla yüklendi.")

# --- E-posta Fonksiyonu ---
def send_email_notification(subject, body):
    notif_config = config.get('notification', {})
    if not notif_config.get('enabled') or not notif_config.get('sender_email'): return
    try:
        msg = MIMEMultipart(); msg['From'] = notif_config['sender_email']; msg['To'] = notif_config['receiver_email']; msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        server = smtplib.SMTP(notif_config['smtp_server'], notif_config['smtp_port']); server.starttls(); server.login(notif_config['sender_email'], notif_config['sender_password']); server.send_message(msg); server.quit()
        print(f"Bildirim e-postası başarıyla gönderildi: '{subject}'")
    except Exception as e: print(f"E-posta gönderilemedi: {e}")

# --- BOT İŞLEMCİ FONKSİYONU ---
def process_bot_run(sid=None):
    result_data = GoldClubBot(email=config['email'], password=config['password'], socketio=socketio, sid=sid).run_full_process()
    if "error" in result_data or not result_data.get('url'):
        error_message = result_data.get('error', 'Bilinmeyen bir hata oluştu veya link alınamadı.')
        send_email_notification("Link Oluşturma Başarısız Oldu", f"Hata: {error_message}")
        return {"error": error_message}
    try:
        expiry_dt = datetime.strptime(result_data['expiry'], "%A, %B %d, %Y")
        formatted_expiry_date = expiry_dt.strftime("%d.%m.%Y")
    except ValueError:
        formatted_expiry_date = result_data['expiry']
    new_link = GeneratedLink( m3u_url=result_data['url'], expiry_date=formatted_expiry_date )
    db.session.add(new_link)
    db.session.commit()
    new_link_data = new_link.to_dict()
    subject = "Yeni M3U Linki Oluşturuldu"
    body = f"<p>Yeni bir M3U linki başarıyla oluşturuldu.</p><ul><li><b>Link:</b> {result_data['url']}</li><li><b>Son Kullanma:</b> {formatted_expiry_date}</li></ul>"
    send_email_notification(subject, body)
    return {"new_link": new_link_data}

# --- ZAMANLANMIŞ GÖREVLER ---
def scheduled_task():
    print("Zamanlanmış link üretme görevi başlatılıyor...")
    with app.app_context(): process_bot_run()
    print("Zamanlanmış link üretme görevi tamamlandı.")

def cleanup_expired_links():
    print("Süresi dolmuş linkler için temizlik görevi başlatılıyor...")
    with app.app_context():
        try:
            now = datetime.now()
            expired_links = GeneratedLink.query.all()
            deleted_count = 0
            for link in expired_links:
                try:
                    expiry_dt = datetime.strptime(link.expiry_date, "%d.%m.%Y")
                    if expiry_dt < now:
                        db.session.delete(link); deleted_count += 1
                except ValueError: print(f"ID #{link.id} için tarih formatı anlaşılamadı: {link.expiry_date}")
            if deleted_count > 0:
                db.session.commit()
                print(f"{deleted_count} adet süresi dolmuş link veritabanından silindi.")
            else: print("Silinecek süresi dolmuş link bulunamadı.")
        except Exception as e: print(f"Temizlik görevi sırasında hata oluştu: {e}")

# --- HTML TEMPLATE'LER ---
# LOGIN_TEMPLATE VE HOME_TEMPLATE burada... (Değişiklik olmadığı için yer kaplamaması adına kodu kısalttım, sizde tam hali kalmalı)

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
        input[type="password"] { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        input[type="password"]:focus { border-color: #E94057; outline: none; }
        .btn { width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; }
        .flash-error { background: rgba(244, 67, 54, 0.2); border: 1px solid var(--error-color); color: var(--error-color); padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; text-align: center; }
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
                <label for="password">Şifre</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn">Giriş Yap</button>
        </form>
    </div>
</body>
</html>
"""

HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>M3U Link Üretici & Filtreleyici</title>
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
        .main-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; align-items: flex-start; }
        .shell { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; padding: 1.5rem; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
        h1 { text-align: center; margin-bottom: 1rem; font-weight: 800; }
        h2 { text-align: center; margin-bottom: 2rem; color: var(--text-secondary); font-weight: 500;}
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 0.75rem; width: 100%; padding: 0.9rem; background: var(--accent-grad); color: white; border: none; border-radius: 8px; font-size: 1.1rem; cursor: pointer; font-weight: 700; margin-top: 1.5rem; text-decoration: none; }
        .btn-logout { background: var(--error-color); margin-top: 1rem; }
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
        .log-line.error { color: var(--error-color); font-weight: bold; }
        .form-group { margin-bottom: 1.5rem; }
        label { display: block; margin-bottom: 0.5rem; color: var(--text-secondary); }
        input[type="url"], input[type="text"] { width: 100%; padding: 0.8rem 1rem; background-color: rgba(0,0,0,0.2); border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-primary); font-size: 1rem; }
        #filter_sonuc_alani { margin-top: 1rem; background-color: rgba(0,0,0,0.3); padding: 1rem; border-radius: 8px; min-height: 200px; overflow-y: auto; }
        @media (max-width: 1200px) { .main-grid { grid-template-columns: 1fr; } }
        @media (max-width: 992px) { 
            .history-table thead { display: none; }
            .history-table tr { display: block; border-bottom: 2px solid var(--accent-grad); margin-bottom: 1.5rem; border-radius: 8px; background: rgba(0,0,0,0.2); }
            .history-table td { display: block; text-align: right; border-bottom: 1px dotted rgba(255,255,255,0.1); padding: 0.75rem; }
            .history-table td::before { content: attr(data-label); float: left; font-weight: bold; }
            .m3u-cell { flex-direction: column; align-items: flex-start; gap: 0.5rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>M3U Link Aracı</h1>
        <div class="main-grid">
            <div class="shell">
                <h2>Link Üretici</h2>
                <form id="control-form"><button type="submit" id="start-btn" class="btn"><i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span></button></form>
                <a href="/logout" class="btn btn-logout">Çıkış Yap</a>
                <h3 style="margin-top:2rem;color:var(--text-secondary);">Canlı Loglar</h3>
                <div id="log-container"></div>
            </div>
            <div class="shell">
                <h2>M3U Kanal Filtreleme</h2>
                <div class="form-group">
                    <label for="filter_m3u_link">Filtrelenecek M3U Linki:</label>
                    <input type="url" id="filter_m3u_link" placeholder="Yeni link üretildiğinde burası otomatik dolacak...">
                </div>
                <div class="form-group">
                    <label for="filter_grup_adi">Filtrelenecek Grup Adı (Tam Adını Yazın):</label>
                    <input type="text" id="filter_grup_adi" placeholder="Örn: HABER, SPOR, ULUSAL">
                </div>
                <button id="filter_btn" class="btn"><i data-feather="filter"></i><span>Kanalları Listele</span></button>
                <div id="filter_sonuc_alani">Filtreleme sonuçları burada görünecek...</div>
            </div>
        </div>
        <div class="shell" style="margin-top: 2rem;">
             <h3 style="margin-bottom:1rem;color:var(--text-secondary);">Geçmiş Linkler</h3>
             <div style="max-height: 550px; overflow-y: auto;">
                 <table class="history-table">
                     <thead><tr><th>Üretim Zamanı</th><th>Son Kullanma</th><th>M3U Linki</th></tr></thead>
                     <tbody id="history-body"></tbody>
                 </table>
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
        const filterBtn = document.getElementById('filter_btn');
        const filterM3uLinkInput = document.getElementById('filter_m3u_link');
        const filterGrupAdiInput = document.getElementById('filter_grup_adi');
        const filterSonucAlani = document.getElementById('filter_sonuc_alani');

        function renderHistoryRow(item) {
            const creationDateUTC = new Date(item.created_at);
            creationDateUTC.setHours(creationDateUTC.getHours() + 3);
            const localCreationTime = creationDateUTC.toLocaleString('tr-TR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
            const expiryParts = item.expiry_date.split('.');
            const expiryDate = new Date(`${expiryParts[2]}-${expiryParts[1]}-${expiryParts[0]}`);
            const now = new Date();
            const oneDay = 24 * 60 * 60 * 1000;
            let rowClass = (expiryDate < now) ? 'expired' : ((expiryDate - now) < oneDay ? 'expiring' : '');
            return `<tr id="history-row-${item.id}" class="${rowClass}">
                <td data-label="Üretim">${localCreationTime}</td>
                <td data-label="Son Kullanma">${item.expiry_date}</td>
                <td data-label="M3U Linki" class="m3u-cell">
                    <div class="m3u-link">${item.m3u_url}</div>
                    <button class="btn-copy" onclick="copyLink(this, \`${item.m3u_url}\`)"><i data-feather="copy"></i></button>
                </td></tr>`;
        }
        async function fetchHistory() { 
            try { 
                const res = await fetch('/get_history?t=' + new Date().getTime());
                const historyData = await res.json();
                historyBody.innerHTML = historyData.map(renderHistoryRow).join('');
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
        
        filterBtn.addEventListener('click', async () => {
            const m3uLink = filterM3uLinkInput.value;
            const grupAdi = filterGrupAdiInput.value;
            if (!m3uLink || !grupAdi) {
                Toastify({ text: "Lütfen M3U linkini ve grup adını girin!", duration: 3000, gravity: "bottom", position: "right", style: { background: "var(--error-color)" } }).showToast();
                return;
            }
            filterBtn.disabled = true;
            filterBtn.innerHTML = '<i data-feather="loader" class="spinner"></i><span>Filtreleniyor...</span>';
            feather.replace();
            filterSonucAlani.innerHTML = '<p>Lütfen bekleyin...</p>';
            
            const proxyUrl = '/ayristir_proxy';
            
            try {
                const formData = new FormData();
                formData.append('m3u_url', m3uLink);
                formData.append('grup_adi', grupAdi);

                const response = await fetch(proxyUrl, {
                    method: 'POST',
                    body: formData
                });
                
                const resultHtml = await response.text();

                if (!response.ok) {
                    filterSonucAlani.innerHTML = `<p style="color:var(--error-color);">${resultHtml}</p>`;
                } else {
                    filterSonucAlani.innerHTML = resultHtml;
                }

            } catch (error) {
                console.error('Filtreleme hatası:', error);
                filterSonucAlani.innerHTML = `<p style="color:var(--error-color);">Proxy hatası: ${error}. Sunucuya erişilemiyor olabilir.</p>`;
            } finally {
                filterBtn.disabled = false;
                filterBtn.innerHTML = '<i data-feather="filter"></i><span>Kanalları Listele</span>';
                feather.replace();
            }
        });

        socket.on('process_complete', (data) => {
            startBtn.disabled = false;
            startBtn.innerHTML = '<i data-feather="play-circle"></i><span>Yeni M3U Linki Üret</span>';
            if (data.new_link) {
                historyBody.insertAdjacentHTML('afterbegin', renderHistoryRow(data.new_link));
                filterM3uLinkInput.value = data.new_link.m3u_url;
            }
            feather.replace();
            Toastify({ text: "Yeni link başarıyla üretildi!", duration: 4000, gravity: "bottom", position: "right", style: { background: "var(--accent-grad)" } }).showToast();
        });
        socket.on('status_update', (data) => {
            logContainer.innerHTML += `<div class="log-line ${data.level || 'info'}">${data.message.replace(/</g, "&lt;")}</div>`;
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
    if 'logged_in' not in session: return redirect(url_for('login'))
    return render_template_string(HOME_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == config.get('app_password'):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Hatalı şifre. Lütfen tekrar deneyin.')
            return redirect(url_for('login'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/get_history')
def get_history():
    if 'logged_in' not in session: return jsonify({"error": "Unauthorized"}), 401
    links = GeneratedLink.query.order_by(desc(GeneratedLink.id)).limit(20).all()
    return jsonify([link.to_dict() for link in links])


# ===================================================================================
# <<<--- GÜNCELLENMİŞ PROXY FONKSİYONU BAŞLANGICI ---<<<
# ===================================================================================
@app.route('/ayristir_proxy', methods=['POST'])
def ayristir_proxy():
    if 'logged_in' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    m3u_url = request.form.get('m3u_url')
    grup_adi = request.form.get('grup_adi')

    if not m3u_url or not grup_adi:
        return "Hata: Eksik parametre.", 400

    # Hedef PHP sunucusunun adresi
    php_server_url = 'https://goldmatch.rf.gd/ayristir.php'
    payload = {
        'm3u_url': m3u_url,
        'grup_adi': grup_adi
    }

    # <<<--- DEĞİŞİKLİK BURADA: KENDİMİZİ TARAYICI GİBİ GÖSTEREN BAŞLIKLAR ---<<<
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }

    try:
        # Python sunucusu, tarayıcı adına ve TARAYICI KİMLİĞİYLE PHP sunucusuna isteği yapıyor
        response = requests.post(php_server_url, data=payload, headers=headers, timeout=30)
        
        # <<<--- DEĞİŞİKLİK BURADA: GELEN CEVABI KONTROL ETME ---<<<
        # Eğer rf.gd yine de bir hata sayfası (genellikle Cloudflare/güvenlik sayfası) dönerse,
        # bu sayfanın içeriğinde genellikle "browser" veya "security" kelimeleri geçer.
        if response.status_code == 200 and ("browser check" in response.text.lower() or "security check" in response.text.lower()):
             return f"Proxy hatası: PHP sunucusu isteği bir güvenlik kontrolü ile engelledi. Ücretsiz hosting sağlayıcısı (rf.gd) script erişimine izin vermiyor.", 503

        # PHP'den gelen cevabı (HTML) ve durum kodunu doğrudan tarayıcıya geri döndür
        return response.text, response.status_code
        
    except requests.exceptions.Timeout:
        return f"Proxy hatası: PHP sunucusu ({php_server_url}) zaman aşımına uğradı.", 504
    except requests.exceptions.RequestException as e:
        return f"Proxy hatası: PHP sunucusuna ulaşılamadı. Hata: {e}", 502
# ===================================================================================
# <<<--- GÜNCELLENMİŞ PROXY FONKSİYONU SONU ---<<<
# ===================================================================================


# --- SocketIO Olayları ---
@socketio.on('start_process')
def handle_start_process(data):
    sid = request.sid
    def background_task_wrapper(sid):
        with app.app_context():
            result = process_bot_run(sid)
        if "error" in result: socketio.emit('process_error', {'error': result['error']}, to=sid)
        else: socketio.emit('process_complete', {'new_link': result['new_link']}, to=sid)
    socketio.start_background_task(background_task_wrapper, sid)

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
