# Dockerfile (Tüm Bağımlılıklar Eklenmiş Final Versiyon)

# Resmi Python 3.10 slim imajını temel alıyoruz.
FROM python:3.10-slim

# Çalışma dizinini /app olarak ayarlıyoruz.
WORKDIR /app

# --- Google Chrome ve Bağımlılıklarının Kurulumu ---
# Bu, sorunu çözen en önemli adımdır.
# Chrome'u kurmadan önce, onun ihtiyaç duyduğu tüm sistem kütüphanelerini kuruyoruz.
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libgconf-2-4 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0 \
    libnss3 \
    libxss1 \
    libasound2 \
    libxtst6 \
    libx11-xcb1 \
    libdbus-glib-1-2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libatk1.0-0 \
    --no-install-recommends \
    # Şimdi, bağımlılıklar hazır olduğuna göre Chrome'u indirip kurabiliriz.
    && wget -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/google-chrome.deb \
    # Kurulumdan sonra gereksiz dosyaları silerek imaj boyutunu küçültüyoruz.
    && rm /tmp/google-chrome.deb \
    && rm -rf /var/lib/apt/lists/*


# requirements.txt dosyasını imajın içine kopyalıyoruz.
COPY requirements.txt .

# Python bağımlılıklarını kuruyoruz.
RUN pip install --no-cache-dir -r requirements.txt

# Projemizin geri kalan tüm dosyalarını (.py dosyaları) imajın içine kopyalıyoruz.
COPY . .

# Render'ın verdiği PORT ortam değişkenini Gunicorn'a iletiyoruz.
# Uygulamayı başlatacak olan komut budur.
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:$PORT", "app:app"]
