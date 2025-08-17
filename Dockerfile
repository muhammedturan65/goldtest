# Dockerfile

# Resmi Python 3.10 imajını temel alıyoruz. Bu, içinde Debian olan bir işletim sistemidir.
FROM python:3.10-slim

# Çalışma dizinini /app olarak ayarlıyoruz. Sonraki tüm komutlar bu klasörde çalışacak.
WORKDIR /app

# --- Google Chrome Kurulumu ---
# apt-get'in paket listelerini güncelliyoruz ve Chrome'un ihtiyaç duyduğu paketleri kuruyoruz.
# Bu komutların hepsi tek bir RUN katmanında birleştirilerek Docker imaj boyutu optimize edilir.
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    fontconfig \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# En son kararlı Chrome sürümünü indirip kuruyoruz.
RUN wget -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/google-chrome.deb \
    && rm /tmp/google-chrome.deb

# requirements.txt dosyasını imajın içine kopyalıyoruz.
COPY requirements.txt .

# Python bağımlılıklarını kuruyoruz.
RUN pip install --no-cache-dir -r requirements.txt

# Projemizin geri kalan tüm dosyalarını (.py dosyaları) imajın içine kopyalıyoruz.
COPY . .

# Render'ın verdiği PORT ortam değişkenini Gunicorn'a iletiyoruz.
# Uygulamayı başlatacak olan komut budur.
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:$PORT", "app:app"]