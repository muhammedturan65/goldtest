# gold_club_bot.py (Zaman Aşımı Düzeltilmiş ve Loglaması İyileştirilmiş Final Versiyon)

import time
import traceback
import re
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

class GoldClubBot:
    def __init__(self, email, password, socketio=None, sid=None, target_group=None):
        self.email = email
        self.password = password
        self.socketio = socketio
        self.sid = sid
        self.target_group = target_group
        self.driver = None
        self.wait = None
        self.base_url = "https://goldclubhosting.xyz/"
    
    def _report_status(self, message, level='info'):
        """Mesajları seviyelerine göre (info, warning, error) raporlar."""
        log_message = f"SID {self.sid or 'Scheduler'}: {message}"
        print(log_message)
        if self.socketio and self.sid:
            self.socketio.emit('status_update', {'message': message, 'level': level}, to=self.sid)
            self.socketio.sleep(0)

    def _find_element_with_retry(self, by, value, retries=3, delay=5):
        for i in range(retries):
            try:
                return self.wait.until(EC.visibility_of_element_located((by, value)))
            except TimeoutException:
                if i < retries - 1:
                    self._report_status(f"-> Element '{value}' bulunamadı. {delay} sn sonra tekrar deneniyor...", level='warning')
                    time.sleep(delay)
                else:
                    raise
    
    def _click_element_with_retry(self, by, value, retries=3, delay=5):
        for i in range(retries):
            try:
                element = self.wait.until(EC.element_to_be_clickable((by, value)))
                element.click()
                return
            except TimeoutException:
                if i < retries - 1:
                    self._report_status(f"-> Tıklanabilir element '{value}' bulunamadı. {delay} sn sonra tekrar deneniyor...", level='warning')
                    time.sleep(delay)
                else:
                    raise
    
    def _parse_playlist(self, m3u_url):
        self._report_status(f"-> M3U içeriği indiriliyor ve '{self.target_group}' grubuna göre filtreleniyor...")
        try:
            # Zaman aşımı süresini 20 saniyeden 60 saniyeye çıkarıyoruz.
            response = requests.get(m3u_url, timeout=60)
            response.raise_for_status()
            content = response.text
            
            # İndirmenin başarılı olduğuna ve ayrıştırmanın başladığına dair yeni bir log ekliyoruz.
            self._report_status(f"-> Playlist içeriği başarıyla indirildi ({len(content) / 1024:.2f} KB). Şimdi kanallar ayrıştırılıyor...")

            channels = [
                {"name": name.strip(), "group": group.strip(), "url": url.strip()} 
                for group, name, url in re.findall(r'#EXTINF:-1.*?group-title="(.*?)".*?,(.*?)\n(https?://.*)', content) 
                if self.target_group.lower() in group.lower()
            ]
            
            self._report_status(f"-> Analiz tamamlandı: {len(channels)} adet '{self.target_group}' kanalı bulundu.")
            if not channels:
                self._report_status(f"[UYARI] '{self.target_group}' grubunda hiç kanal bulunamadı.", level='warning')

            return channels
        except requests.RequestException as e:
            self._report_status(f"[HATA] Playlist içeriği indirilemedi. Sunucu yanıt vermiyor veya zaman aşımına uğradı. Hata: {e}", level='error')
            return None

    def _setup_driver(self):
        self._report_status("-> WebDriver hazırlanıyor (arka plan modu)...")
        try:
            options = webdriver.ChromeOptions()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--log-level=3')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.wait = WebDriverWait(self.driver, 20)
        except WebDriverException as e:
            self._report_status(f"[HATA] WebDriver başlatılamadı: {e.msg}", level='error')
            raise
    
    def _login(self):
        self._report_status("-> Giriş yapılıyor...")
        self.driver.get(f"{self.base_url}index.php?rp=/login")
        self._find_element_with_retry(By.ID, "inputEmail").send_keys(self.email)
        self._find_element_with_retry(By.ID, "inputPassword").send_keys(self.password)
        self._click_element_with_retry(By.ID, "login")
        self.wait.until(EC.url_contains("clientarea.php"))
    
    def _order_free_trial(self):
        self._report_status("-> Ücretsiz deneme sipariş ediliyor...")
        self.driver.get(f"{self.base_url}index.php?rp=/store/free-trial")
        self._click_element_with_retry(By.ID, "product7-order-button")
        self._click_element_with_retry(By.ID, "checkout")
        self._click_element_with_retry(By.XPATH, "//label[contains(., 'I have read and agree to the')]")
        self._click_element_with_retry(By.ID, "btnCompleteOrder")
        self.wait.until(EC.url_contains("cart.php?a=complete"))
    
    def _navigate_to_product_details(self):
        self._report_status("-> Ürün detayları sayfasına gidiliyor...")
        self._click_element_with_retry(By.PARTIAL_LINK_TEXT, "Continue To Client Area")
        view_details_button = self._find_element_with_retry(By.XPATH, "(//button[contains(., 'View Details')])[1]")
        view_details_button.click()
    
    def _extract_data(self):
        self._report_status("-> Temel veriler çekiliyor...")
        m3u_input = self._find_element_with_retry(By.ID, "m3ulinks")
        m3u_link = m3u_input.get_attribute("value")
        expiry_date_element = self._find_element_with_retry(By.XPATH, "//div[contains(., 'Expiry Date:')]/strong")
        expiry_date = expiry_date_element.text.strip()
        
        if not (m3u_link and expiry_date):
            raise Exception("M3U linki veya son kullanma tarihi alınamadı.")
        
        channels = self._parse_playlist(m3u_link)
        
        self._report_status("-> Veri çekme ve ayrıştırma tamamlandı.")
        return {"url": m3u_link, "expiry": expiry_date, "channels": channels}
    
    def _cleanup(self):
        if self.driver:
            self.driver.quit()
            self._report_status("-> Tarayıcı kapatıldı.")
    
    def run_full_process(self):
        try:
            self._setup_driver()
            self._login()
            self._order_free_trial()
            self._navigate_to_product_details()
            return self._extract_data()
        except Exception as e:
            error_message = f"[KRİTİK HATA] {type(e).__name__}: {e}"
            self._report_status(error_message, level='error')
            traceback.print_exc()
            if self.socketio and self.sid:
                self.socketio.emit('process_error', {'error': str(e)}, to=self.sid)
            return {'error': error_message}
        finally:
            self._cleanup()
