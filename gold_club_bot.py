# gold_club_bot.py (Selenium Kaldırılmış, Sadece Requests Kullanan Final Versiyon)

import requests
import re
import traceback
from bs4 import BeautifulSoup

class GoldClubBot:
    def __init__(self, email, password, socketio=None, sid=None):
        self.email = email
        self.password = password
        self.socketio = socketio
        self.sid = sid
        self.base_url = "https://goldclubhosting.xyz/"
        # Session objesi, çerezleri (cookies) saklayarak giriş yapmış gibi davranmamızı sağlar.
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def _report_status(self, message, level='info'):
        log_message = f"SID {self.sid or 'Scheduler'}: {message}"
        print(log_message)
        if self.socketio and self.sid:
            self.socketio.emit('status_update', {'message': message, 'level': level}, to=self.sid)
            self.socketio.sleep(0)

    def _get_token(self, page_content):
        """Sayfa içeriğinden gizli CSRF token'ını çeker."""
        soup = BeautifulSoup(page_content, 'html.parser')
        token_input = soup.find('input', {'name': 'token'})
        if not token_input:
            raise Exception("CSRF token bulunamadı. Site yapısı değişmiş olabilir.")
        return token_input['value']

    def _login(self):
        self._report_status("-> Giriş sayfasına erişiliyor...")
        login_page_url = f"{self.base_url}index.php?rp=/login"
        try:
            response = self.session.get(login_page_url, timeout=15)
            response.raise_for_status()
            
            token = self._get_token(response.text)
            
            payload = {
                'token': token,
                'username': self.email,
                'password': self.password,
            }
            
            self._report_status("-> Giriş yapılıyor...")
            login_response = self.session.post(login_page_url, data=payload, timeout=15)
            login_response.raise_for_status()

            # Başarılı girişten sonra URL'de "clientarea.php" olmalı
            if "clientarea.php" not in login_response.url:
                raise Exception("Giriş başarısız. Kullanıcı adı veya şifre hatalı olabilir.")
            
            self._report_status("-> Giriş başarılı!", level='info')
            
        except requests.RequestException as e:
            self._report_status(f"Giriş sırasında ağ hatası: {e}", level='error')
            raise

    def _order_free_trial(self):
        self._report_status("-> Ücretsiz deneme sayfasına gidiliyor...")
        order_page_url = f"{self.base_url}index.php?rp=/store/free-trial/order"
        try:
            response = self.session.get(order_page_url, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            # Sipariş için gerekli ürün ID'sini ve diğer form verilerini bul
            pid_input = soup.find('input', {'name': 'pid'})
            if not pid_input:
                raise Exception("Ürün ID'si bulunamadı.")
            
            payload = {
                'pid': pid_input['value'],
                'billingcycle': 'onetime',
                'submit': 'true'
            }
            
            self._report_status("-> Sipariş oluşturuluyor...")
            order_response = self.session.post(order_page_url, data=payload, timeout=15)
            order_response.raise_for_status()

            if "/cart.php?a=confproduct" not in order_response.url:
                raise Exception("Sipariş oluşturma adımı başarısız.")
            
            self._report_status("-> Sipariş oluşturuldu, onay sayfasına geçiliyor...")
            
            # Onay sayfasından son token'ı al
            token = self._get_token(order_response.text)

            checkout_payload = {
                'token': token,
                'accepttos': 'on', # Şartları kabul et
                'notes': '',
                'paymentmethod': '', # Ücretsiz olduğu için boş
                'checkout': 'true'
            }
            
            self._report_status("-> Sipariş tamamlanıyor...")
            final_response = self.session.post(f"{self.base_url}cart.php?a=checkout", data=checkout_payload, timeout=15)
            final_response.raise_for_status()
            
            if "a=complete" not in final_response.url:
                raise Exception("Sipariş tamamlama başarısız oldu.")
            
            self._report_status("-> Sipariş başarıyla tamamlandı!", level='info')
            return final_response.text

        except requests.RequestException as e:
            self._report_status(f"Sipariş sırasında ağ hatası: {e}", level='error')
            raise

    def _extract_data(self, completion_page_content):
        self._report_status("-> Veriler çekiliyor...")
        soup = BeautifulSoup(completion_page_content, 'html.parser')

        # Sipariş tamamlandıktan sonraki sayfada, ürün detaylarına giden bir link bulunur.
        # Bu linki bularak doğrudan o sayfaya gidiyoruz.
        details_link = soup.find('a', href=re.compile(r'view=products&action=productdetails'))
        if not details_link:
            raise Exception("Ürün detayları linki bulunamadı.")
            
        details_page_url = f"{self.base_url}{details_link['href']}"
        self._report_status("-> Ürün detayları sayfasına erişiliyor...")
        
        response = self.session.get(details_page_url, timeout=15)
        response.raise_for_status()
        
        details_soup = BeautifulSoup(response.text, 'html.parser')
        
        m3u_input = details_soup.find('input', {'id': 'm3ulinks'})
        expiry_div = details_soup.find('div', string=re.compile(r'Expiry Date:'))
        
        if not m3u_input or not expiry_div:
            raise Exception("M3U linki veya son kullanma tarihi sayfada bulunamadı.")
            
        m3u_link = m3u_input['value']
        # 'Expiry Date:' div'inin kardeş elementi olan 'strong' etiketini bul
        expiry_date = expiry_div.find_next_sibling('div').strong.text.strip()
        
        if not (m3u_link and expiry_date):
            raise Exception("M3U linki veya son kullanma tarihi alınamadı.")
        
        self._report_status("-> M3U Linki başarıyla alındı.", level='info')
        return {"url": m3u_link, "expiry": expiry_date}

    def run_full_process(self):
        try:
            self._login()
            completion_page = self._order_free_trial()
            return self._extract_data(completion_page)
        except Exception as e:
            error_message = f"[KRİTİK HATA] {type(e).__name__}: {e}"
            self._report_status(error_message, level='error')
            traceback.print_exc()
            if self.socketio and self.sid:
                self.socketio.emit('process_error', {'error': str(e)}, to=self.sid)
            return {'error': error_message}
