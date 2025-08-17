# gold_club_bot.py (Login Hatası Düzeltilmiş Final Versiyon)

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

    def _get_token(self, page_content, form_action=None):
        """Sayfa içeriğinden gizli CSRF token'ını çeker."""
        soup = BeautifulSoup(page_content, 'html.parser')
        
        if form_action:
            form = soup.find('form', {'action': re.compile(form_action)})
            if not form:
                 raise Exception(f"'{form_action}' için form bulunamadı.")
            token_input = form.find('input', {'name': 'token'})
        else:
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
            
            # Formun action'ı boş olduğu için, form verilerini login sayfasının kendisine gönderiyoruz.
            # Token'ı da spesifik bir form aramadan, sayfadaki ilk token olarak alıyoruz.
            token = self._get_token(response.text)
            
            payload = {
                'token': token,
                'username': self.email,
                'password': self.password,
            }
            
            self._report_status("-> Giriş yapılıyor...")
            # Post isteğini 'dologin.php' yerine 'login_page_url'e yapıyoruz.
            login_response = self.session.post(login_page_url, data=payload, timeout=15)
            login_response.raise_for_status()

            if "clientarea.php" not in login_response.url and "login" in login_response.url:
                raise Exception("Giriş başarısız. Kullanıcı adı veya şifre hatalı olabilir.")
            
            self._report_status("-> Giriş başarılı!", level='info')
            
        except requests.RequestException as e:
            self._report_status(f"Giriş sırasında ağ hatası: {e}", level='error')
            raise

    def _order_free_trial(self):
        self._report_status("-> Mağaza sayfasına gidiliyor...")
        client_area_url = f"{self.base_url}clientarea.php"
        response = self.session.get(client_area_url, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        order_link = soup.find('a', href=re.compile(r'cart\.php$'))
        if not order_link:
            raise Exception("Mağaza (Order New Services) linki bulunamadı.")
        
        store_url = f"{self.base_url}{order_link['href']}"
        self._report_status("-> Ürün listesine erişiliyor...")
        response = self.session.get(store_url, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        free_trial_link = soup.find('a', href=re.compile(r'cart\.php\?a=add&pid=\d+'))
        if not free_trial_link:
            raise Exception("Ücretsiz deneme (Free Trial) sipariş linki bulunamadı.")
            
        order_page_url = f"{self.base_url}{free_trial_link['href']}"
        self._report_status("-> Ücretsiz deneme sepet sayfasına gidiliyor...")
        
        response = self.session.get(order_page_url, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        checkout_link = soup.find('a', href=re.compile(r'cart\.php\?a=checkout'))
        if not checkout_link:
             raise Exception("Ödeme (Checkout) linki bulunamadı.")

        checkout_url = f"{self.base_url}{checkout_link['href']}"
        self._report_status("-> Ödeme sayfasına geçiliyor...")
        response = self.session.get(checkout_url, timeout=15)
        response.raise_for_status()
        
        token = self._get_token(response.text)
        final_payload = {
            'token': token,
            'i_agree': 'on',
            'notes': '',
            'paymentmethod': 'stripe', # Genellikle varsayılan bir değer seçmek gerekir.
            'submit': 'true'
        }
        
        self._report_status("-> Sipariş tamamlanıyor...")
        final_response = self.session.post(checkout_url, data=final_payload, timeout=15)
        final_response.raise_for_status()
        
        if "a=complete" not in final_response.url:
            # Hata mesajını daha net göstermek için sayfa içeriğini loglayalım
            soup_error = BeautifulSoup(final_response.text, 'html.parser')
            error_div = soup_error.find('div', class_='alert-danger')
            error_message = error_div.text.strip() if error_div else "Bilinmeyen bir hata oluştu."
            raise Exception(f"Sipariş tamamlama başarısız oldu: {error_message}")
        
        self._report_status("-> Sipariş başarıyla tamamlandı!", level='info')
        return final_response.text

    def _extract_data(self, completion_page_content):
        self._report_status("-> Veriler çekiliyor...")
        soup = BeautifulSoup(completion_page_content, 'html.parser')

        details_link = soup.find('a', href=re.compile(r'clientarea\.php\?action=productdetails'))
        if not details_link:
            raise Exception("Ürün detayları linki bulunamadı.")
            
        details_page_url = f"{self.base_url}{details_link['href']}"
        self._report_status("-> Ürün detayları sayfasına erişiliyor...")
        
        response = self.session.get(details_page_url, timeout=15)
        response.raise_for_status()
        
        details_soup = BeautifulSoup(response.text, 'html.parser')
        
        m3u_input = details_soup.find('input', {'id': 'm3ulinks'})
        
        expiry_date_element = details_soup.find(lambda tag: 'Expiry Date:' in tag.text and tag.name == 'div')
        if not expiry_date_element:
             raise Exception("Son kullanma tarihi bulunamadı.")
        
        expiry_date = expiry_date_element.find_next_sibling('div').strong.text.strip()
        
        if not m3u_input or not expiry_date:
            raise Exception("M3U linki veya son kullanma tarihi sayfada bulunamadı.")
            
        m3u_link = m3u_input['value']
        
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
